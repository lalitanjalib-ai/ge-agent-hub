# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Manually verify the Entra->Google STS token exchange used by the agent.

Usage:
    uv run python scripts/verify_sts.py <entra_jwt>
    # or pipe it:
    pbpaste | uv run python scripts/verify_sts.py -

Reads `WIF_PROVIDER_RESOURCE` and `GOOGLE_CLOUD_PROJECT` from the environment
(auto-sourced from `./.env` at the repo root if present). Prints the decoded
Entra subject token claims, performs the STS exchange against the WIF
provider the agent uses, then calls the BQ MCP endpoint with the resulting
Google access token to confirm end-to-end IAM works.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import requests


def _load_dotenv() -> None:
    """Minimal .env loader so this script behaves like deploy_cloud_run.sh."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

WIF_PROVIDER_AUDIENCE = os.environ.get("WIF_PROVIDER_RESOURCE")
USER_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
if not WIF_PROVIDER_AUDIENCE or not USER_PROJECT:
    sys.exit(
        "WIF_PROVIDER_RESOURCE and GOOGLE_CLOUD_PROJECT must be set "
        "(in env or in ./.env)."
    )

STS_URL = "https://sts.googleapis.com/v1/token"
BQ_MCP_URL = "https://bigquery.googleapis.com/mcp"


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _decode_jwt_claims(jwt: str) -> dict:
    _, payload_b64, _ = jwt.split(".")
    return json.loads(_b64url_decode(payload_b64))


def _read_jwt_from_args() -> str:
    if len(sys.argv) != 2:
        sys.exit("usage: verify_sts.py <entra_jwt|->")
    arg = sys.argv[1]
    if arg == "-":
        return sys.stdin.read().strip()
    return arg.strip()


def main() -> None:
    entra_jwt = _read_jwt_from_args()

    print("=== Entra subject token claims ===")
    claims = _decode_jwt_claims(entra_jwt)
    for key in ("iss", "aud", "ver", "appid", "azp", "oid", "upn", "preferred_username", "email", "sub", "scp", "exp"):
        if key in claims:
            print(f"  {key}: {claims[key]}")

    print("\n=== STS token exchange ===")
    print(f"  audience: {WIF_PROVIDER_AUDIENCE}")
    response = requests.post(
        STS_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "audience": WIF_PROVIDER_AUDIENCE,
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": "https://www.googleapis.com/auth/cloud-platform",
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "subject_token": entra_jwt,
            "options": json.dumps({"userProject": USER_PROJECT}),
        },
        timeout=10,
    )
    print(f"  HTTP {response.status_code}")
    if response.status_code != 200:
        print(response.text)
        sys.exit(1)
    google_token = response.json()["access_token"]
    print(f"  google access token (first 40): {google_token[:40]}...")

    print("\n=== tokeninfo (who does Google think this is?) ===")
    info = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": google_token},
        timeout=10,
    )
    print(f"  HTTP {info.status_code}")
    print(json.dumps(info.json(), indent=2))

    print("\n=== BQ MCP smoke (POST initialize) ===")
    mcp_resp = requests.post(
        BQ_MCP_URL,
        headers={
            "Authorization": f"Bearer {google_token}",
            "X-Goog-User-Project": USER_PROJECT,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "verify_sts", "version": "0.1"},
            },
        },
        timeout=15,
    )
    print(f"  HTTP {mcp_resp.status_code}")
    body = mcp_resp.text
    print(body[:1500] + ("..." if len(body) > 1500 else ""))


if __name__ == "__main__":
    main()
