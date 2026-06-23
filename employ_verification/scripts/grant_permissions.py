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
    
    response = requests.post(get_url, headers=headers, json={}, verify=False)
    if response.status_code != 200:
        print(f"  [ERROR] Failed to fetch IAM policy (HTTP {response.status_code}): {response.text}")
        sys.exit(1)
        
    policy = response.json()
    bindings = policy.get("bindings", [])
    
    # 2. Define the required permissions to add
    # Format: (role, member)
    required_bindings = [
        # Workforce Identity Federated User permissions
        ("roles/aiplatform.user", "principal://iam.googleapis.com/locations/global/workforcePools/azure-oidc-agentspace-dev-app/subject/pengwang3@kpmg.com"),
        ("roles/aiplatform.viewer", "principal://iam.googleapis.com/locations/global/workforcePools/azure-oidc-agentspace-dev-app/subject/pengwang3@kpmg.com"),
        
        # Discovery Engine Service Account permissions
        ("roles/aiplatform.user", "serviceAccount:service-901535160018@gcp-sa-discoveryengine.iam.gserviceaccount.com"),
        ("roles/aiplatform.viewer", "serviceAccount:service-901535160018@gcp-sa-discoveryengine.iam.gserviceaccount.com"),
        
        # Reasoning Engine Service Agent permissions
        ("roles/bigquery.admin", "serviceAccount:service-901535160018@gcp-sa-aiplatform-re.iam.gserviceaccount.com"),
        ("roles/discoveryengine.viewer", "serviceAccount:service-901535160018@gcp-sa-aiplatform-re.iam.gserviceaccount.com"),
    ]
    
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
    
    set_response = requests.post(set_url, headers=headers, json=payload, verify=False)
    if set_response.status_code == 200:
        print("  [✓] IAM policy updated successfully!")
        print("  [ℹ] Note: It may take 2-3 minutes for Google Cloud to propagate the new permissions.")
    else:
        print(f"  [ERROR] Failed to update IAM policy (HTTP {set_response.status_code}): {set_response.text}")
        sys.exit(1)

if __name__ == "__main__":
    main()
