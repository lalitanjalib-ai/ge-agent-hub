"""
Deploy emp_verify_google_oauth_v3 to Vertex AI Agent Engine and register it
in Gemini Enterprise as a pure A2A agent.

Auth-safe deploy model
-----------------------
There are two supported use cases:

1. FIRST-TIME DEPLOY — no Reasoning Engine exists yet.
   - `client.agent_engines.create()` provisions a brand-new Reasoning Engine
     resource (new resource name / ID).
   - The new resource name is written back into
     `config/emp_verify_google_oauth_v3.yaml` (`deploy.reasoning_engine`) so
     every subsequent run knows this engine already exists.
   - The GE authorization resource (`deploy.agent_authorization_id`) is
     created if it doesn't already exist, then attached when the GE agent is
     registered. `create_agent()` retries with a long backoff (2m/3m/5m) if
     GE reports the authorization as locked (`"is used by another agent"`) —
     GE's release of a freed authorization is eventually consistent and can
     take several minutes.

2. REDEPLOY — fixing/updating the agent's code, after a first deploy.
   - The existing Reasoning Engine resource name is read from
     `deploy.reasoning_engine` in the YAML.
   - `client.agent_engines.update(name=..., agent=...)` pushes the new code/
     requirements/env_vars to the *same* resource — the resource name (and
     therefore its A2A URL) never changes.
   - Because the resource name never changes, the GE agent registration is
     simply PATCHed in place (`_register_or_update`) — the authorization
     resource is NEVER detached/reattached on a normal redeploy, so the "GE
     doesn't release the authorization for ~5 minutes" problem never comes
     up in this path.
   - If the recorded engine no longer exists (e.g. manually deleted), deploy.py
     automatically falls back to the first-time-deploy (create) path.

Optional recovery path — `--force-recreate-engine`:
   - Deliberately deletes the current Reasoning Engine and creates a brand
     new one (e.g. for changes `update()` cannot apply, like agent_framework).
   - Because the new engine gets a new resource name, the GE registration
     PATCH will effectively re-point the *same* GE agent record at the new
     A2A URL — the authorization resource itself is untouched (still
     attached to the same GE agent id), so no re-attach/lock wait is
     actually required for the *authorization*. However, since this path
     recreates infrastructure, deploy.py still waits 5 minutes before
     re-registering as a conservative safety margin for eventual consistency
     on the Agent Engine / IAM side.

Usage:
    python scripts/deploy.py                    # first deploy OR in-place redeploy (auto-detected)
    python scripts/deploy.py --dry-run          # preview config only
    python scripts/deploy.py --force-recreate-engine   # delete + recreate the Reasoning Engine
    python scripts/deploy.py --undeploy         # unregister from GE
    python scripts/deploy.py --undeploy --delete-engine  # also delete the Reasoning Engine resource

    # Re-register an already-deployed engine without redeploying
    python scripts/deploy.py --register-only --reasoning-engine <RESOURCE_ID_OR_FULL_NAME>
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PROJECT_ROOT.parent
for _path in (_PROJECT_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import os
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

# Corporate proxies (Zscaler/Netskope): when SSL_VERIFY=false, patch Python SSL
# before google/vertex SDK clients initialize their HTTP stacks.
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
    import httpx as _httpx

    def _no_verify(orig):
        def wrapper(self, *a, **kw):
            kw.setdefault("verify", False)
            orig(self, *a, **kw)
        return wrapper

    _httpx.Client.__init__ = _no_verify(_httpx.Client.__init__)  # type: ignore[method-assign]
    _httpx.AsyncClient.__init__ = _no_verify(_httpx.AsyncClient.__init__)  # type: ignore[method-assign]

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import argparse
import importlib
import json
import re
import time

import httpx
import requests
import vertexai
from a2a.types import AgentSkill
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest

from google.genai import types
from google.genai.errors import ClientError
from vertexai.preview.reasoning_engines import A2aAgent
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card

from agents._base.config_loader import get_agent_config_path, load_agent_config
from agents._base.agent_card import build_a2ui_agent_card

AGENT_NAME = "emp_verify_google_oauth_v3"

# Conservative wait for GE's eventually-consistent authorization release /
# general infra propagation after a destructive recreate. Empirically GE can
# take several minutes to release an authorization after the agent that used
# it is deleted — 5 minutes is the minimum requested wait.
_RECREATE_SAFETY_WAIT_SECONDS = 300


# =============================================================================
# Gemini Enterprise REST helpers
# =============================================================================

def _de_hostname(ge_location: str) -> str:
    return "discoveryengine.googleapis.com" if ge_location == "global" \
        else f"{ge_location}-discoveryengine.googleapis.com"


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


class GEClient:
    """Thin wrapper around the Gemini Enterprise agents/authorizations REST API."""

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

    def get_agent(self, agent_id: str) -> dict | None:
        headers = self._headers()
        if not headers:
            return None
        resp = requests.get(self._agents_url(agent_id), headers=headers, verify=_SSL_VERIFY)
        return resp.json() if resp.status_code == 200 else None

    def find_agent_by_display_name(self, display_name: str) -> dict | None:
        """GE assigns its own numeric resource ID on create (the 'name' field in
        the create payload is NOT used as the ID), so finding an existing
        registration on redeploy requires listing and matching by display name.
        """
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


    # GE's authorization-in-use lock is eventually consistent: after an agent
    # using an authorization is deleted, GE can take several minutes before
    # it allows a new agent to attach to that same authorization. Retry with
    # a long backoff (2m, 3m, 5m) rather than failing fast.
    _LOCK_RETRY_DELAYS = (120, 180, 300)

    def create_agent(self, payload: dict) -> dict | None:
        """POST a new agent. Retries with long backoff if the authorization is locked."""
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
                print(
                    f"  ⏳ Authorization locked (attempt {attempt}/{attempts}) — "
                    f"waiting {delay // 60}m{delay % 60:02d}s for GE to release it..."
                )
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
            self._agents_url(agent_id),
            headers=headers,
            json=payload,
            params={"updateMask": ",".join(update_mask)},
            verify=_SSL_VERIFY,
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
# Authorization resource (single stable ID)
# =============================================================================

def _ensure_auth_resource(auth_id: str, project_id: str, project_number: str, ge_location: str) -> str | None:
    """Return the auth resource name, creating it if it doesn't already exist."""
    from scripts.setup_agent_auth import _check_auth_exists, _create_auth

    existing = _check_auth_exists(project_number, ge_location, auth_id, project_id)
    if existing:
        return existing.get("name")

    client_id = os.environ.get("OAUTH_CLIENT_ID")
    client_secret = os.environ.get("OAUTH_CLIENT_SECRET")
    if not (client_id and client_secret):
        print("  ⚠ Missing OAUTH_CLIENT_ID/SECRET — skipping auth resource creation")
        return None

    print(f"  ⏳ Creating auth resource '{auth_id}'...")
    result = _create_auth(
        project_number=project_number,
        ge_location=ge_location,
        auth_id=auth_id,
        project_id=project_id,
        oauth_client_id=client_id,
        oauth_client_secret=client_secret,
    )
    if result:
        print(f"  ✓ Created auth resource: {result.get('name')}")
        return result.get("name")
    print(f"  ✗ Could not create auth resource '{auth_id}'")
    return None


