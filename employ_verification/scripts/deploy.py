"""
Agent Framework — Generic Deployment Script

Deploys agents to Vertex AI Agent Engine and registers them in Gemini Enterprise.
All agent configuration is read from YAML files in config/.

Usage:
    # Deploy a single agent
    python scripts/deploy.py employee_verification

    # Deploy multiple specific agents
    python scripts/deploy.py employee_verification benefits_enrollment

    # Deploy ALL agents (every YAML in config/)
    python scripts/deploy.py --all

    # List available agents
    python scripts/deploy.py --list

    # Dry run — show what would be deployed
    python scripts/deploy.py employee_verification --dry-run

    # Undeploy an agent from Agent Engine
    python scripts/deploy.py employee_verification --undeploy
"""

# Add project root to sys.path and load environment variables early
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PROJECT_ROOT.parent
for _path in (_PROJECT_ROOT, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=True)

# Corporate proxies (Zscaler/Netskope): when SSL_VERIFY=false, patch Python SSL
# before google/vertex SDK clients initialize their HTTP stacks.
if os.environ.get("SSL_VERIFY", "").strip().lower() in ("false", "0", "no"):
    import ssl

    ssl._create_default_https_context = ssl._create_unverified_context  # type: ignore[attr-defined]

    import httpx

    _OrigHttpxClient = httpx.Client.__init__
    _OrigHttpxAsync = httpx.AsyncClient.__init__

    def _httpx_client_no_verify(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        _OrigHttpxClient(self, *args, **kwargs)

    def _httpx_async_no_verify(self, *args, **kwargs):
        kwargs.setdefault("verify", False)
        _OrigHttpxAsync(self, *args, **kwargs)

    httpx.Client.__init__ = _httpx_client_no_verify  # type: ignore[method-assign]
    httpx.AsyncClient.__init__ = _httpx_async_no_verify  # type: ignore[method-assign]

import argparse
import importlib
import json
import os
import re
import sys
import time

import httpx
import requests
import urllib3
import vertexai
from a2a.types import AgentSkill
from google.auth import default
from google.auth.transport.requests import Request
from google.genai import types
from vertexai.preview.reasoning_engines import A2aAgent
from vertexai.preview.reasoning_engines.templates.a2a import create_agent_card

from agents._base.config_loader import load_agent_config, list_available_agents
from agents._base.agent_card import build_a2ui_agent_card

# Import setup_agent_auth helpers inline to avoid circular imports
from scripts.setup_agent_auth import (
    _get_project_number,
    _SSL_VERIFY,
    _check_auth_exists,
    _create_auth,
)

# Suppress InsecureRequestWarning when SSL verification is disabled
if _SSL_VERIFY is False:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# =============================================================================
# Helpers
# =============================================================================

def _get_bearer_token() -> str | None:
    """Gets a bearer token for authenticating with Google Cloud."""
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        request = Request()
        credentials.refresh(request)
        return credentials.token
    except Exception as e:
        print(f"  ✗ Error getting credentials: {e}")
        print("    Please run: gcloud auth application-default login")
        return None


def _get_de_hostname(ge_location: str) -> str:
    """Return the correct Discovery Engine API hostname for the given GE location.

    Args:
        ge_location: The Gemini Enterprise region ('global', 'us', 'eu', etc.)

    Returns:
        The correct API hostname string.
    """
    if ge_location == "global":
        return "discoveryengine.googleapis.com"
    return f"{ge_location}-discoveryengine.googleapis.com"


# GE's deletion of a prior agent registration is eventually consistent: the
# authorization resource can appear "in use" for a few seconds after the
# owning agent was deleted. Retry registration with backoff instead of
# failing immediately on this specific, transient 400.
_AUTH_IN_USE_MARKER = "is used by another agent"
_REGISTER_RETRY_DELAYS = (3, 6, 10)  # seconds, applied between attempts


def _register_agent_on_gemini_enterprise(
    project_id: str,
    app_id: str,
    agent_card: str,
    agent_name: str,
    display_name: str,
    description: str,
    agent_authorization: str | None = None,
    ge_location: str = "global",
    reasoning_engine: str | None = None,
    ge_registration: str = "adk",
) -> dict | None:
    """Register an agent in Gemini Enterprise.

    ge_registration:
      - ``adk`` — link GE to a provisioned Reasoning Engine (adkAgentDefinition)
      - ``a2a`` — GE calls the A2A URL from jsonAgentCard (a2aAgentDefinition)

    Retries automatically if the authorization resource momentarily reports
    "is used by another agent" — a transient race after unregistering the
    previous agent that owned it (GE agent deletion is eventually consistent).
    """
    de_hostname = _get_de_hostname(ge_location)
    api_endpoint = (
        f"https://{de_hostname}/v1alpha/projects/{project_id}/"
        f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
        "assistants/default_assistant/agents"
    )

    payload = {
        "name": agent_name,
        "displayName": display_name,
        "description": description,
    }

    if ge_registration == "a2a":
        payload["a2aAgentDefinition"] = {"jsonAgentCard": agent_card}
    elif reasoning_engine:
        payload["adkAgentDefinition"] = {
            "toolSettings": {
                "toolDescription": display_name
            },
            "provisionedReasoningEngine": {
                "reasoningEngine": reasoning_engine
            }
        }
    else:
        payload["a2aAgentDefinition"] = {"jsonAgentCard": agent_card}

    if agent_authorization:
        payload["authorization_config"] = {"agent_authorization": agent_authorization}

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return None

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    attempts = len(_REGISTER_RETRY_DELAYS) + 1
    response = None
    for attempt in range(1, attempts + 1):
        response = requests.post(
            api_endpoint, headers=headers, json=payload, verify=_SSL_VERIFY
        )

        if response.status_code == 200:
            return response.json()

        is_auth_lock = (
            response.status_code == 400
            and _AUTH_IN_USE_MARKER in response.text
        )
        if is_auth_lock and attempt <= len(_REGISTER_RETRY_DELAYS):
            delay = _REGISTER_RETRY_DELAYS[attempt - 1]
            print(
                f"  ⏳ Auth resource momentarily locked by the just-removed "
                f"agent (attempt {attempt}/{attempts}) — retrying in {delay}s..."
            )
            time.sleep(delay)
            continue

        break

    # Log the full error for debugging — always show the complete response body
    print(f"  ✗ GE registration failed (HTTP {response.status_code})")
    print(f"    URL: {api_endpoint}")
    print(f"    Response: {response.text}")
    if agent_authorization:
        print(f"    Auth resource used: {agent_authorization}")
    if response.status_code == 400 and _AUTH_IN_USE_MARKER in response.text:
        auth_id_hint = (
            agent_authorization.rsplit("/", 1)[-1] if agent_authorization else "<AUTH_ID>"
        )
        print(
            "    ℹ The authorization resource is still locked after retries. "
            "Force-recreate it and try again:\n"
            f"      python scripts/setup_agent_auth.py --id {auth_id_hint} --force\n"
            "      python scripts/deploy.py <agent> --register-only "
            "--reasoning-engine <RESOURCE_ID>"
        )
    return None


def _verify_ge_agent_registration(
    project_id: str,
    app_id: str,
    agent_name: str,
    ge_location: str = "global",
) -> None:
    """Fetch a registered GE agent and log whether OAuth authorization is attached."""
    de_hostname = _get_de_hostname(ge_location)
    url = (
        f"https://{de_hostname}/v1alpha/projects/{project_id}/"
        f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
        f"assistants/default_assistant/agents/{agent_name}"
    )

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "X-Goog-User-Project": project_id,
    }

    response = requests.get(url, headers=headers, verify=_SSL_VERIFY)
    if response.status_code != 200:
        print(f"  ⚠ Could not verify GE registration (HTTP {response.status_code})")
        return

    agent = response.json()
    auth_config = agent.get("authorizationConfig") or agent.get("authorization_config") or {}
    agent_auth = auth_config.get("agentAuthorization") or auth_config.get("agent_authorization")
    reg_type = "a2a" if agent.get("a2aAgentDefinition") else "adk"
    print(f"  ✓ GE registration type: {reg_type}")
    if agent_auth:
        print(f"  ✓ GE agent has OAuth authorization: {agent_auth}")
    else:
        print("  ⚠ GE agent registered WITHOUT authorizationConfig — users will not be prompted to sign in")


