#!/usr/bin/env python
"""
Agent Framework — Grant IAM Permissions Helper Script

Automates granting the required IAM permissions for:
1. The Workforce Identity Federated User (to query the Reasoning Engine).
2. The Discovery Engine Service Account (to allow Gemini Enterprise to call the Reasoning Engine).
3. The AI Platform Reasoning Engine Service Agent (to query BigQuery and Discovery Engine).

Uses Application Default Credentials (ADC) to call the Cloud Resource Manager API.
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
    print("  Agent Framework — Grant IAM Permissions Helper")
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

    # Workforce Identity Federation config (drives the On-Behalf-Of bindings).
    pool_id = os.environ.get("WORKFORCE_POOL_ID", "azure-oidc-agentspace-dev-app")
    pool_location = os.environ.get("WORKFORCE_POOL_LOCATION", "global")
    user_email = os.environ.get("GE_USER_PERMISSION", "").strip()
    pool_principal_set = (
        f"principalSet://iam.googleapis.com/locations/{pool_location}/"
        f"workforcePools/{pool_id}/*"
    )
    
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

        # Reasoning Engine Service Agent permissions (used for ADC fallback)
        ("roles/bigquery.admin", re_sa),
        ("roles/discoveryengine.viewer", re_sa),

        # On-Behalf-Of: the Workforce-federated users (the whole pool) must be
        # able to invoke the agent AND read/run BigQuery jobs as themselves.
        ("roles/aiplatform.user", pool_principal_set),
        ("roles/aiplatform.viewer", pool_principal_set),
        ("roles/bigquery.dataViewer", pool_principal_set),
        ("roles/bigquery.jobUser", pool_principal_set),
        # Workforce identities have no project of their own; STS-issued tokens
        # need a billing/quota project (options.userProject / X-Goog-User-Project).
        # Without this binding the FIRST downstream Google API call returns 403.
        ("roles/serviceusage.serviceUsageConsumer", pool_principal_set),
    ]

    # Optionally scope the same BigQuery access to a single named user.
    if user_email:
        user_principal = (
            f"principal://iam.googleapis.com/locations/{pool_location}/"
            f"workforcePools/{pool_id}/subject/{user_email}"
        )
        required_bindings.extend([
            ("roles/aiplatform.user", user_principal),
            ("roles/aiplatform.viewer", user_principal),
            ("roles/bigquery.dataViewer", user_principal),
            ("roles/bigquery.jobUser", user_principal),
            ("roles/serviceusage.serviceUsageConsumer", user_principal),
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
        print("\n[OK] All required permissions are already in place!")
        sys.exit(0)
        
    # 3. Update the IAM policy
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
        print("  [✓] IAM policy updated successfully!")
        print("  [ℹ] Note: It may take 2-3 minutes for Google Cloud to propagate the new permissions.")
    else:
        print(f"  [ERROR] Failed to update IAM policy (HTTP {set_response.status_code}): {set_response.text}")
        sys.exit(1)

if __name__ == "__main__":
    main()