# =============================================================================
# GE registration (create on first deploy, PATCH in place on redeploys)
# =============================================================================

def _build_agent_payload(display_name: str, description: str, agent_card_json: str) -> dict:
    return {
        "displayName": display_name,
        "description": description,
        "a2aAgentDefinition": {"jsonAgentCard": agent_card_json},
    }


def _register_or_update(
    ge: GEClient,
    *,
    display_name: str,
    description: str,
    agent_card_json: str,
    agent_authorization: str | None,
    access_policy: str,
) -> bool:
    """Create the GE agent on first deploy, or PATCH it in place on redeploys.

    PATCHing in place (rather than delete+recreate) means the authorization
    resource stays attached the whole time — no detach/reattach race, no
    "used by another agent" lock on redeploys.

    GE assigns its own numeric resource ID on create (the ``name`` field sent
    in the create payload is NOT used as the ID), so the existing agent must
    be found by display name rather than by guessing its resource ID.
    """
    payload = _build_agent_payload(display_name, description, agent_card_json)
    existing = ge.find_agent_by_display_name(display_name)

    if existing:
        existing_id = existing.get("name", "").split("/")[-1]
        update_mask = ["displayName", "description", "a2aAgentDefinition"]

        # Defensive fix: if a prior run registered this agent WITHOUT an
        # authorization attached (e.g. auth creation failed but registration
        # otherwise succeeded), make sure a redeploy heals that instead of
        # silently leaving the agent unauthenticated forever.
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
    ge.patch_agent(
        result_id, {"sharingConfig": {"scope": access_policy}},
        update_mask=["sharingConfig.scope"],
    )
    print(f"  ✓ Gallery visibility set to '{access_policy}'")
    return True