def _resolve_ge_agent_id(
    project_id: str,
    app_id: str,
    ge_location: str,
    *,
    agent_name: str | None = None,
    display_name: str | None = None,
) -> str | None:
    """Resolve the server-assigned GE agent ID (numeric) for PATCH/GET calls."""
    if agent_name:
        de_hostname = _get_de_hostname(ge_location)
        url = (
            f"https://{de_hostname}/v1alpha/projects/{project_id}/"
            f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
            f"assistants/default_assistant/agents/{agent_name}"
        )
        bearer_token = _get_bearer_token()
        if bearer_token:
            headers = {
                "Authorization": f"Bearer {bearer_token}",
                "X-Goog-User-Project": project_id,
            }
            response = requests.get(url, headers=headers, verify=_SSL_VERIFY)
            if response.status_code == 200:
                return response.json().get("name", "").split("/")[-1]

    if display_name:
        for agent in _list_ge_agents(project_id, app_id, ge_location):
            if agent.get("displayName") == display_name:
                return agent.get("name", "").split("/")[-1]
    return None


def _set_agent_access_policy(
    project_id: str,
    app_id: str,
    agent_name: str,
    access_policy: str = "ALL_USERS",
    ge_location: str = "global",
    display_name: str | None = None,
) -> bool:
    """Set gallery visibility for a registered GE agent via sharingConfig.scope.

    Args:
        access_policy: ``ALL_USERS`` (agent gallery) or ``ADMINS_ONLY`` (admin table only).
    """
    ge_agent_id = _resolve_ge_agent_id(
        project_id,
        app_id,
        ge_location,
        agent_name=agent_name,
        display_name=display_name,
    )
    if not ge_agent_id:
        print("  ⚠ Could not resolve GE agent ID for sharing config update")
        return False

    de_hostname = _get_de_hostname(ge_location)
    api_endpoint = (
        f"https://{de_hostname}/v1alpha/projects/{project_id}/"
        f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
        f"assistants/default_assistant/agents/{ge_agent_id}"
    )

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return False

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    payload = {"sharingConfig": {"scope": access_policy}}
    params = {"updateMask": "sharingConfig.scope"}

    response = requests.patch(
        api_endpoint, headers=headers, json=payload, params=params, verify=_SSL_VERIFY
    )

    if response.status_code == 200:
        return True

    print(f"  ⚠ Could not set sharing config (HTTP {response.status_code}): {response.text}")
    return False


