"""Verify a forwarded Google OAuth access token can query BigQuery (no STS).

Usage:
    python scripts/verify_google_oauth.py <google_access_token>
    gcloud auth print-access-token | python scripts/verify_google_oauth.py -

This exercises the same direct-OBO path as emp_verify_google_oauth_v3:
  Google access token → bigquery.Client(credentials=...) → test query

Get a token:
  gcloud auth application-default login
  gcloud auth print-access-token
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

import requests
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from agents._base.exceptions import OBOAuthError
from agents._base.user_context import _credentials_from_google_access_token


DATASET_ID = "employee_verification"
TABLE_ID = "employee_records"


def _read_token_from_args() -> str:
    if len(sys.argv) != 2:
        sys.exit("usage: verify_google_oauth.py <google_access_token|->")
    arg = sys.argv[1]
    if arg == "-":
        return sys.stdin.read().strip()
    return arg.strip()


def main() -> None:
    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        sys.exit("PROJECT_ID must be set in ./.env")

    token = _read_token_from_args()
    if not token:
        sys.exit("empty token")

    print("Step 1 — tokeninfo (who does Google think this is?)")
    resp = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": token},
        timeout=30,
    )
    if resp.status_code != 200:
        sys.exit(f"tokeninfo failed ({resp.status_code}): {resp.text[:500]}")
    info = resp.json()
    print(f"  email: {info.get('email')}")
    print(f"  scope: {info.get('scope')}")
    print(f"  expires_in: {info.get('expires_in')}")

    print("\nStep 2 — build credentials (google_direct OBO path)")
    try:
        creds = _credentials_from_google_access_token(
            token,
            scopes=["https://www.googleapis.com/auth/bigquery"],
            quota_project_id=project_id,
        )
    except OBOAuthError as e:
        sys.exit(
            f"OBOAuthError — this token cannot be used for BigQuery OBO: {e}\n\n"
            "This is the exact failure mode reported in production: GE forwarded "
            "an id_token (or a wrong-IdP JWT) instead of an OAuth access_token. "
            "Fix the GE authorization resource's OAuth scopes/consent — see "
            "scripts/setup_agent_auth.py — a client-side token exchange is not "
            "possible for this case."
        )


    print("\nStep 3 — BigQuery test query as user")
    table = f"{project_id}.{DATASET_ID}.{TABLE_ID}"
    client = bigquery.Client(project=project_id, credentials=creds)
    rows = list(
        client.query(f"SELECT employee_id, name FROM `{table}` LIMIT 3").result()
    )
    print(f"  OK — returned {len(rows)} row(s)")
    for row in rows:
        print(f"    {row.employee_id}: {row.name}")


if __name__ == "__main__":
    main()