# =============================================================================
# YAML config write-back (persist the Reasoning Engine resource name)
# =============================================================================

def _persist_reasoning_engine(agent_name: str, engine_resource: str) -> None:
    """Write/replace `deploy.reasoning_engine` in the agent's YAML config.

    Uses a targeted regex/line-based edit (not a full YAML dump) so we never
    disturb comments, key ordering, or formatting elsewhere in the file.
    """
    config_path = get_agent_config_path(agent_name)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"  ⚠ Could not read {config_path} to persist reasoning_engine: {e}")
        return

    line_re = re.compile(r"^(\s*reasoning_engine:).*$", re.MULTILINE)
    new_line = rf"\1 {engine_resource}"

    if line_re.search(text):
        new_text = line_re.sub(new_line, text, count=1)
    else:
        # Insert right after the `deploy:` section header.
        deploy_re = re.compile(r"^(deploy:\s*\n)", re.MULTILINE)
        if deploy_re.search(text):
            new_text = deploy_re.sub(
                rf"\1  reasoning_engine: {engine_resource}\n", text, count=1
            )
        else:
            print(f"  ⚠ Could not find 'deploy:' section in {config_path} — "
                  f"add this manually: reasoning_engine: {engine_resource}")
            return

    try:
        config_path.write_text(new_text, encoding="utf-8")
        print(f"  ✓ Persisted reasoning_engine to {config_path.name}")
    except OSError as e:
        print(f"  ⚠ Could not write {config_path}: {e}")


# =============================================================================
# Agent Engine resource helpers (existence check, create, update, delete)
# =============================================================================

def _normalize_engine_resource(value: str, project_id: str, location: str) -> str:
    """Accept a bare ID or a full resource name; always return the full name."""
    if value.startswith("projects/"):
        return value
    return f"projects/{project_id}/locations/{location}/reasoningEngines/{value}"


def _engine_exists(client: "vertexai.Client", engine_resource: str) -> bool:
    """Return True iff the Reasoning Engine resource still exists."""
    try:
        client.agent_engines.get(name=engine_resource)
        return True
    except ClientError as e:
        if e.code == 404:
            return False
        # Any other error (e.g. permission issue, malformed name) — don't
        # silently fall through to a create/delete decision; surface it.
        print(f"  ⚠ Could not verify existing engine (HTTP {e.code}): {e}")
        return False
    except Exception as e:
        print(f"  ⚠ Could not verify existing engine: {e}")
        return False


def _build_a2a_agent(config: dict):
    """Build the A2aAgent instance + display metadata used for create/update."""
    agent_cfg = config.get("agent", {})
    deploy_cfg = config.get("deploy", {})

    display_name = agent_cfg.get("display_name", AGENT_NAME)
    description = agent_cfg.get("description", "")
    skills_cfg = deploy_cfg.get("skills", [])

    skills = [
        AgentSkill(
            id=s.get("id", ""), name=s.get("name", ""), description=s.get("description", ""),
            tags=s.get("tags", []), examples=s.get("examples", []),
        )
        for s in skills_cfg
    ]
    agent_card = create_agent_card(
        agent_name=display_name, description=description, skills=skills,
        default_input_modes=deploy_cfg.get("default_input_modes", ["text/plain"]),
        default_output_modes=deploy_cfg.get("default_output_modes", ["text/plain"]),
    )

    executor_class = _load_executor_class(AGENT_NAME)
    if executor_class is None:
        raise RuntimeError(f"No executor class found for {AGENT_NAME}")

    a2a_agent = A2aAgent(agent_card=agent_card, agent_executor_builder=executor_class)
    a2a_agent.set_up()
    return a2a_agent, display_name, description