def _list_ge_agents(
    project_id: str,
    app_id: str,
    ge_location: str = "global",
) -> list[dict]:
    """List agents registered in Gemini Enterprise."""
    de_hostname = _get_de_hostname(ge_location)
    api_endpoint = (
        f"https://{de_hostname}/v1alpha/projects/{project_id}/"
        f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
        "assistants/default_assistant/agents?pageSize=200"
    )

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return []

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    try:
        response = requests.get(api_endpoint, headers=headers, verify=_SSL_VERIFY)
        if response.status_code == 200:
            return response.json().get("agents", [])
    except Exception as e:
        print(f"  ⚠ Error listing GE agents: {e}")
    return []


def _delete_ge_agent_by_id(
    project_id: str,
    app_id: str,
    ge_agent_id: str,
    ge_location: str = "global",
) -> bool:
    """Delete a GE agent by its server-assigned ID."""
    de_hostname = _get_de_hostname(ge_location)
    api_endpoint = (
        f"https://{de_hostname}/v1alpha/projects/{project_id}/"
        f"locations/{ge_location}/collections/default_collection/engines/{app_id}/"
        f"assistants/default_assistant/agents/{ge_agent_id}"
    )

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return False

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }

    response = requests.delete(api_endpoint, headers=headers, verify=_SSL_VERIFY)
    return response.status_code in (200, 204, 404)


def _unregister_agent_from_gemini_enterprise(
    project_id: str,
    app_id: str,
    agent_name: str,
    ge_location: str = "global",
    display_name: str | None = None,
    auth_id: str | None = None,
) -> bool:
    """Unregister an agent from Gemini Enterprise.

    GE assigns server-side numeric agent IDs, so we also search by display name
    and per-agent authorization ID to remove stale registrations.
    """
    deleted_any = False

    if _delete_ge_agent_by_id(project_id, app_id, agent_name, ge_location):
        deleted_any = True

    auth_suffix = f"/authorizations/{auth_id}" if auth_id else None

    for agent in _list_ge_agents(project_id, app_id, ge_location):
        ge_agent_id = agent.get("name", "").split("/")[-1]
        if ge_agent_id == agent_name:
            continue

        agent_display = agent.get("displayName", "")
        agent_auth = (
            (agent.get("authorizationConfig") or {}).get("agentAuthorization") or ""
        )

        match = False
        if display_name and agent_display == display_name:
            match = True
        if auth_suffix and auth_suffix in agent_auth:
            match = True

        if match and _delete_ge_agent_by_id(project_id, app_id, ge_agent_id, ge_location):
            print(f"  ✓ Removed stale GE agent '{agent_display}' ({ge_agent_id})")
            deleted_any = True

    return deleted_any


# =============================================================================
# Auth resource resolution (single stable ID, or blue/green rotation)
# =============================================================================

