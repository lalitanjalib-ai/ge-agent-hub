"""
Employee Verification — Grant IAM Permissions Helper

Grants the minimum IAM roles needed for:
  1. Project-level: Reasoning Engine SA (BigQuery fallback), Discovery Engine
     SA (broad platform access).
  2. Resource-level on the deployed Reasoning Engine: Discovery Engine SA must
     have roles/aiplatform.user ON THE ENGINE ITSELF (grants
     reasoningEngines.query / execute). Project-level binding alone is not
     enough — GE gets 401 when calling the V2 ingress without this.

NOTE: this script does NOT create the WIF pool/provider itself (see README.md
for that one-time setup), but it DOES grant the resulting `principalSet` the
IAM roles it needs to run BigQuery OBO queries (roles/bigquery.dataViewer,
roles/bigquery.jobUser, roles/serviceusage.serviceUsageConsumer), derived
automatically from WIF_PROVIDER_RESOURCE in .env.

Usage:
    python scripts/grant_permissions.py
"""

from __future__ import annotations

import json
import os
import re
import sys
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

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
import requests

PROJECT_ID = os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    print("✗ PROJECT_ID is not set in .env")
    sys.exit(1)


def _bearer_token() -> str | None:
    try:
        creds, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(GoogleAuthRequest())
        return creds.token
    except Exception as e:
        print(f"  ✗ Could not get credentials: {e}")
        return None


def _get_project_number(project_id: str) -> str | None:
    token = _bearer_token()
    if not token:
        return None
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, verify=_SSL_VERIFY)
    return resp.json().get("projectNumber") if resp.status_code == 200 else None


def _get_iam_policy(project_id: str, token: str) -> dict:
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:getIamPolicy"
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json={}, verify=_SSL_VERIFY)
    resp.raise_for_status()
    return resp.json()


def _set_iam_policy(project_id: str, token: str, policy: dict) -> None:
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:setIamPolicy"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"policy": policy},
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()


def _ensure_binding(policy: dict, role: str, member: str) -> bool:
    """Adds member to role's binding, creating the binding if needed.
    Returns True if the policy was modified."""
    for binding in policy.get("bindings", []):
        if binding["role"] == role:
            if member in binding["members"]:
                return False
            binding["members"].append(member)
            return True
    policy.setdefault("bindings", []).append({"role": role, "members": [member]})
    return True


def _load_engine_resource() -> str | None:
    state_file = _PROJECT_ROOT / "scripts" / "deploy_state.json"
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text()).get("reasoning_engine")
    except (json.JSONDecodeError, OSError):
        return None


def _get_engine_iam_policy(engine_resource: str, token: str, location: str) -> dict:
    url = f"https://{location}-aiplatform.googleapis.com/v1/{engine_resource}:getIamPolicy"
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json={}, verify=_SSL_VERIFY)
    resp.raise_for_status()
    return resp.json()


def _set_engine_iam_policy(engine_resource: str, token: str, location: str, policy: dict) -> None:
    url = f"https://{location}-aiplatform.googleapis.com/v1/{engine_resource}:setIamPolicy"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"policy": policy},
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()


def _ensure_engine_invoker(
    engine_resource: str,
    token: str,
    location: str,
    discoveryengine_sa: str,
) -> bool:
    """Grant GE's service agent invoke permission on the Reasoning Engine resource."""
    print(f"Fetching IAM policy for Reasoning Engine resource...")
    print(f"  {engine_resource}")
    policy = _get_engine_iam_policy(engine_resource, token, location)
    modified = False
    for role in ("roles/aiplatform.user", "roles/aiplatform.viewer"):
        if _ensure_binding(policy, role, discoveryengine_sa):
            print(f"  [+] Adding {discoveryengine_sa} -> {role} (engine resource)")
            modified = True
        else:
            print(f"  [OK] Already exists on engine: {role} -> {discoveryengine_sa}")
    if modified:
        _set_engine_iam_policy(engine_resource, token, location, policy)
        print("  [OK] Reasoning Engine IAM policy updated!")
    return modified


