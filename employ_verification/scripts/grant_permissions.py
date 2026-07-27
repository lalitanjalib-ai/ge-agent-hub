#!/usr/bin/env python
"""
emp_verify_google_oauth_v3 — Grant IAM Permissions Helper Script

Grants the least-privilege IAM permissions needed for:
1. The Discovery Engine Service Account (to allow Gemini Enterprise to call
   the Reasoning Engine).
2. The AI Platform Reasoning Engine Service Agent (ADC fallback — used only
   when no forwarded user token is present, or OBO fails).
3. The named user (GE_USER_PERMISSION) — direct Google OAuth OBO access to
   BigQuery, since v3 authenticates the user's own Cloud Identity account
   (no Workforce Identity Federation / Entra token exchange involved).

Uses Application Default Credentials (ADC) to call the Cloud Resource
Manager API.
"""

import os
import sys
import requests
import urllib3
from pathlib import Path
from dotenv import load_dotenv
from google.auth import default
from google.auth.transport.requests import Request

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _get_bearer_token() -> str | None:
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        request = Request()
        credentials.refresh(request)
        return credentials.token
    except Exception as exc:
        print(f"  [ERROR] Authentication failed: {exc}")
        print("    Please run: gcloud auth application-default login")
        return None


from scripts.setup_agent_auth import _get_project_number, _SSL_VERIFY


def _ssl_verify():
    return False if _SSL_VERIFY is False else _SSL_VERIFY


def main():
    load_dotenv(_PROJECT_ROOT / ".env", override=True)

    project_id = os.environ.get("PROJECT_ID")
    if not project_id:
        print("[ERROR] PROJECT_ID is not set in .env")
        sys.exit(1)

    print("=" * 80)
    print("  emp_verify_google_oauth_v3 — Grant IAM Permissions Helper")
    print(f"  Project: {project_id}")
    print("=" * 80)
    print()

    print("  Resolving project number...")
    project_number = _get_project_number(project_id)
    if not project_number:
        print("[ERROR] Could not resolve project number. Run: gcloud auth application-default login")
        sys.exit(1)
    print(f"  Project number: {project_number}")
    print()

    user_email = os.environ.get("GE_USER_PERMISSION", "").strip()

    token = _get_bearer_token()
    if not token:
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 1. Fetch current IAM policy
    print(f"Fetching current IAM policy for project '{project_id}'...")
    get_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:getIamPolicy"

    response = requests.post(get_url, headers=headers, json={}, verify=_ssl_verify())
    if response.status_code != 200:
        print(f"  [ERROR] Failed to fetch IAM policy (HTTP {response.status_code}): {response.text}")
        sys.exit(1)

    policy = response.json()
    bindings = policy.get("bindings", [])

    de_sa = f"serviceAccount:service-{project_number}@gcp-sa-discoveryengine.iam.gserviceaccount.com"
    re_sa = f"serviceAccount:service-{project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

    # 2. Define the required permissions to add
    # Format: (role, member)
    required_bindings = [
        # Discovery Engine Service Account permissions
        ("roles/aiplatform.user", de_sa),
        ("roles/aiplatform.viewer", de_sa),

        # Reasoning Engine Service Agent permissions (ADC fallback — used
        # only when NO forwarded user token is present, machine-to-machine
        # calls, or OBO fails). Scoped to least-privilege for what the
        # agent's tools actually do: SELECT (lookup_employee) and UPDATE
        # (verify / update_employee_field) on the employee_verification
        # dataset. Avoid roles/bigquery.admin — it grants dataset/table
        # create-delete and IAM-policy management the service account
        # never needs.
        ("roles/bigquery.dataEditor", re_sa),
        ("roles/bigquery.jobUser", re_sa),
        ("roles/discoveryengine.viewer", re_sa),
    ]

    # Named user from GE_USER_PERMISSION — direct Google OAuth OBO access.
    # v3 authenticates the user's own Cloud Identity / Google account
    # principal directly (OBO_CREDENTIAL_MODE=google_direct); there is no
    # Workforce Identity Federation pool/subject involved.
    if user_email:
        google_user = f"user:{user_email}"
        required_bindings.extend([
            ("roles/bigquery.jobUser", google_user),
            ("roles/bigquery.dataEditor", google_user),
            ("roles/serviceusage.serviceUsageConsumer", google_user),
        ])

    print("\nAnalyzing required bindings...")
    modified = False

    for role, member in required_bindings:
        # Find if role binding already exists
        role_binding = None
        for b in bindings:
            if b.get("role") == role:
                role_binding = b
                break

        if role_binding is None:
            # Create new role binding
            print(f"  [+] Adding new role binding: {role} -> {member}")
            bindings.append({
                "role": role,
                "members": [member]
            })
            modified = True
        else:
            # Check if member is already in the binding
            if member not in role_binding.get("members", []):
                print(f"  [+] Appending member to role '{role}': {member}")
                role_binding.setdefault("members", []).append(member)
                modified = True
            else:
                print(f"  [OK] Already exists: {role} -> {member}")

    if not modified:
        print("\n[OK] All required project IAM bindings are already in place!")
    else:
        print(f"\nUpdating IAM policy for project '{project_id}'...")
        set_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:setIamPolicy"
        payload = {
            "policy": {
                "bindings": bindings,
                "etag": policy.get("etag")
            }
        }

        set_response = requests.post(set_url, headers=headers, json=payload, verify=_ssl_verify())
        if set_response.status_code == 200:
            print("  [OK] Project IAM policy updated successfully!")
            print("  [i] Note: It may take 2-3 minutes for Google Cloud to propagate the new permissions.")
        else:
            print(f"  [ERROR] Failed to update IAM policy (HTTP {set_response.status_code}): {set_response.text}")
            sys.exit(1)


if __name__ == "__main__":
    main()