def _ensure_auth_resource(
    *,
    auth_id: str,
    ge_location: str,
    project_id: str,
    project_number: str,
    oauth_client_id: str,
    oauth_client_secret: str,
) -> str | None:
    """Return the resource name for ``auth_id``, creating it if it doesn't exist."""
    existing = _check_auth_exists(project_number, ge_location, auth_id, project_id)
    if existing:
        return existing.get("name")

    print(f"  ⏳ Creating auth resource '{auth_id}' in {ge_location}...")
    result_auth = _create_auth(
        project_number=project_number,
        ge_location=ge_location,
        auth_id=auth_id,
        project_id=project_id,
        oauth_client_id=oauth_client_id,
        oauth_client_secret=oauth_client_secret,
    )
    if result_auth:
        print(f"  ✓ Created auth resource: {result_auth.get('name')}")
        return result_auth.get("name")

    print(f"  ✗ Could not create auth resource '{auth_id}'")
    return None


def _set_active_auth_slot(agent_name: str, slot: str) -> None:
    """Persist ``deploy.active_auth_slot: <slot>`` into config/<agent_name>.yaml.

    Does a targeted text replace (not a full YAML re-dump) so comments and
    formatting elsewhere in the file are left untouched.
    """
    config_path = _PROJECT_ROOT / "config" / f"{agent_name}.yaml"
    text = config_path.read_text()
    line_re = re.compile(r"^(\s*)active_auth_slot:\s*.*$", flags=re.MULTILINE)
    new_line = rf'\1active_auth_slot: "{slot}"'
    if line_re.search(text):
        text = line_re.sub(new_line, text, count=1)
    else:
        text = text.replace("deploy:\n", f'deploy:\n  active_auth_slot: "{slot}"\n', 1)
    config_path.write_text(text)
    print(f"  ✓ Recorded active auth slot '{slot}' in config/{agent_name}.yaml")


def _resolve_agent_authorization(
    deploy_cfg: dict,
    project_id: str,
    agent_name: str | None = None,
) -> str | None:
    """Create or resolve the GE OAuth authorization resource for an agent.

    Supports two shapes in the YAML:
      - ``agent_authorization_id: "auth-foo"`` — single stable ID (default).
      - ``agent_authorization_ids: ["auth-foo-blue", "auth-foo-green"]`` plus
        ``active_auth_slot: "blue"|"green"`` — blue/green rotation. Each
        deploy registers against the *standby* slot (the one not currently
        active), so it's never the resource GE just detached from a deleted
        agent — avoiding the "used by another agent" lock/delay entirely.
        After a successful deploy, the caller flips ``active_auth_slot``.
    """
    ge_location = os.environ.get("GE_LOCATION", "global")
    auth_ids = deploy_cfg.get("agent_authorization_ids")

    if auth_ids:
        if len(auth_ids) != 2:
            print("  ✗ agent_authorization_ids must list exactly 2 slots (blue/green)")
            return None

        oauth_client_id = os.environ.get("OAUTH_CLIENT_ID")
        oauth_client_secret = os.environ.get("OAUTH_CLIENT_SECRET")
        project_number = _get_project_number(project_id)
        if not (project_number and oauth_client_id and oauth_client_secret):
            print("  ⚠ Missing OAUTH_CLIENT_ID/SECRET — skipping auth resource creation")
            return None

        active_slot = deploy_cfg.get("active_auth_slot", "blue")
        slot_names = ["blue", "green"]
        slot_ids = dict(zip(slot_names, auth_ids))
        standby_slot = "green" if active_slot == "blue" else "blue"
        standby_auth_id = slot_ids[standby_slot]

        print(
            f"  ├── Auth slots: active='{active_slot}', deploying to "
            f"standby='{standby_slot}' ({standby_auth_id})"
        )
        agent_authorization = _ensure_auth_resource(
            auth_id=standby_auth_id,
            ge_location=ge_location,
            project_id=project_id,
            project_number=project_number,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
        )
        if agent_authorization and agent_name:
            _set_active_auth_slot(agent_name, standby_slot)
        return agent_authorization

    auth_id = deploy_cfg.get("agent_authorization_id")
    if auth_id:
        oauth_client_id = os.environ.get("OAUTH_CLIENT_ID")
        oauth_client_secret = os.environ.get("OAUTH_CLIENT_SECRET")
        project_number = _get_project_number(project_id)

        if project_number and oauth_client_id and oauth_client_secret:
            agent_authorization = _ensure_auth_resource(
                auth_id=auth_id,
                ge_location=ge_location,
                project_id=project_id,
                project_number=project_number,
                oauth_client_id=oauth_client_id,
                oauth_client_secret=oauth_client_secret,
            )
            if agent_authorization:
                return agent_authorization
            print(
                f"  ✗ Could not create auth resource '{auth_id}' — "
                "GE will not prompt for login without it"
            )
            return None

        print("  ⚠ Missing OAUTH_CLIENT_ID/SECRET — skipping auth resource creation")
        return None

    env_auth = os.environ.get("AGENT_AUTHORIZATION")
    if env_auth is not None:
        env_auth_stripped = env_auth.strip('"').strip()
        if env_auth_stripped.lower() not in ("none", ""):
            print(f"  ✓ Using AGENT_AUTHORIZATION from environment: {env_auth_stripped}")
            return env_auth_stripped
        print("  ⚠ Agent authorization disabled (AGENT_AUTHORIZATION=none)")
    return None