def _build_engine_config(config: dict, project_id: str, api_version: str, storage: str) -> dict:
    deploy_cfg = config.get("deploy", {})
    description = config.get("agent", {}).get("description", "")

    requirements = deploy_cfg.get("base_requirements", []) + deploy_cfg.get("extra_requirements", [])
    extra_packages = [
        str(_REPO_ROOT / "widgets") if pkg in ("widgets", "../widgets") else pkg
        for pkg in deploy_cfg.get("extra_packages", [])
    ]
    env_vars = {**deploy_cfg.get("env_vars", {}), "PROJECT_ID": project_id}
    if _ssl_env:
        env_vars["SSL_VERIFY"] = os.environ["SSL_VERIFY"]
    if os.environ.get("OBO_CREDENTIAL_MODE"):
        env_vars["OBO_CREDENTIAL_MODE"] = os.environ["OBO_CREDENTIAL_MODE"]

    return {
        "display_name": f"{AGENT_NAME}_agent",
        "description": description,
        "agent_framework": deploy_cfg.get("agent_framework", "google-adk"),
        "staging_bucket": storage,
        "gcs_dir_name": AGENT_NAME,
        "requirements": requirements,
        "http_options": {"api_version": api_version},
        "max_instances": deploy_cfg.get("max_instances", 1),
        "extra_packages": extra_packages,
        "env_vars": env_vars,
    }


# =============================================================================
# Deploy
# =============================================================================

def _load_executor_class(agent_name: str):
    module = importlib.import_module(f"agents.{agent_name}.executor")
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and hasattr(attr, "AGENT_CONFIG_NAME") and attr_name != "BaseA2UIExecutor":
            return attr
    return None


