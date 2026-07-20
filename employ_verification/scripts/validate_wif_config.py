"""Pre-flight validation for Entra -> WIF -> OBO configuration.

Checks .env alignment before running setup_agent_auth / deploy. Does NOT call
Google Cloud APIs (safe to run offline).

Usage:
    python scripts/validate_wif_config.py
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

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "token_exchange",
    _PROJECT_ROOT / "agents" / "_base" / "token_exchange.py",
)
_token_exchange = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_token_exchange)
build_workforce_audience = _token_exchange.build_workforce_audience

# Known OIDC clientId on azure-dev-oidc-provider (override via .env if pool changes).
_WIF_PROVIDER_OIDC_CLIENT_ID = os.environ.get(
    "WIF_PROVIDER_OIDC_CLIENT_ID", "49818a2b-fed8-448b-a477-47c8658ba9ea"
)


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def main() -> None:
    print("=" * 70)
    print("  WIF / OBO configuration validation")
    print("=" * 70)

    errors = 0

    pool_id = os.environ.get("WORKFORCE_POOL_ID", "")
    provider_id = os.environ.get("WORKFORCE_PROVIDER_ID", "")
    pool_location = os.environ.get("WORKFORCE_POOL_LOCATION", "global")
    oauth_client_id = os.environ.get("OAUTH_CLIENT_ID", "")
    oauth_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
    oauth_scopes = os.environ.get("OAUTH_SCOPES", "")
    agent_auth = os.environ.get("AGENT_AUTHORIZATION", "")
    subject_type = os.environ.get("WIF_SUBJECT_TOKEN_TYPE", "jwt")
    user_email = os.environ.get("GE_USER_PERMISSION", "")

    if not pool_id:
        _fail("WORKFORCE_POOL_ID is not set")
        errors += 1
    else:
        _ok(f"WORKFORCE_POOL_ID={pool_id}")

    if not provider_id or provider_id == "CHANGE_ME":
        _fail("WORKFORCE_PROVIDER_ID is not set (still CHANGE_ME?)")
        errors += 1
    else:
        _ok(f"WORKFORCE_PROVIDER_ID={provider_id}")

    if provider_id and pool_id:
        audience = build_workforce_audience(pool_id, provider_id, pool_location)
        _ok(f"STS audience: {audience}")

    if not oauth_client_id:
        _fail("OAUTH_CLIENT_ID is not set")
        errors += 1
    elif oauth_client_id != _WIF_PROVIDER_OIDC_CLIENT_ID:
        _warn(
            f"OAUTH_CLIENT_ID={oauth_client_id} does not match WIF provider "
            f"oidc.clientId ({_WIF_PROVIDER_OIDC_CLIENT_ID}). "
            "GE login may work, but STS/OBO will 401 until identity adds a "
            "shadow provider or allowed-audience for this app."
        )
    else:
        _ok(f"OAUTH_CLIENT_ID matches WIF provider ({oauth_client_id})")

    api_scope = f"api://{oauth_client_id}/access_as_user"
    if api_scope in oauth_scopes:
        _ok(f"OAUTH_SCOPES includes {api_scope} (recommended for WIF/OBO)")
    elif "user.Read" in oauth_scopes or "graph.microsoft.com" in oauth_scopes.lower():
        _warn(
            "OAUTH_SCOPES uses Microsoft Graph (e.g. user.Read). "
            "GE can authenticate users, but WIF STS exchange typically needs "
            f"{api_scope} unless the provider allows Graph token audiences."
        )
    else:
        _warn(f"OAUTH_SCOPES may not produce a token WIF accepts: {oauth_scopes!r}")

    if not oauth_secret or oauth_secret == "CHANGE_ME":
        _fail(
            "OAUTH_CLIENT_SECRET is missing — set the secret for Entra app "
            f"{oauth_client_id} in Azure Portal before running setup_agent_auth.py"
        )
        errors += 1
    else:
        _ok("OAUTH_CLIENT_SECRET is set")

    if subject_type != "jwt":
        _warn(f"WIF_SUBJECT_TOKEN_TYPE={subject_type!r} — use 'jwt' for Entra access tokens")
    else:
        _ok("WIF_SUBJECT_TOKEN_TYPE=jwt")

    if not agent_auth or agent_auth.strip().lower() in ("none", ""):
        _warn(
            "AGENT_AUTHORIZATION=none — GE will NOT forward user tokens. "
            "Run setup_agent_auth.py then redeploy."
        )
    else:
        _ok(f"AGENT_AUTHORIZATION={agent_auth}")

    if user_email:
        _ok(f"GE_USER_PERMISSION={user_email} (workforce subject for IAM grants)")
        _warn(
            "azure-dev-oidc-provider maps google.subject from preferred_username, "
            "not email — ensure GE_USER_PERMISSION matches your Entra UPN exactly."
        )

    print()
    if errors:
        print(f"Validation FAILED with {errors} error(s). Fix .env before deploying.")
        sys.exit(1)
    print("Validation passed. Next:")
    print("  python scripts/grant_permissions.py")
    print("  python scripts/setup_agent_auth.py --id auth-employee-verification")
    print("  python scripts/deploy.py employee_verification")


if __name__ == "__main__":
    main()