def _register_agent_in_gemini_enterprise(
    *,
    agent_name: str,
    config: dict,
    a2ui_agent_card_str: str,
    agent_authorization: str | None,
    reasoning_engine: str | None = None,
    ge_registration: str | None = None,
) -> bool:
    """Unregister any prior GE agent and register with the latest card + auth."""
    agent_cfg = config.get("agent", {})
    deploy_cfg = config.get("deploy", {})
    ge_registration = ge_registration or deploy_cfg.get("ge_registration", "adk")
    project_id = os.environ.get("PROJECT_ID")
    app_id = os.environ.get("GEMINI_ENTERPRISE_APP_ID")
    ge_location = os.environ.get("GE_LOCATION", "global")
    display_name = agent_cfg.get("display_name", agent_name)
    description = agent_cfg.get("description", "")

    print(f"  ⏳ Removing any existing GE registration for '{agent_name}_agent'...")
    _unregister_agent_from_gemini_enterprise(
        project_id=project_id,
        app_id=app_id,
        agent_name=f"{agent_name}_agent",
        ge_location=ge_location,
        display_name=display_name,
        auth_id=deploy_cfg.get("agent_authorization_id"),
    )

    print(f"  ⏳ Registering in Gemini Enterprise ({ge_registration.upper()} registration)...")
    result = _register_agent_on_gemini_enterprise(
        project_id=project_id,
        app_id=app_id,
        agent_card=a2ui_agent_card_str,
        agent_name=f"{agent_name}_agent",
        display_name=display_name,
        description=description,
        agent_authorization=agent_authorization,
        ge_location=ge_location,
        reasoning_engine=reasoning_engine if ge_registration == "adk" else None,
        ge_registration=ge_registration,
    )

    if not result:
        print("  ⚠ Agent deployed but GE registration failed")
        return False

    print("  ✓ Registered in Gemini Enterprise")
    _verify_ge_agent_registration(
        project_id=project_id,
        app_id=app_id,
        agent_name=f"{agent_name}_agent",
        ge_location=ge_location,
    )

    access_policy = deploy_cfg.get("ge_access_policy", "ALL_USERS")
    print(f"  ⏳ Setting gallery visibility to '{access_policy}'...")
    if _set_agent_access_policy(
        project_id=project_id,
        app_id=app_id,
        agent_name=f"{agent_name}_agent",
        access_policy=access_policy,
        ge_location=ge_location,
        display_name=display_name,
    ):
        print(f"  ✓ Gallery visibility set to '{access_policy}'")
    else:
        print("  ⚠ Could not set access policy — enable the agent manually in GE if needed")
    return True


# =============================================================================
# Deploy / Undeploy a single agent (Vertex AI Agent Engine only)
# =============================================================================

def deploy_agent(agent_name: str, dry_run: bool = False) -> bool:
    """Deploy a single agent to Vertex AI Agent Engine and register it in GE.

    Args:
        agent_name: Name matching config/<agent_name>.yaml
        dry_run: If True, just print config without deploying.

    Returns:
        True if successful, False otherwise.
    """
    try:
        config = load_agent_config(agent_name)
    except FileNotFoundError as e:
        print(f"  ✗ {e}")
        return False

    return _deploy_agent_engine(agent_name, config, dry_run=dry_run)