def deploy(dry_run: bool = False, force_recreate_engine: bool = False) -> bool:
    config = load_agent_config(AGENT_NAME)
    agent_cfg = config.get("agent", {})
    deploy_cfg = config.get("deploy", {})

    display_name = agent_cfg.get("display_name", AGENT_NAME)
    skills_cfg = deploy_cfg.get("skills", [])
    project_id = os.environ["PROJECT_ID"]
    location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))
    storage = os.environ.get("STORAGE_BUCKET")
    api_version = deploy_cfg.get("api_version", "v1beta1")

    print(f"  ├── Model: {agent_cfg.get('model', 'default')}")
    print(f"  ├── Tools: {len(agent_cfg.get('tools', []))}")
    print(f"  ├── Skills: {len(skills_cfg)}")

    if dry_run:
        print(f"  ├── Region: {location}")
        print(f"  ├── Display Name: {display_name}")
        existing_engine = deploy_cfg.get("reasoning_engine")
        if existing_engine:
            print(f"  ├── Existing engine on file: {existing_engine}")
            print("  ├── Would attempt: in-place UPDATE (or CREATE if that engine no longer exists)")
        else:
            print("  ├── No existing engine on file — would attempt: CREATE (first-time deploy)")
        print("  └── 🔍 DRY RUN — nothing deployed")
        return True

    vertexai.init(
        project=project_id, location=location, staging_bucket=storage,
        api_endpoint=f"{location}-aiplatform.googleapis.com",
    )
    client = vertexai.Client(
        project=project_id, location=location,
        http_options=types.HttpOptions(api_version=api_version),
    )

    try:
        a2a_agent, _display_name, _description = _build_a2a_agent(config)
    except RuntimeError as e:
        print(f"  ✗ {e}")
        return False

    engine_config = _build_engine_config(config, project_id, api_version, storage)

    existing_engine_raw = deploy_cfg.get("reasoning_engine")
    existing_engine = (
        _normalize_engine_resource(existing_engine_raw, project_id, location)
        if existing_engine_raw else None
    )

    engine_exists = bool(existing_engine) and _engine_exists(client, existing_engine)

    # -------------------------------------------------------------------
    # --force-recreate-engine: delete the current engine, then fall through
    # to the create path below with a mandatory safety wait.
    # -------------------------------------------------------------------
    if force_recreate_engine and engine_exists:
        print(f"  ⚠ --force-recreate-engine: deleting existing engine {existing_engine} ...")
        try:
            op = client.agent_engines.delete(name=existing_engine, force=True)
            _ = op  # deletion is typically synchronous-enough for our purposes
            print("  ✓ Deleted existing Reasoning Engine")
        except Exception as e:
            print(f"  ✗ Failed to delete existing engine: {e}")
            return False
        engine_exists = False
        print(
            f"  ⏳ Waiting {_RECREATE_SAFETY_WAIT_SECONDS // 60} minutes before "
            f"provisioning a new engine (eventual-consistency safety margin)..."
        )
        time.sleep(_RECREATE_SAFETY_WAIT_SECONDS)

    start = time.time()
    if engine_exists:
        # ---------------------------------------------------------------
        # REDEPLOY: update the SAME Reasoning Engine resource in place.
        # Resource name / A2A URL never changes -> GE registration/auth
        # never needs to move.
        # ---------------------------------------------------------------
        print(f"  ⏳ Redeploying in place onto existing engine: {existing_engine}")
        try:
            remote_agent = client.agent_engines.update(
                name=existing_engine,
                agent=a2a_agent,
                config=engine_config,
            )
        except Exception as e:
            print(f"  ✗ In-place update failed: {e}")
            return False
        engine_resource = remote_agent.api_resource.name
        print(f"  ✓ Updated in place: {engine_resource} ({time.time() - start:.0f}s)")
    else:
        # ---------------------------------------------------------------
        # FIRST-TIME DEPLOY (or recovering from a deleted/missing engine):
        # create a brand-new Reasoning Engine resource.
        # ---------------------------------------------------------------
        if existing_engine_raw:
            print(f"  ⚠ Recorded engine '{existing_engine_raw}' no longer exists — creating a new one")
        else:
            print("  ⏳ No existing engine on file — this is a first-time deploy")
        print("  ⏳ Creating new Agent Engine resource...")
        try:
            remote_agent = client.agent_engines.create(
                agent=a2a_agent,
                config=engine_config,
            )
        except Exception as e:
            print(f"  ✗ Deployment failed: {e}")
            return False
        engine_resource = remote_agent.api_resource.name
        print(f"  ✓ Created: {engine_resource} ({time.time() - start:.0f}s)")
        _persist_reasoning_engine(AGENT_NAME, engine_resource)

    return _register(engine_resource, config)


def register_only(reasoning_engine: str) -> bool:
    config = load_agent_config(AGENT_NAME)
    deploy_cfg = config.get("deploy", {})
    location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))

    if not reasoning_engine.startswith("projects/"):
        project_id = os.environ["PROJECT_ID"]
        project_number = _get_project_number(project_id) or project_id
        reasoning_engine = f"projects/{project_number}/locations/{location}/reasoningEngines/{reasoning_engine}"

    print(f"  ⏳ Registering existing engine: {reasoning_engine}")
    return _register(reasoning_engine, config)


def _register(engine_resource: str, config: dict) -> bool:
    """Fetch the A2A card from a deployed engine and register/patch it in GE."""
    agent_cfg = config.get("agent", {})
    deploy_cfg = config.get("deploy", {})
    location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))
    api_version = deploy_cfg.get("api_version", "v1beta1")
    api_endpoint = f"{location}-aiplatform.googleapis.com"

    token = _bearer_token()
    if not token:
        return False
    try:
        resp = httpx.get(
            f"https://{api_endpoint}/{api_version}/{engine_resource}/a2a/v1/card",
            headers={"Authorization": f"Bearer {token}"},
            verify=(_SSL_VERIFY is not False), timeout=60,
        )
        resp.raise_for_status()
        card = resp.json()
    except Exception as e:
        print(f"  ✗ Could not fetch A2A card: {e}")
        return False

    card["capabilities"] = build_a2ui_agent_card(AGENT_NAME)["capabilities"]
    card["url"] = f"https://{api_endpoint}/{api_version}/{engine_resource}/a2a/v1"
    print(f"  ✓ Agent card A2A URL: {card['url']}")

    project_id = os.environ["PROJECT_ID"]
    ge_location = os.environ.get("GE_LOCATION", "global")
    ge = GEClient(project_id, os.environ["GEMINI_ENTERPRISE_APP_ID"], ge_location)

    auth_id = deploy_cfg.get("agent_authorization_id")
    agent_authorization = None
    if auth_id:
        project_number = _get_project_number(project_id)
        if project_number:
            agent_authorization = _ensure_auth_resource(auth_id, project_id, project_number, ge_location)

    return _register_or_update(
        ge,
        display_name=agent_cfg.get("display_name", AGENT_NAME),
        description=agent_cfg.get("description", ""),
        agent_card_json=json.dumps(card),
        agent_authorization=agent_authorization,
        access_policy=deploy_cfg.get("ge_access_policy", "ALL_USERS"),
    )


