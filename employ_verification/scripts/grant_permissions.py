"""
Employee Verification v5 — Grant IAM Permissions Helper

Grants the minimum IAM roles needed for:
  1. The Reasoning Engine's own service account (ADC fallback path, and to
     invoke Vertex AI APIs) to run BigQuery jobs on the fallback path.
  2. The Discovery Engine (Gemini Enterprise) service agent to invoke the
     Agent Engine resource.

Usage:
    python scripts/grant_permissions.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

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
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    return resp.json().get("projectNumber") if resp.status_code == 200 else None


def _get_iam_policy(project_id: str, token: str) -> dict:
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:getIamPolicy"
    resp = requests.post(url, headers={"Authorization": f"Bearer {token}"}, json={})
    resp.raise_for_status()
    return resp.json()


def _set_iam_policy(project_id: str, token: str, policy: dict) -> None:
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:setIamPolicy"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={"policy": policy},
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


def main() -> None:
    print()
    print("=" * 80)
    print(f"  Employee Verification v5 — Grant IAM Permissions Helper")
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
        print("  [i] Note: It may take 2-3 minutes for Google Cloud to propagate the new permissions.")
    else:
        print()
        print("  [OK] No changes needed — all required bindings already present.")

    print()


if __name__ == "__main__":
    main()