def _deploy_agent_engine(agent_name: str, config: dict, dry_run: bool = False) -> bool:
    """Deploy to Vertex AI Agent Engine and register in Gemini Enterprise."""
    agent_cfg = config.get("agent", {})
    deploy_cfg = config.get("deploy", {})

    model = agent_cfg.get("model", os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash"))
    display_name = agent_cfg.get("display_name", agent_name)
    description = agent_cfg.get("description", "")
    tool_paths = agent_cfg.get("tools", [])
    skills_cfg = deploy_cfg.get("skills", [])

    # Environment
    project_id = os.environ.get("PROJECT_ID")
    location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))
    storage = os.environ.get("STORAGE_BUCKET")
    api_endpoint = f"{location}-aiplatform.googleapis.com"
    api_version = deploy_cfg.get("api_version", "v1beta1")

    print(f"  ├── Model: {model}")
    print(f"  ├── Tools: {len(tool_paths)} tools")
    print(f"  ├── Skills: {len(skills_cfg)} skills defined")
    print(f"  ├── GE registration: {deploy_cfg.get('ge_registration', 'adk')}")

    if dry_run:
        print(f"  ├── Region: {location}")
        print(f"  ├── Display Name: {display_name}")
        print(f"  ├── Description: {description[:80]}...")
        print(f"  ├── Tool paths:")
        for tp in tool_paths:
            print(f"  │   - {tp}")
        print(f"  ├── Skills:")
        for s in skills_cfg:
            print(f"  │   - {s.get('name', s.get('id', '?'))}")
        print(f"  └── 🔍 DRY RUN — nothing deployed")
        return True

    # Initialize Vertex AI
    vertexai.init(
        project=project_id,
        location=location,
        api_endpoint=api_endpoint,
        staging_bucket=storage,
    )

    client = vertexai.Client(
        project=project_id,
        location=location,
        http_options=types.HttpOptions(api_version=api_version),
    )

    # Build skills from config
    skills = []
    for skill_def in skills_cfg:
        skills.append(AgentSkill(
            id=skill_def.get("id", ""),
            name=skill_def.get("name", ""),
            description=skill_def.get("description", ""),
            tags=skill_def.get("tags", []),
            examples=skill_def.get("examples", []),
        ))

    # Default I/O modes
    defaults = config.get("deploy", {})
    input_modes = defaults.get("default_input_modes", ["text/plain"])
    output_modes = defaults.get("default_output_modes", ["text/plain"])

    # Create agent card
    agent_card = create_agent_card(
        agent_name=display_name,
        description=description,
        skills=skills,
        default_input_modes=input_modes,
        default_output_modes=output_modes,
    )

    # Dynamically import the executor class
    executor_module_path = f"agents.{agent_name}.executor"
    try:
        executor_module = importlib.import_module(executor_module_path)
    except ImportError as e:
        print(f"  ✗ Could not import executor from {executor_module_path}: {e}")
        return False

    # Find the executor class (first subclass of AgentExecutor in the module)
    executor_class = None
    for attr_name in dir(executor_module):
        attr = getattr(executor_module, attr_name)
        if (
            isinstance(attr, type)
            and hasattr(attr, "AGENT_CONFIG_NAME")
            and attr_name != "BaseA2UIExecutor"
        ):
            executor_class = attr
            break

    if executor_class is None:
        print(f"  ✗ No executor class found in {executor_module_path}")
        return False

    # Create A2aAgent
    a2a_agent = A2aAgent(
        agent_card=agent_card,
        agent_executor_builder=executor_class,
    )
    a2a_agent.set_up()

    # Build requirements
    base_reqs = config.get("deploy", {}).get("base_requirements", [])
    extra_reqs = deploy_cfg.get("extra_requirements", [])
    all_requirements = base_reqs + extra_reqs

    # Build extra packages (resolve repo-root widgets path when needed).
    extra_packages = []
    for pkg in deploy_cfg.get("extra_packages", []):
        if pkg in ("widgets", "../widgets"):
            extra_packages.append(str(_REPO_ROOT / "widgets"))
        else:
            extra_packages.append(pkg)

    # Build env vars
    env_vars = deploy_cfg.get("env_vars", {})
    env_vars["PROJECT_ID"] = project_id

    # Propagate On-Behalf-Of / Workforce Identity Federation config into the
    # deployed runtime so the agent can exchange forwarded Entra tokens via STS.
    for _obo_key in (
        "WORKFORCE_POOL_ID",
        "WORKFORCE_PROVIDER_ID",
        "WORKFORCE_POOL_LOCATION",
        "WIF_SUBJECT_TOKEN_TYPE",
        "WIF_SCOPE",
        "OBO_CREDENTIAL_MODE",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "SSL_VERIFY",
    ):
        _obo_val = os.environ.get(_obo_key)
        if _obo_val:
            env_vars[_obo_key] = _obo_val

    # Deploy config
    deploy_config = {
        "display_name": f"{agent_name}_agent",
        "description": description,
        "agent_framework": deploy_cfg.get("agent_framework", "google-adk"),
        "staging_bucket": storage,
        "gcs_dir_name": agent_name,
        "requirements": all_requirements,
        "http_options": {"api_version": api_version},
        "max_instances": deploy_cfg.get("max_instances", 1),
        "extra_packages": extra_packages,
        "env_vars": env_vars,
    }

    print(f"  ⏳ Deploying to Agent Engine...")
    start_time = time.time()

    try:
        remote_agent = client.agent_engines.create(agent=a2a_agent, config=deploy_config)
    except Exception as e:
        print(f"  ✗ Deployment failed: {e}")
        return False

    elapsed = time.time() - start_time
    remote_engine_resource = remote_agent.api_resource.name
    print(f"  ✓ Deployed: {remote_engine_resource} ({elapsed:.0f}s)")

    agent_authorization = _resolve_agent_authorization(deploy_cfg, project_id, agent_name)
    return _register_deployed_agent_in_ge(
        agent_name=agent_name,
        config=config,
        remote_engine_resource=remote_engine_resource,
        agent_authorization=agent_authorization,
    )


