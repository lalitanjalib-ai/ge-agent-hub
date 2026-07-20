"""
Acquire an Entra access token for manual WIF/STS testing (device code flow).

Opens a browser-less OAuth device-code login using the same client + scope as GE.
Prints the access token and optionally runs verify_wif.py / STS curl.

Usage:
    python scripts/get_entra_token.py
    python scripts/get_entra_token.py --sts-only   # skip verify_wif BigQuery test
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env", override=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Get Entra access token via device code")
    parser.add_argument(
        "--sts-only",
        action="store_true",
        help="Print token and run STS via verify_wif only (default: full verify_wif)",
    )
    args = parser.parse_args()

    try:
        import msal
    except ImportError:
        print("Installing msal...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "msal", "-q"])
        import msal

    tenant = os.environ.get("TENANT_ID", "").strip()
    client_id = os.environ.get("OAUTH_CLIENT_ID", "").strip()
    scopes_raw = os.environ.get(
        "OAUTH_SCOPES",
        "openid offline_access api://49818a2b-fed8-448b-a477-47c8658ba9ea/GRead",
    ).strip('"')

    if not tenant or not client_id:
        sys.exit("Set TENANT_ID and OAUTH_CLIENT_ID in .env")

    # MSAL wants scope list without openid/offline for v2 sometimes — pass API scope
    scopes = [s for s in scopes_raw.split() if not s.startswith("openid")]

    authority = f"https://login.microsoftonline.com/{tenant}"
    app = msal.PublicClientApplication(client_id, authority=authority)

    print("=== Entra device code login ===")
    print(f"  Tenant:   {tenant}")
    print(f"  Client:   {client_id}")
    print(f"  Scopes:   {scopes}")
    print()

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        sys.exit(f"Device flow failed: {flow}")

    print(flow["message"])
    print()
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        err = result.get("error_description") or result.get("error") or result
        sys.exit(f"Token acquisition failed: {err}")

    token = result["access_token"]
    print("\n=== Access token acquired ===")
    print("Copy this for STS testing (treat as a secret — do not commit or share):")
    print()
    print(token)
    print()

    # Save for curl in same shell session hint
    token_file = _PROJECT_ROOT / ".entra_token.tmp"
    token_file.write_text(token, encoding="utf-8")
    print(f"Also saved to: {token_file}")
    print("PowerShell: $env:AZURE_ACCESS_TOKEN = Get-Content .entra_token.tmp -Raw")
    print()

    verify_script = _PROJECT_ROOT / "scripts" / "verify_wif.py"
    if verify_script.exists():
        print("=== Running verify_wif.py (Entra -> STS -> BigQuery) ===")
        proc = subprocess.run(
            [sys.executable, str(verify_script), token],
            cwd=str(_PROJECT_ROOT),
        )
        sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
