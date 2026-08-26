"""Entra ID -> Google Cloud token exchange (RFC 8693 STS via Workforce Identity
Federation).

Gemini Enterprise forwards the end user's Microsoft Entra ID JWT (issuer
`login.microsoftonline.com` / `sts.windows.net`) on the `Authorization`
header. BigQuery does not accept that JWT directly, so we exchange it for a
short-lived Google Cloud federated access token via the Security Token
Service, using the Workforce Identity Federation provider configured for
this project's Entra tenant.
"""

from __future__ import annotations

import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

_STS_ENDPOINT = "https://sts.googleapis.com/v1/token"
_STS_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/bigquery"
)

# token_hash -> (google_access_token, expiry_epoch_seconds)
_TOKEN_CACHE: dict[int, tuple[str, float]] = {}


class TokenExchangeError(RuntimeError):
    """Raised when the Entra JWT could not be exchanged for a Google token."""


def exchange_entra_token_for_google_token(
    entra_jwt: str,
    wif_provider: str,
    user_project: str,
) -> str:
    """Exchange an Entra JWT for a federated Google Cloud access token.

    Args:
        entra_jwt: The raw Microsoft Entra ID JWT forwarded by Gemini
            Enterprise on the Authorization header.
        wif_provider: Full WIF provider resource, e.g.
            "//iam.googleapis.com/locations/global/workforcePools/<POOL>/providers/<PROVIDER>".
        user_project: GCP project ID to bill/attribute the exchange to.

    Returns:
        A short-lived Google Cloud OAuth access token.

    Raises:
        TokenExchangeError: if the STS endpoint rejects the exchange.
    """
    cache_key = hash(entra_jwt)
    cached = _TOKEN_CACHE.get(cache_key)
    if cached:
        token, expiry = cached
        if time.time() < expiry - 60:
            return token

    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "audience": wif_provider,
        "scope": _STS_SCOPES,
        "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "subject_token": entra_jwt,
        "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
        "options": json.dumps({"userProject": user_project}),
    }

    resp = requests.post(_STS_ENDPOINT, data=payload, timeout=10)
    if resp.status_code != 200:
        logger.error("STS token exchange failed (HTTP %s): %s", resp.status_code, resp.text)
        raise TokenExchangeError(f"STS token exchange failed: {resp.text}")

    data = resp.json()
    google_token = data["access_token"]
    expires_in = data.get("expires_in", 3600)
    _TOKEN_CACHE[cache_key] = (google_token, time.time() + expires_in)
    return google_token