def _register_deployed_agent_in_ge(
    *,
    agent_name: str,
    config: dict,
    remote_engine_resource: str,
    agent_authorization: str | None,
) -> bool:
    """Fetch the A2A card from a deployed Reasoning Engine and register in GE."""
    deploy_cfg = config.get("deploy", {})
    location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))
    api_endpoint = f"{location}-aiplatform.googleapis.com"
    api_version = deploy_cfg.get("api_version", "v1beta1")

    card_url = (
        f"https://{api_endpoint}/{api_version}/{remote_engine_resource}/a2a/v1/card"
    )
    bearer_token = _get_bearer_token()
    if not bearer_token:
        print("  ✗ Could not get bearer token for card fetch")
        return False

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
    }

    try:
        httpx_verify = False if _SSL_VERIFY is False else True
        response = httpx.get(card_url, headers=headers, verify=httpx_verify, timeout=60)
        response.raise_for_status()
        a2ui_agent_card_json = response.json()
    except Exception as e:
        print(f"  ✗ Could not fetch A2A card: {e}")
        return False

    built_card = build_a2ui_agent_card(agent_name)
    a2ui_agent_card_json["capabilities"] = built_card["capabilities"]

    ge_registration = deploy_cfg.get("ge_registration", "adk")
    if ge_registration == "a2a":
        a2a_rpc_url = (
            f"https://{api_endpoint}/{api_version}/{remote_engine_resource}/a2a/v1"
        )
        a2ui_agent_card_json["url"] = a2a_rpc_url
        print(f"  ✓ Agent card A2A URL: {a2a_rpc_url}")

    return _register_agent_in_gemini_enterprise(
        agent_name=agent_name,
        config=config,
        a2ui_agent_card_str=json.dumps(a2ui_agent_card_json),
        agent_authorization=agent_authorization,
        reasoning_engine=remote_engine_resource,
        ge_registration=ge_registration,
    )


def register_agent_in_ge(agent_name: str, reasoning_engine: str | None = None) -> bool:
    """Register an already-deployed Reasoning Engine agent in Gemini Enterprise."""
    try:
        config = load_agent_config(agent_name)
    except FileNotFoundError as e:
        print(f"  ✗ {e}")
        return False

    deploy_cfg = config.get("deploy", {})
    engine = (
        reasoning_engine
        or deploy_cfg.get("reasoning_engine")
        or os.environ.get("REASONING_ENGINE")
    )
    if not engine:
        print("  ✗ No Reasoning Engine resource — pass --reasoning-engine or set deploy.reasoning_engine")
        return False

    if not engine.startswith("projects/"):
        project_id = os.environ.get("PROJECT_ID")
        location = deploy_cfg.get("region", os.environ.get("LOCATION", "us-central1"))
        engine = f"projects/{_get_project_number(project_id) or project_id}/locations/{location}/reasoningEngines/{engine}"

    print(f"  ⏳ Registering Reasoning Engine in Gemini Enterprise...")
    print(f"  ├── Engine: {engine}")
    print(f"  ├── GE registration: {deploy_cfg.get('ge_registration', 'adk')}")

    project_id = os.environ.get("PROJECT_ID")
    agent_authorization = _resolve_agent_authorization(deploy_cfg, project_id, agent_name)
    return _register_deployed_agent_in_ge(
        agent_name=agent_name,
        config=config,
        remote_engine_resource=engine,
        agent_authorization=agent_authorization,
    )


def undeploy_agent(agent_name: str) -> bool:
    """Undeploy an agent from Agent Engine and unregister from Gemini Enterprise.

    Note: This unregisters from GE. To fully remove the Agent Engine resource,
    you would need the resource name. Use `gcloud` or the console for that.

    Args:
        agent_name: Name matching config/<agent_name>.yaml

    Returns:
        True if successful, False otherwise.
    """
    project_id = os.environ.get("PROJECT_ID")
    app_id = os.environ.get("GEMINI_ENTERPRISE_APP_ID")

    print(f"  ⏳ Unregistering from Gemini Enterprise...")

    ge_location = os.environ.get("GE_LOCATION", "global")
    success = _unregister_agent_from_gemini_enterprise(
        project_id=project_id,
        app_id=app_id,
        agent_name=f"{agent_name}_agent",
        ge_location=ge_location,
    )

    if success:
        print(f"  ✓ Unregistered from Gemini Enterprise")
        print(f"  ℹ To delete the Agent Engine resource, use:")
        print(f"    gcloud ai reasoning-engines list --region=us-central1")
        print(f"    gcloud ai reasoning-engines delete <RESOURCE_ID> --region=us-central1")
    else:
        print(f"  ✗ Failed to unregister from Gemini Enterprise")

    return success