def main() -> None:
    print()
    print("=" * 80)
    print(f"  Employee Verification — Grant IAM Permissions Helper")
    print(f"  Project: {PROJECT_ID}")
    print("=" * 80)
    print()

    token = _bearer_token()
    if not token:
        sys.exit(1)

    print("  Resolving project number...")
    project_number = _get_project_number(PROJECT_ID)
    if not project_number:
        print("  ✗ Could not resolve project number")
        sys.exit(1)
    print(f"  Project number: {project_number}")
    print()

    reasoning_engine_sa = f"serviceAccount:service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
    discoveryengine_sa = f"serviceAccount:service-{project_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"

    print(f"Fetching current IAM policy for project '{PROJECT_ID}'...")
    policy = _get_iam_policy(PROJECT_ID, token)

    print()
    print("Analyzing required bindings...")
    modified = False

    bindings_to_ensure = [
        ("roles/bigquery.dataEditor", reasoning_engine_sa),
        ("roles/bigquery.jobUser", reasoning_engine_sa),
        ("roles/aiplatform.user", discoveryengine_sa),
        ("roles/aiplatform.viewer", discoveryengine_sa),
        ("roles/discoveryengine.viewer", reasoning_engine_sa),
    ]

    # Workforce Identity Federation: grant the WIF principalSet (derived from
    # WIF_PROVIDER_RESOURCE) the roles it needs to run BigQuery OBO queries
    # as the end user. Without these, lookup_employee/etc. fail with:
    #   403 Caller does not have required permission to use project ...
    #   Grant the caller the roles/serviceusage.serviceUsageConsumer role
    wif_provider = os.environ.get("WIF_PROVIDER_RESOURCE", "")
    wif_pool_match = re.search(r"workforcePools/([^/]+)", wif_provider)
    if wif_pool_match:
        wif_pool_id = wif_pool_match.group(1)
        wif_principal_set = (
            f"principalSet://iam.googleapis.com/locations/global/"
            f"workforcePools/{wif_pool_id}/*"
        )
        bindings_to_ensure += [
            # dataEditor (not just dataViewer) because update_employee_field
            # and verify_employee run UPDATE DML as the end user via OBO.
            ("roles/bigquery.dataEditor", wif_principal_set),
            ("roles/bigquery.jobUser", wif_principal_set),
            ("roles/serviceusage.serviceUsageConsumer", wif_principal_set),
        ]
    else:
        print("  ⚠ WIF_PROVIDER_RESOURCE not set/unparsable — skipping WIF principalSet IAM bindings")

    for role, member in bindings_to_ensure:
        if _ensure_binding(policy, role, member):
            print(f"  [+] Adding {member} -> {role}")
            modified = True
        else:
            print(f"  [OK] Already exists: {role} -> {member}")

    if modified:
        print()
        print(f"Updating IAM policy for project '{PROJECT_ID}'...")
        _set_iam_policy(PROJECT_ID, token, policy)
        print("  [OK] Project IAM policy updated successfully!")
    else:
        print()
        print("  [OK] No project-level changes needed — all required bindings already present.")

    location = os.environ.get("LOCATION", "us-central1")
    engine_resource = _load_engine_resource()
    if engine_resource:
        print()
        print("Reasoning Engine resource-level IAM (required for GE -> V2 ingress):")
        engine_modified = _ensure_engine_invoker(engine_resource, token, location, discoveryengine_sa)
        if engine_modified:
            print("  [i] Note: It may take 2-3 minutes for Google Cloud to propagate the new permissions.")
        elif not modified:
            print("  [OK] Engine invoker bindings already present.")
    else:
        print()
        print("  [i] No reasoning_engine in deploy_state.json — skipping engine-level IAM.")

    print()


if __name__ == "__main__":
    main()
