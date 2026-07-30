"""
Deploy the Employee Verification agent (v5) to Vertex AI Agent Engine using
Bring-Your-Own-Dockerfile (BYOC) mode, and register it with Gemini Enterprise
at the Agent Engine V2 ingress URL — the URL pattern required for GE to
propagate the end user's OAuth token onto the standard Authorization header
that `main.py`'s TokenExtractorMiddleware reads.

    V2 ingress URL pattern (what gets registered with GE):
        https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/
        projects/{PROJECT}/locations/{LOCATION}/reasoningEngines/{ENGINE_ID}/api/a2a/

    NOTE: this is intentionally different from the legacy
    ".../reasoningEngines/{id}/a2a/v1" URL used by the managed A2aAgent
    template in the v3/v4 branches — that URL does not receive the
    propagated token, and registering it caused agent-card `url`
    construction bugs (a doubled "/v1/v1/message:send" 404) that we hit
    previously.

Usage:
    python scripts/deploy.py                 # first deploy or in-place redeploy
    python scripts/deploy.py --dry-run
    python scripts/deploy.py --undeploy
    python scripts/deploy.py --undeploy --delete-engine
    python scripts/deploy.py --register-only --reasoning-engine <ID_OR_FULL_NAME>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

_SSL_VERIFY: bool | str = True
_ssl_env = os.environ.get("SSL_VERIFY", "").strip().lower()
if _ssl_env in ("false", "0", "no"):
    _SSL_VERIFY = False
elif _ssl_env:
    _SSL_VERIFY = _ssl_env
elif os.environ.get("REQUESTS_CA_BUNDLE"):
    _SSL_VERIFY = os.environ["REQUESTS_CA_BUNDLE"]

if _SSL_VERIFY is False:
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import requests
import vertexai
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.genai import types
from google.genai.errors import ClientError

AGENT_NAME = "employee_verification_v5"
CONFIG_FILE = _PROJECT_ROOT / "scripts" / "deploy_state.json"

# Conservative safety wait after a destructive engine recreate, matching
# GE's eventually-consistent authorization release behavior.
_RECREATE_SAFETY_WAIT_SECONDS = 300


# =============================================================================
# Persisted deploy state (Reasoning Engine resource name across runs)
# =============================================================================

def _load_state() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(state, indent=2) + "\n")


# =============================================================================
# Auth helpers
# =============================================================================

def _bearer_token() -> str | None:
    try:
        creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(GoogleAuthRequest())
        return creds.token
    except Exception as e:
        print(f"  ✗ Could not get credentials: {e}")
        print("    Run: gcloud auth application-default login")
        return None


def _get_project_number(project_id: str) -> str | None:
    token = _bearer_token()
    if not token:
        return None
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, verify=_SSL_VERIFY)
    return resp.json().get("projectNumber") if resp.status_code == 200 else None


# =============================================================================
# Gemini Enterprise REST helpers
# =============================================================================

def _de_hostname(ge_location: str) -> str:
    return "discoveryengine.googleapis.com" if ge_location == "global" \
        else f"{ge_location}-discoveryengine.googleapis.com"


class GEClient:
    def __init__(self, project_id: str, app_id: str, ge_location: str = "global"):
        self.project_id = project_id
        self.app_id = app_id
        self.ge_location = ge_location
        self._host = _de_hostname(ge_location)

    def _agents_url(self, agent_id: str = "") -> str:
        base = (
            f"https://{self._host}/v1alpha/projects/{self.project_id}/"
            f"locations/{self.ge_location}/collections/default_collection/"
            f"engines/{self.app_id}/assistants/default_assistant/agents"
        )
        return f"{base}/{agent_id}" if agent_id else base

    def _headers(self) -> dict | None:
        token = _bearer_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Goog-User-Project": self.project_id,
        }

    def find_agent_by_display_name(self, display_name: str) -> dict | None:
        headers = self._headers()
        if not headers:
            return None
        resp = requests.get(self._agents_url(), headers=headers, params={"pageSize": 1000}, verify=_SSL_VERIFY)
        if resp.status_code != 200:
            return None
        for agent in resp.json().get("agents", []):
            if agent.get("displayName") == display_name:
                return agent
        return None

    _LOCK_RETRY_DELAYS = (120, 180, 300)

    def create_agent(self, payload: dict) -> dict | None:
        headers = self._headers()
        if not headers:
            return None
        attempts = len(self._LOCK_RETRY_DELAYS) + 1
        for attempt in range(1, attempts + 1):
            resp = requests.post(self._agents_url(), headers=headers, json=payload, verify=_SSL_VERIFY)
            if resp.status_code == 200:
                return resp.json()
            if "is used by another agent" in resp.text and attempt <= len(self._LOCK_RETRY_DELAYS):
                delay = self._LOCK_RETRY_DELAYS[attempt - 1]
                print(f"  ⏳ Authorization locked (attempt {attempt}/{attempts}) — waiting {delay // 60}m{delay % 60:02d}s...")
                time.sleep(delay)
                continue
            print(f"  ✗ GE create failed (HTTP {resp.status_code}): {resp.text}")
            return None
        return None

    def patch_agent(self, agent_id: str, payload: dict, update_mask: list[str]) -> dict | None:
        headers = self._headers()
        if not headers:
            return None
        resp = requests.patch(
            self._agents_url(agent_id), headers=headers, json=payload,
            params={"updateMask": ",".join(update_mask)}, verify=_SSL_VERIFY,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"  ✗ GE patch failed (HTTP {resp.status_code}): {resp.text}")
        return None

    def delete_agent(self, agent_id: str) -> bool:
        headers = self._headers()
        if not headers:
            return False
        resp = requests.delete(self._agents_url(agent_id), headers=headers, verify=_SSL_VERIFY)
        return resp.status_code in (200, 204, 404)


# =============================================================================
# Authorization resource
# =============================================================================

def _ensure_auth_resource(auth_id: str, project_id: str, project_number: str) -> str | None:
    from scripts.setup_agent_auth import _check_auth_exists, _create_auth

    existing = _check_auth_exists(project_number, auth_id, project_id)
    if existing:
        return existing.get("name")

    client_id = os.environ.get("OAUTH_CLIENT_ID")
    client_secret = os.environ.get("OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("  ⚠ Missing OAUTH_CLIENT_ID/SECRET — skipping auth resource creation")
        return None

    print(f"  ⏳ Creating auth resource '{auth_id}'...")
    result = _create_auth(project_number, auth_id, project_id, client_id, client_secret)
    if result:
        print(f"  ✓ Created auth resource: {result.get('name')}")
        return result.get("name")
    print(f"  ✗ Could not create auth resource '{auth_id}'")
    return None


# =============================================================================
# GE registration (create / patch in place)
# =============================================================================

def _register_or_update(
    ge: GEClient,
    *,
    display_name: str,
    description: str,
    agent_card_json: str,
    agent_authorization: str | None,
    access_policy: str,
) -> bool:
    payload = {
        "displayName": display_name,
        "description": description,
        "a2aAgentDefinition": {"jsonAgentCard": agent_card_json},
    }
    existing = ge.find_agent_by_display_name(display_name)

    if existing:
        existing_id = existing.get("name", "").split("/")[-1]
        update_mask = ["displayName", "description", "a2aAgentDefinition"]
        existing_auth = (
            existing.get("authorizationConfig", {}).get("agentAuthorization")
            or existing.get("authorization_config", {}).get("agent_authorization")
        )
        if agent_authorization and not existing_auth:
            print(f"  ⚠ Existing GE agent has no authorization attached — attaching '{agent_authorization}' now")
            payload["authorization_config"] = {"agent_authorization": agent_authorization}
            update_mask.append("authorization_config")

        print(f"  ⏳ Updating existing GE registration '{existing_id}' in place...")
        result = ge.patch_agent(existing_id, payload, update_mask=update_mask)
        if not result:
            return False
        print("  ✓ Updated Gemini Enterprise registration (in place — auth resource untouched)")
    else:
        print(f"  ⏳ Registering '{display_name}' in Gemini Enterprise (first-time registration)...")
        if agent_authorization:
            payload["authorization_config"] = {"agent_authorization": agent_authorization}
        result = ge.create_agent(payload)
        if not result:
            return False
        print("  ✓ Registered in Gemini Enterprise")

    result_id = result.get("name", "").split("/")[-1]
    ge.patch_agent(result_id, {"sharingConfig": {"scope": access_policy}}, update_mask=["sharingConfig.scope"])
    print(f"  ✓ Gallery visibility set to '{access_policy}'")
    return True


# =============================================================================
# Agent Engine (BYOC) create / update
# =============================================================================

def _engine_exists(client: "vertexai.Client", engine_resource: str) -> bool:
    try:
        client.agent_engines.get(name=engine_resource)
        return True
    except ClientError as e:
        if e.code == 404:
            return False
        print(f"  ⚠ Could not verify existing engine (HTTP {e.code}): {e}")
        return False
    except Exception as e:
        print(f"  ⚠ Could not verify existing engine: {e}")
        return False


def _normalize_engine_resource(value: str, project_id: str, location: str) -> str:
    if value.startswith("projects/"):
        return value
    return f"projects/{project_id}/locations/{location}/reasoningEngines/{value}"


def _byoc_config(display_name: str, description: str) -> dict:
    """BYOC config: source_packages + image_spec, per Google's Agent Engine
    V2 ingress notebook. entrypoint_module/entrypoint_object are required
    when source_packages is specified; class_methods describes the A2A
    surface Agent Engine should generate proxy methods for.
    """
    return {
        "display_name": display_name,
        "description": description,
        "source_packages": [
            str(_PROJECT_ROOT / "employee_agent"),
            str(_PROJECT_ROOT / "main.py"),
            str(_PROJECT_ROOT / "requirements.txt"),
            str(_PROJECT_ROOT / "Dockerfile"),
        ],
        "image_spec": {},
        "agent_framework": "google-adk",
        "env_vars": {
            "GOOGLE_GENAI_USE_VERTEXAI": "1",
            "PROJECT_ID": os.environ["PROJECT_ID"],
            "GOOGLE_GENAI_MODEL": os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash"),
        },
        "resource_limits": {"cpu": "2", "memory": "4Gi"},
        "max_instances": 3,
    }


def deploy(dry_run: bool = False, force_recreate_engine: bool = False) -> bool:
    project_id = os.environ["PROJECT_ID"]
    location = os.environ.get("LOCATION", "us-central1")
    storage = os.environ.get("STORAGE_BUCKET")
    display_name = "Employee Verification Agent (v5)"
    description = (
        "An HR agent that helps employees review, update, and verify their "
        "employment records, using Gemini Enterprise's built-in OAuth token "
        "propagation (Agent Engine V2 ingress)."
    )

    state = _load_state()
    existing_engine_raw = state.get("reasoning_engine")

    print(f"  ├── Deployment mode: BYOC (source_packages + image_spec)")
    print(f"  ├── Region: {location}")
    print(f"  ├── Display Name: {display_name}")

    if dry_run:
        if existing_engine_raw:
            print(f"  ├── Existing engine on file: {existing_engine_raw}")
            print("  ├── Would attempt: in-place UPDATE (or CREATE if missing)")
        else:
            print("  ├── No existing engine on file — would attempt: CREATE (first-time deploy)")
        print("  └── 🔍 DRY RUN — nothing deployed")
        return True

    vertexai.init(project=project_id, location=location, staging_bucket=storage)
    client = vertexai.Client(project=project_id, location=location)

    engine_config = _byoc_config(display_name, description)
    existing_engine = (
        _normalize_engine_resource(existing_engine_raw, project_id, location)
        if existing_engine_raw else None
    )
    engine_exists = bool(existing_engine) and _engine_exists(client, existing_engine)

    if force_recreate_engine and engine_exists:
        print(f"  ⚠ --force-recreate-engine: deleting existing engine {existing_engine} ...")
        try:
            client.agent_engines.delete(name=existing_engine, force=True)
            print("  ✓ Deleted existing Reasoning Engine")
        except Exception as e:
            print(f"  ✗ Failed to delete existing engine: {e}")
            return False
        engine_exists = False
        print(f"  ⏳ Waiting {_RECREATE_SAFETY_WAIT_SECONDS // 60} minutes (eventual-consistency safety margin)...")
        time.sleep(_RECREATE_SAFETY_WAIT_SECONDS)

    start = time.time()
    if engine_exists:
        print(f"  ⏳ Redeploying in place onto existing engine: {existing_engine}")
        try:
            remote_agent = client.agent_engines.update(name=existing_engine, config=engine_config)
        except Exception as e:
            print(f"  ✗ In-place update failed: {e}")
            return False
        engine_resource = remote_agent.api_resource.name
        print(f"  ✓ Updated in place: {engine_resource} ({time.time() - start:.0f}s)")
    else:
        if existing_engine_raw:
            print(f"  ⚠ Recorded engine '{existing_engine_raw}' no longer exists — creating a new one")
        else:
            print("  ⏳ No existing engine on file — this is a first-time deploy")
        print("  ⏳ Creating new Agent Engine resource (BYOC build, ~5-10 min)...")
        try:
            remote_agent = client.agent_engines.create(config=engine_config)
        except Exception as e:
            print(f"  ✗ Deployment failed: {e}")
            return False
        engine_resource = remote_agent.api_resource.name
        print(f"  ✓ Created: {engine_resource} ({time.time() - start:.0f}s)")
        state["reasoning_engine"] = engine_resource
        _save_state(state)

    return _register(engine_resource, display_name, description)


def register_only(reasoning_engine: str) -> bool:
    location = os.environ.get("LOCATION", "us-central1")
    if not reasoning_engine.startswith("projects/"):
        project_id = os.environ["PROJECT_ID"]
        project_number = _get_project_number(project_id) or project_id
        reasoning_engine = f"projects/{project_number}/locations/{location}/reasoningEngines/{reasoning_engine}"
    print(f"  ⏳ Registering existing engine: {reasoning_engine}")
    return _register(
        reasoning_engine,
        "Employee Verification Agent (v5)",
        "An HR agent that helps employees review, update, and verify their employment records.",
    )


def _register(engine_resource: str, display_name: str, description: str) -> bool:
    """Register with GE at the Agent Engine V2 ingress URL. This is the URL
    pattern (with the `/api/` segment) required for GE to propagate the
    end-user OAuth token — NOT the legacy `/a2a/v1` URL.
    """
    project_id = os.environ["PROJECT_ID"]
    location = os.environ.get("LOCATION", "us-central1")

    # engine_resource looks like:
    #   projects/{PROJECT_NUM}/locations/{LOCATION}/reasoningEngines/{ID}
    engine_id = engine_resource.rstrip("/").split("/")[-1]

    v2_ingress_url = (
        f"https://{location}-aiplatform.googleapis.com/reasoningEngines/v1/"
        f"{engine_resource}/api/a2a/"
    )
    print(f"  ✓ Agent Engine V2 ingress URL: {v2_ingress_url}")

    agent_card = {
        "protocolVersion": "0.3.0",
        "name": display_name,
        "description": description,
        "url": v2_ingress_url,
        "version": "1.0.0",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": "employee-lookup",
                "name": "Employee Lookup",
                "description": "Search and find employee records by name, employee ID, or department.",
                "tags": ["employee", "lookup", "search", "hr"],
                "examples": ["Find employee John Smith", "Look up employee E-1001"],
            },
            {
                "id": "employee-update",
                "name": "Employee Field Update",
                "description": "Update editable employee fields like address, phone, email, and emergency contact.",
                "tags": ["employee", "update", "edit", "hr"],
                "examples": ["Update my address to 123 Main St"],
            },
            {
                "id": "employee-verification",
                "name": "Employee Verification",
                "description": "Verify employee records and mark records as verified.",
                "tags": ["employee", "verification", "verify", "hr"],
                "examples": ["Verify my employment"],
            },
        ],
    }

    ge_location = os.environ.get("GE_LOCATION", "global")
    ge = GEClient(project_id, os.environ["GEMINI_ENTERPRISE_APP_ID"], ge_location)

    auth_id = os.environ.get("AUTH_ID", "auth-emp-verify-v5")
    agent_authorization = None
    project_number = _get_project_number(project_id)
    if project_number:
        agent_authorization = _ensure_auth_resource(auth_id, project_id, project_number)

    return _register_or_update(
        ge,
        display_name=display_name,
        description=description,
        agent_card_json=json.dumps(agent_card),
        agent_authorization=agent_authorization,
        access_policy=os.environ.get("GE_ACCESS_POLICY", "ALL_USERS"),
    )


def undeploy(delete_engine: bool = False) -> bool:
    display_name = "Employee Verification Agent (v5)"
    project_id = os.environ["PROJECT_ID"]
    ge_location = os.environ.get("GE_LOCATION", "global")
    ge = GEClient(project_id, os.environ["GEMINI_ENTERPRISE_APP_ID"], ge_location)

    existing = ge.find_agent_by_display_name(display_name)
    if not existing:
        print(f"  ℹ No GE registration found for '{display_name}' — nothing to unregister")
        ok = True
    else:
        ge_agent_id = existing.get("name", "").split("/")[-1]
        print(f"  ⏳ Unregistering '{display_name}' ({ge_agent_id})...")
        ok = ge.delete_agent(ge_agent_id)
        print("  ✓ Unregistered" if ok else "  ✗ Failed to unregister")

    state = _load_state()
    engine_resource_raw = state.get("reasoning_engine")

    if not delete_engine:
        print("  ℹ To delete the Agent Engine resource:")
        if engine_resource_raw:
            print(f"    python scripts/deploy.py --undeploy --delete-engine")
        return ok

    if not engine_resource_raw:
        print("  ℹ No reasoning_engine recorded — nothing to delete")
        return ok

    location = os.environ.get("LOCATION", "us-central1")
    engine_resource = _normalize_engine_resource(engine_resource_raw, project_id, location)
    print(f"  ⏳ Deleting Agent Engine resource: {engine_resource}")
    try:
        vertexai.init(project=project_id, location=location)
        client = vertexai.Client(project=project_id, location=location)
        client.agent_engines.delete(name=engine_resource, force=True)
        print("  ✓ Deleted Agent Engine resource")
    except ClientError as e:
        if e.code == 404:
            print("  ℹ Engine already deleted / does not exist")
        else:
            print(f"  ✗ Failed to delete engine (HTTP {e.code}): {e}")
            ok = False
    except Exception as e:
        print(f"  ✗ Failed to delete engine: {e}")
        ok = False

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Deploy {AGENT_NAME} to Agent Engine (BYOC) + Gemini Enterprise")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--undeploy", action="store_true")
    parser.add_argument("--delete-engine", action="store_true")
    parser.add_argument("--force-recreate-engine", action="store_true")
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--reasoning-engine")
    args = parser.parse_args()

    print()
    print("=" * 70)
    print(f"  {AGENT_NAME} — {'Undeploy' if args.undeploy else 'Dry Run' if args.dry_run else 'Register' if args.register_only else 'Deploy'}")
    print("=" * 70)
    print()

    if args.undeploy:
        ok = undeploy(delete_engine=args.delete_engine)
    elif args.register_only:
        if not args.reasoning_engine:
            print("✗ --register-only requires --reasoning-engine")
            sys.exit(1)
        ok = register_only(args.reasoning_engine)
    else:
        ok = deploy(dry_run=args.dry_run, force_recreate_engine=args.force_recreate_engine)

    print()
    print("=" * 70)
    print("  ✓ Done" if ok else "  ✗ Failed")
    print("=" * 70)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