# =============================================================================
# CLI
# =============================================================================

def main():
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

    parser = argparse.ArgumentParser(
        description="Agent Framework — Deploy agents to Agent Engine + Gemini Enterprise",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/deploy.py employee_verification          # Deploy one agent
  python scripts/deploy.py agent1 agent2 agent3           # Deploy multiple
  python scripts/deploy.py --all                          # Deploy all agents
  python scripts/deploy.py --list                         # List available agents
  python scripts/deploy.py employee_verification --dry-run  # Preview config
  python scripts/deploy.py employee_verification --undeploy # Undeploy agent
        """,
    )

    parser.add_argument(
        "agents",
        nargs="*",
        help="Agent name(s) to deploy (matches config/<name>.yaml)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Deploy all agents found in config/",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available agents and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deployed without actually deploying",
    )
    parser.add_argument(
        "--undeploy",
        action="store_true",
        help="Undeploy the specified agent(s) instead of deploying",
    )
    parser.add_argument(
        "--register-only",
        action="store_true",
        help="Skip Agent Engine deploy; register an existing Reasoning Engine in GE",
    )
    parser.add_argument(
        "--reasoning-engine",
        help="Reasoning Engine resource name or ID (used with --register-only)",
    )

    args = parser.parse_args()

    # --list: show available agents and exit
    if args.list:
        available = list_available_agents()
        print("=" * 60)
        print("  Agent Framework — Available Agents")
        print("=" * 60)
        if not available:
            print("  No agent configs found in config/")
        else:
            for name in available:
                try:
                    cfg = load_agent_config(name)
                    display = cfg.get("agent", {}).get("display_name", name)
                    model = cfg.get("agent", {}).get("model", "default")
                    tools = len(cfg.get("agent", {}).get("tools", []))
                    skills = len(cfg.get("deploy", {}).get("skills", []))
                    print(f"  • {name}")
                    print(f"    Display: {display}")
                    print(f"    Model: {model} | Tools: {tools} | Skills: {skills}")
                except Exception as e:
                    print(f"  • {name} (error loading config: {e})")
        print("=" * 60)
        return

    # Determine which agents to process
    if args.all:
        agent_names = list_available_agents()
        if not agent_names:
            print("✗ No agent configs found in config/")
            sys.exit(1)
    elif args.agents:
        agent_names = args.agents
    else:
        parser.print_help()
        sys.exit(1)

    # Validate all agent names first
    available = list_available_agents()
    invalid = [name for name in agent_names if name not in available]
    if invalid:
        print(f"✗ Unknown agent(s): {', '.join(invalid)}")
        print(f"  Available: {', '.join(available)}")
        sys.exit(1)

    # Header
    project_id = os.environ.get("PROJECT_ID", "?")
    location = os.environ.get("LOCATION", "us-central1")
    action = (
        "Undeploy"
        if args.undeploy
        else ("Dry Run" if args.dry_run else ("GE Registration" if args.register_only else "Deployment"))
    )

    print()
    print("=" * 80)
    print(f"  Agent Framework — {action}")
    print(f"  Project: {project_id} | Region: {location}")
    print("=" * 80)
    print()

    # Process each agent
    results = {}
    total = len(agent_names)

    for idx, agent_name in enumerate(agent_names, 1):
        print(f"[{idx}/{total}] {'Undeploying' if args.undeploy else 'Registering' if args.register_only else 'Deploying'}: {agent_name}")

        if args.undeploy:
            success = undeploy_agent(agent_name)
        elif args.register_only:
            success = register_agent_in_ge(agent_name, reasoning_engine=args.reasoning_engine)
        else:
            success = deploy_agent(agent_name, dry_run=args.dry_run)

        results[agent_name] = success
        print()

    # Summary
    succeeded = sum(1 for v in results.values() if v)
    failed = total - succeeded

    print("=" * 80)
    if failed == 0:
        print(f"  ✓ {succeeded}/{total} agents {'processed' if args.dry_run else 'completed'} successfully")
    else:
        print(f"  ⚠ {succeeded}/{total} succeeded, {failed}/{total} failed")
        for name, success in results.items():
            status = "✓" if success else "✗"
            print(f"    {status} {name}")
    print("=" * 80)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
