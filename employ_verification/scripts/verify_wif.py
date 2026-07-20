"""Manually verify the Entra -> Google STS token exchange used by the agent.

This exercises the agent's OWN token-exchange code path (agents/_base/token_exchange)
so what you validate here is exactly what runs in production. It:

  1. Decodes and prints the forwarded Entra subject token's claims (iss / aud / etc.).
  2. Performs the STS exchange against the configured Workforce Pool provider.
  3. Calls Google's tokeninfo endpoint (who does Google think this is?).
  4. Runs a test BigQuery query as the user against the employee table.

Usage:
    python scripts/verify_wif.py <entra_jwt>
    # or pipe it (PowerShell):
    Get-Clipboard | python scripts/verify_wif.py -

Reads WORKFORCE_POOL_ID / WORKFORCE_PROVIDER_ID / WORKFORCE_POOL_LOCATION /
WIF_SUBJECT_TOKEN_TYPE / WIF_SCOPE / PROJECT_ID from the environment (auto-loaded
from ./.env). Get a raw Entra token from your tenant with a scope of
api://<APP_ID>/access_as_user so its audience matches the WIF provider.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path and load environment variables early
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env", override=True)

import requests

from agents._base.token_exchange import (
    _ssl_verify,
    build_workforce_audience,
    exchange_entra_token_for_wif_token,
)

DATASET_ID = "employee_verification"
TABLE_ID = "employee_records"


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _decode_jwt_claims(jwt: str) -> dict:
    _, payload_b64, _ = jwt.split(".")
    return json.loads(_b64url_decode(payload_b64))


def _read_jwt_from_args() -> str:
    if len(sys.argv) != 2:
        sys.exit("usage: verify_wif.py <entra_jwt|->")
    arg = sys.argv[1]
    if arg == "-":
        return sys.stdin.read().strip()
    return arg.strip()


def main() -> None:
    project_id = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    pool_id = os.environ.get("WORKFORCE_POOL_ID")
    provider_id = os.environ.get("WORKFORCE_PROVIDER_ID")
    location = os.environ.get("WORKFORCE_POOL_LOCATION", "global")

    if not project_id:
        sys.exit("PROJECT_ID (or GOOGLE_CLOUD_PROJECT) must be set in env or ./.env")
    if not pool_id or not provider_id or provider_id == "CHANGE_ME":
        sys.exit(
            "WORKFORCE_POOL_ID and WORKFORCE_PROVIDER_ID must be set in ./.env "
            "(WORKFORCE_PROVIDER_ID is still 'CHANGE_ME')."
        )

    entra_jwt = _read_jwt_from_args()
    verify = _ssl_verify()

    print("=== Entra subject token claims ===")
    try:
        claims = _decode_jwt_claims(entra_jwt)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"Could not decode the provided token as a JWT: {exc}")
    for key in (
        "iss", "aud", "ver", "appid", "azp", "oid", "upn",
        "preferred_username", "email", "sub", "scp", "exp",
    ):
        if key in claims:
            print(f"  {key}: {claims[key]}")

    print("\n=== STS token exchange ===")
    audience = build_workforce_audience(pool_id, provider_id, location)
    print(f"  audience: {audience}")
    print(f"  user_project: {project_id}")
    try:
        result = exchange_entra_token_for_wif_token(entra_jwt, user_project=project_id)
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        print(f"  FAILED: HTTP {getattr(exc.response, 'status_code', '?')}")
        print(f"  {body[:1500]}")
        sys.exit(1)
    google_token = result["access_token"]
    print(f"  OK — google access token (first 40): {google_token[:40]}...")

    print("\n=== tokeninfo (who does Google think this is?) ===")
    info = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": google_token},
        timeout=10,
        verify=verify,
    )
    print(f"  HTTP {info.status_code}")
    try:
        print(json.dumps(info.json(), indent=2))
    except Exception:  # noqa: BLE001
        print(info.text[:1000])

    print("\n=== BigQuery smoke test (query AS THE USER) ===")
    try:
        from google.cloud import bigquery
        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=google_token,
            scopes=["https://www.googleapis.com/auth/bigquery"],
            quota_project_id=project_id,
        )
        client = bigquery.Client(project=project_id, credentials=creds)
        full_table = f"{project_id}.{DATASET_ID}.{TABLE_ID}"
        query = f"SELECT COUNT(*) AS n FROM `{full_table}`"
        print(f"  query: {query}")
        rows = list(client.query(query).result())
        print(f"  OK — row count: {rows[0]['n']}")
        print("\nSUCCESS: end-to-end Entra -> WIF -> BigQuery works as the user.")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        print(
            "\n  If this is a 403, the workforce principal likely needs "
            "roles/serviceusage.serviceUsageConsumer and BigQuery roles. "
            "Run: python scripts/grant_permissions.py"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
