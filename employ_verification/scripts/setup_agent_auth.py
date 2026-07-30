"""
Employee Verification v5 — Agent Authorization Setup Script

Creates (or verifies) the Gemini Enterprise agent_authorization resource
needed for OAuth propagation. Normally handled automatically by
scripts/deploy.py on first deploy — run this manually only when you need to
force-recreate it.

The authorization resource allows Gemini Enterprise to run the OAuth consent
flow and mint the end-user token that Agent Engine's V2 ingress later
propagates to this agent's Authorization header.

Usage:
    python scripts/setup_agent_auth.py
    python scripts/setup_agent_auth.py --check
    python scripts/setup_agent_auth.py --force
    python scripts/setup_agent_auth.py --id my-auth-id

Prerequisites:
    1. Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET in .env.
    2. The OAuth client must have these redirect URIs configured:
         https://vertexaisearch.cloud.google.com/oauth-redirect
         https://vertexaisearch.cloud.google.com/static/oauth/oauth.html
    3. Set GE_LOCATION in .env to match where your GE app was provisioned.
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env", override=True)

import requests
from google.auth import default
from google.auth.transport.requests import Request

_SSL_VERIFY: bool | str = True
_ssl_verify_env = os.environ.get("SSL_VERIFY", "").strip().lower()
if _ssl_verify_env in ("false", "0", "no"):
    _SSL_VERIFY = False
elif _ssl_verify_env:
    _SSL_VERIFY = _ssl_verify_env
elif os.environ.get("REQUESTS_CA_BUNDLE"):
    _SSL_VERIFY = os.environ.get("REQUESTS_CA_BUNDLE")


def _auth_location() -> str:
    loc = os.environ.get("GE_LOCATION", "global").strip().lower()
    return loc if loc in ("global", "us", "eu") else "global"


def _auth_de_hostname(auth_location: str) -> str:
    if auth_location == "global":
        return "discoveryengine.googleapis.com"
    return f"{auth_location}-discoveryengine.googleapis.com"


def _get_bearer_token() -> str | None:
    try:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        return credentials.token
    except Exception as e:
        print(f"  ✗ Error getting credentials: {e}")
        print("    Please run: gcloud auth application-default login")
        return None


def _get_project_number(project_id: str) -> str | None:
    bearer_token = _get_bearer_token()
    if not bearer_token:
        return None
    url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    response = requests.get(url, headers=headers, verify=_SSL_VERIFY)
    if response.status_code == 200:
        return response.json().get("projectNumber")
    print(f"  ✗ Could not resolve project number for '{project_id}': {response.text}")
    return None


def _check_auth_exists(project_number: str, auth_id: str, project_id: str) -> dict | None:
    auth_location = _auth_location()
    auth_hostname = _auth_de_hostname(auth_location)
    url = (
        f"https://{auth_hostname}/v1alpha/projects/{project_number}/"
        f"locations/{auth_location}/authorizations/{auth_id}"
    )
    bearer_token = _get_bearer_token()
    if not bearer_token:
        return None
    headers = {"Authorization": f"Bearer {bearer_token}", "X-Goog-User-Project": project_id}
    response = requests.get(url, headers=headers, verify=_SSL_VERIFY)
    return response.json() if response.status_code == 200 else None


def _delete_auth(project_number: str, auth_id: str, project_id: str) -> bool:
    auth_location = _auth_location()
    auth_hostname = _auth_de_hostname(auth_location)
    url = (
        f"https://{auth_hostname}/v1alpha/projects/{project_number}/"
        f"locations/{auth_location}/authorizations/{auth_id}"
    )
    bearer_token = _get_bearer_token()
    if not bearer_token:
        return False
    headers = {"Authorization": f"Bearer {bearer_token}", "X-Goog-User-Project": project_id}
    response = requests.delete(url, headers=headers, verify=_SSL_VERIFY)
    return response.status_code in (200, 204, 404)


def _create_auth(
    project_number: str,
    auth_id: str,
    project_id: str,
    oauth_client_id: str,
    oauth_client_secret: str,
) -> dict | None:
    auth_location = _auth_location()
    auth_hostname = _auth_de_hostname(auth_location)
    url = (
        f"https://{auth_hostname}/v1alpha/projects/{project_number}/"
        f"locations/{auth_location}/authorizations?authorizationId={auth_id}"
    )

    auth_base_uri = os.environ.get(
        "OAUTH_AUTHORIZATION_URI", "https://accounts.google.com/o/oauth2/v2/auth"
    ).strip('"')
    token_uri = os.environ.get("OAUTH_TOKEN_URI", "https://oauth2.googleapis.com/token").strip('"')
    scopes = os.environ.get(
        "OAUTH_SCOPES",
        "openid https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile "
        "https://www.googleapis.com/auth/bigquery "
        "https://www.googleapis.com/auth/cloud-platform",
    ).strip('"')

    redirect_uri = "https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fstatic%2Foauth%2Foauth.html"
    encoded_scopes = urllib.parse.quote(scopes)

    authorization_uri = (
        f"{auth_base_uri}"
        f"?client_id={oauth_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={encoded_scopes}"
        f"&include_granted_scopes=true"
        f"&response_type=code"
        f"&access_type=offline"
        f"&prompt=consent"
    )

    payload = {
        "serverSideOauth2": {
            "clientId": oauth_client_id,
            "clientSecret": oauth_client_secret,
            "tokenUri": token_uri,
            "authorizationUri": authorization_uri,
        }
    }

    bearer_token = _get_bearer_token()
    if not bearer_token:
        return None
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": project_id,
    }
    response = requests.post(url, headers=headers, json=payload, verify=_SSL_VERIFY)
    if response.status_code == 200:
        return response.json()
    print(f"  ✗ Failed to create authorization (HTTP {response.status_code}):")
    print(f"    {response.text}")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/verify the GE agent_authorization resource")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--id", default="auth-emp-verify-v5", dest="auth_id")
    args = parser.parse_args()

    project_id = os.environ.get("PROJECT_ID")
    oauth_client_id = os.environ.get("OAUTH_CLIENT_ID")
    oauth_client_secret = os.environ.get("OAUTH_CLIENT_SECRET")

    print()
    print("=" * 70)
    print("  Employee Verification v5 — Agent Authorization Setup")
    print("=" * 70)
    print(f"  Project ID:  {project_id}")
    print(f"  GE Location: {os.environ.get('GE_LOCATION', 'global')}")
    print(f"  Auth ID:     {args.auth_id}")
    print("=" * 70)
    print()

    if not project_id:
        print("✗ PROJECT_ID is not set in .env")
        sys.exit(1)

    project_number = _get_project_number(project_id)
    if not project_number:
        sys.exit(1)
    print(f"  ✓ Project number: {project_number}")

    existing = _check_auth_exists(project_number, args.auth_id, project_id)
    if existing:
        print(f"  ✓ Authorization resource already exists: {existing.get('name')}")
        if args.check:
            sys.exit(0)
        if not args.force:
            print("  ℹ Already exists — skipping. Use --force to recreate.")
            sys.exit(0)
        print("  ⏳ --force: deleting existing authorization...")
        if not _delete_auth(project_number, args.auth_id, project_id):
            print("  ✗ Failed to delete existing authorization")
            sys.exit(1)
    elif args.check:
        print("  ✗ Authorization resource not found.")
        sys.exit(1)

    if not oauth_client_id or not oauth_client_secret:
        print("✗ OAUTH_CLIENT_ID / OAUTH_CLIENT_SECRET not set in .env")
        sys.exit(1)

    print(f"  ⏳ Creating authorization resource '{args.auth_id}'...")
    result = _create_auth(project_number, args.auth_id, project_id, oauth_client_id, oauth_client_secret)
    if not result:
        sys.exit(1)

    print()
    print(f"  ✓ Authorization resource created: {result.get('name')}")
    print()


if __name__ == "__main__":
    main()
