"""
STS Token Exchange — Entra ID -> Google Cloud Workforce Identity (WIF).

Gemini Enterprise authenticates KPMG users against Microsoft Entra ID and
forwards the resulting user token to this agent. Entra tokens are NOT valid
Google credentials, so to call Google Cloud APIs (BigQuery, Discovery Engine,
etc.) *on behalf of the user* we must exchange the Entra token for a Google
federated access token via the Google Secure Token Service (STS).

This implements the standard, headless RFC 8693 token-exchange call against
``https://sts.googleapis.com/v1/token`` for a Workforce Pool provider.

Key facts (see the identity-federation analysis in the project docs):
  * The STS endpoint is unauthenticated/headless — no client secret, no redirect.
  * For Workforce Pools the audience is a *global* path and contains NO project
    number: ``//iam.googleapis.com/locations/global/workforcePools/POOL/providers/PROVIDER``.
  * ``subject_token_type`` must carry user identity claims. Use ``id_token`` when
    the forwarded token is an OIDC id_token; use ``jwt`` for an Entra access token.

Configuration (environment variables):
  * WORKFORCE_POOL_ID          — e.g. ``azure-oidc-agentspace-dev-app``
  * WORKFORCE_PROVIDER_ID      — the OIDC provider ID inside that pool
  * WORKFORCE_POOL_LOCATION    — defaults to ``global`` (required for workforce pools)
  * WIF_SUBJECT_TOKEN_TYPE     — ``id_token`` (default) or ``jwt`` (access token)
  * WIF_SCOPE                  — defaults to cloud-platform
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

STS_TOKEN_URL = "https://sts.googleapis.com/v1/token"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_REQUESTED_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
_DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Map the friendly config value to the RFC 8693 subject_token_type URN.
_SUBJECT_TOKEN_TYPES = {
    "id_token": "urn:ietf:params:oauth:token-type:id_token",
    "jwt": "urn:ietf:params:oauth:token-type:jwt",
    "access_token": "urn:ietf:params:oauth:token-type:access_token",
}


def _ssl_verify():
    """Resolve the SSL verification setting (mirrors the deploy/setup scripts).

    Corporate machines (Zscaler/Netskope) may need SSL_VERIFY=false or a custom
    REQUESTS_CA_BUNDLE. Returns True, False, or a path to a CA bundle.
    """
    raw = os.environ.get("SSL_VERIFY", "").strip().lower()
    if raw in ("false", "0", "no"):
        return False
    if raw:
        return raw
    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE")
    if ca_bundle:
        return ca_bundle
    return True


def build_workforce_audience(
    pool_id: str,
    provider_id: str,
    location: str = "global",
) -> str:
    """Build the Workforce Pool provider audience for the STS call.

    NOTE: workforce pool audiences use a global path and never contain the
    GCP project number. An audience mismatch is the #1 cause of 401/429s.
    """
    return (
        f"//iam.googleapis.com/locations/{location}/workforcePools/"
        f"{pool_id}/providers/{provider_id}"
    )


def exchange_entra_token_for_wif_token(
    subject_token: str,
    *,
    pool_id: str | None = None,
    provider_id: str | None = None,
    location: str | None = None,
    subject_token_type: str | None = None,
    scope: str | None = None,
    user_project: str | None = None,
    timeout: int = 30,
) -> dict:
    """Exchange an Entra ID token for a Google Workforce (WIF) access token.

    Args:
        subject_token: The user's Entra token forwarded by Gemini Enterprise.
                       Must carry user identity claims (e.g. email) so downstream
                       APIs can apply user-level access control.
        pool_id: Workforce Pool ID (defaults to $WORKFORCE_POOL_ID).
        provider_id: Workforce Pool Provider ID (defaults to $WORKFORCE_PROVIDER_ID).
        location: Pool location (defaults to $WORKFORCE_POOL_LOCATION or 'global').
        subject_token_type: 'id_token' (default), 'jwt', or 'access_token'.
        scope: OAuth scope to request (defaults to cloud-platform).
        user_project: Billing/quota project for the federated token. Workforce
                      identities have no project of their own, so STS requires a
                      ``userProject`` option (and downstream calls a matching
                      quota project) or requests fail with 403. Defaults to
                      $PROJECT_ID / $GOOGLE_CLOUD_PROJECT.

    Returns:
        The parsed STS JSON response, including ``access_token`` and ``expires_in``.

    Raises:
        ValueError: if required configuration is missing.
        requests.HTTPError: if the STS call fails (body is logged for debugging).
    """
    pool_id = pool_id or os.environ.get("WORKFORCE_POOL_ID")
    provider_id = provider_id or os.environ.get("WORKFORCE_PROVIDER_ID")
    location = location or os.environ.get("WORKFORCE_POOL_LOCATION", "global")
    scope = scope or os.environ.get("WIF_SCOPE", _DEFAULT_SCOPE)
    user_project = (
        user_project
        or os.environ.get("PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    token_type_key = (
        subject_token_type
        or os.environ.get("WIF_SUBJECT_TOKEN_TYPE", "id_token")
    ).strip().lower()

    if not pool_id or not provider_id:
        raise ValueError(
            "Workforce Identity Federation is not configured. Set "
            "WORKFORCE_POOL_ID and WORKFORCE_PROVIDER_ID (and optionally "
            "WORKFORCE_POOL_LOCATION) in the environment."
        )

    subject_token_type_urn = _SUBJECT_TOKEN_TYPES.get(token_type_key)
    if subject_token_type_urn is None:
        raise ValueError(
            f"Unsupported WIF_SUBJECT_TOKEN_TYPE '{token_type_key}'. "
            f"Use one of: {', '.join(_SUBJECT_TOKEN_TYPES)}"
        )

    audience = build_workforce_audience(pool_id, provider_id, location)

    logger.info(
        "STS exchange: audience=%s subject_token_type=%s scope=%s user_project=%s",
        audience,
        token_type_key,
        scope,
        user_project,
    )

    request_data = {
        "grant_type": _GRANT_TYPE,
        "audience": audience,
        "requested_token_type": _REQUESTED_TOKEN_TYPE,
        "subject_token": subject_token,
        "subject_token_type": subject_token_type_urn,
        "scope": scope,
    }

    # Workforce pools require a billing/quota project to be named on the exchange.
    # Without it STS-issued tokens hit 403 on the first Google API call.
    if user_project:
        request_data["options"] = json.dumps({"userProject": user_project})

    response = requests.post(
        STS_TOKEN_URL,
        data=request_data,
        timeout=timeout,
        verify=_ssl_verify(),
    )

    if response.status_code != 200:
        # Never log the token itself — only the STS error detail.
        logger.error(
            "STS token exchange failed (HTTP %s): %s",
            response.status_code,
            response.text[:1000],
        )
        response.raise_for_status()

    return response.json()


# ---------------------------------------------------------------------------
# Simple in-process cache keyed by the incoming subject token.
# Avoids hammering STS on every tool call within a single user turn.
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[str, float]] = {}
# Refresh a little early to avoid using a token that expires mid-request.
_EXPIRY_SKEW_SECONDS = 60


def get_federated_access_token(subject_token: str, **kwargs) -> str:
    """Return a cached (or freshly exchanged) Google federated access token.

    The cache key is the subject token, so each distinct user session reuses
    its own federated token until shortly before expiry.
    """
    now = time.time()
    key = subject_token

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached[1] - _EXPIRY_SKEW_SECONDS > now:
            return cached[0]

    result = exchange_entra_token_for_wif_token(subject_token, **kwargs)
    access_token = result["access_token"]
    expires_in = int(result.get("expires_in", 3600))

    with _CACHE_LOCK:
        _CACHE[key] = (access_token, now + expires_in)

    return access_token