def undeploy(delete_engine: bool = False) -> bool:
    config = load_agent_config(AGENT_NAME)
    display_name = config.get("agent", {}).get("display_name", AGENT_NAME)
    deploy_cfg = config.get("deploy", {})

    project_id = os.environ["PROJECT_ID"]
    ge_location = os.environ.get("GE_LOCATION", "global")
    ge = GEClient(project_id, os.environ["GEMINI_ENTERPRISE_APP_ID"], ge_location)

    existing = ge.find_agent_by_display_name(display_name)
    if not existing:
        print(f"  ℹ No GE registration found for '{display_name}' — nothing to unregister")
        ok = True
    else:
        ge_agent_id = existing.get("name", "").split("/")[-1]
        print(f"  ⏳ Unregistering '{display_name}' ({ge_agent_id}) from Gemini Enterprise...")
        ok = ge.delete_agent(ge_agent_id)
        if ok:
            print("  ✓ Unregistered from Gemini Enterprise")
        else:
            print("  ✗ Failed to unregister from Gemini Enterprise")

    if not delete_engine:
        engine_resource = deploy_cfg.get("reasoning_engine")
        print("  ℹ To delete the Agent Engine resource:")
        if engine_resource:
            print(f"    python scripts/deploy.py --undeploy --delete-engine")
            print(f"    (or) gcloud ai reasoning-engines delete {engine_resource.split('/')[-1]} --region={deploy_cfg.get('region', 'us-central1')}")
        else:
            print("    gcloud ai reasoning-engines list --region=us-central1")
            print("    gcloud ai reasoning-engines delete <RESOURCE_ID> --region=us-central1")
        return ok

    engine_resource_raw = deploy_cfg.get("reasoning_engine")
    if not engine_resource_raw:
        print("  ℹ No reasoning_engine recorded in YAML — nothing to delete")
        return ok

    location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))
    api_version = deploy_cfg.get("api_version", "v1beta1")
    engine_resource = _normalize_engine_resource(engine_resource_raw, project_id, location)

    print(f"  ⏳ Deleting Agent Engine resource: {engine_resource}")
    try:
        vertexai.init(project=project_id, location=location, api_endpoint=f"{location}-aiplatform.googleapis.com")
        client = vertexai.Client(
            project=project_id, location=location,
            http_options=types.HttpOptions(api_version=api_version),
        )
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



# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description=f"Deploy {AGENT_NAME} to Agent Engine + Gemini Enterprise")
    parser.add_argument("--dry-run", action="store_true", help="Preview config without deploying")
    parser.add_argument("--undeploy", action="store_true", help="Unregister from Gemini Enterprise")
    parser.add_argument("--delete-engine", action="store_true", help="With --undeploy, also delete the Agent Engine resource")
    parser.add_argument("--force-recreate-engine", action="store_true",
                         help="Delete the existing Reasoning Engine and create a brand-new one "
                              "(instead of updating in place). Waits 5 minutes before re-registering.")
    parser.add_argument("--register-only", action="store_true", help="Skip deploy; register an existing Reasoning Engine")
    parser.add_argument("--reasoning-engine", help="Reasoning Engine resource name or ID (with --register-only)")
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
