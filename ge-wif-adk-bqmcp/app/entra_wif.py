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
"""Entra ID -> Google WIF header provider for ADK tools.

Reads the Entra JWT that Gemini Enterprise injects into ADK session state,
STS-exchanges it for a Google access token bound to the workforce subject,
returns Authorization + X-Goog-User-Project headers. Cached per Entra subject
until shortly before expiry. See README for prerequisites and wiring.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests
from google.adk.agents.readonly_context import ReadonlyContext

logger = logging.getLogger(__name__)

_STS_URL = "https://sts.googleapis.com/v1/token"
_DEFAULT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_REFRESH_MARGIN_SECONDS = 60
_STS_TIMEOUT_SECONDS = 10

_token_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()


class EntraWifError(RuntimeError):
    """Base class for failures in the Entra->Google header-provider chain."""


class MissingEntraTokenError(EntraWifError):
    """No Entra-issued JWT was found in the ADK session state."""


class TokenExchangeError(EntraWifError):
    """STS token-exchange call returned a non-200 response."""


def entra_wif_header_provider(
    *,
    wif_provider: str,
    user_project: str,
    scope: str = _DEFAULT_SCOPE,
) -> Callable[[ReadonlyContext], dict[str, str]]:
    """Build an ADK `header_provider` that authenticates as the GE end-user.

    Locates the GE-injected Entra JWT in session state by scanning for any
    value whose `iss` claim is a Microsoft endpoint.
    """

    def _provider(ctx: ReadonlyContext) -> dict[str, str]:
        entra_jwt = _find_entra_token(ctx.state)
        if not entra_jwt:
            keys = _state_keys(ctx.state)
            logger.error(
                "entra_wif: no Entra JWT in session state (available keys=%r)",
                keys,
            )
            raise MissingEntraTokenError(
                f"No Entra token in session state (available keys: {keys}). "
                "Agent must be invoked from Gemini Enterprise with the "
                "linked authorization resource."
            )

        subject = _subject_of(entra_jwt)
        google_token = _get_cached_or_exchange(
            subject=subject,
            entra_jwt=entra_jwt,
            wif_provider=wif_provider,
            user_project=user_project,
            scope=scope,
        )
        return {
            "Authorization": f"Bearer {google_token}",
            "X-Goog-User-Project": user_project,
        }

    return _provider


def _get_cached_or_exchange(
    *,
    subject: str,
    entra_jwt: str,
    wif_provider: str,
    user_project: str,
    scope: str,
) -> str:
    now = time.time()
    with _cache_lock:
        cached = _token_cache.get(subject)
        if cached and cached[1] - now > _REFRESH_MARGIN_SECONDS:
            logger.debug("entra_wif: cache hit for subject=%r", subject)
            return cached[0]

    token, expires_in = _exchange_entra_for_google(
        entra_jwt=entra_jwt,
        wif_provider=wif_provider,
        user_project=user_project,
        scope=scope,
    )
    expires_at = time.time() + max(0, expires_in - _REFRESH_MARGIN_SECONDS)
    with _cache_lock:
        _token_cache[subject] = (token, expires_at)
    logger.info(
        "entra_wif: STS exchange completed for subject=%r (expires_in=%ds)",
        subject,
        expires_in,
    )
    return token


def _exchange_entra_for_google(
    *,
    entra_jwt: str,
    wif_provider: str,
    user_project: str,
    scope: str,
) -> tuple[str, int]:
    response = requests.post(
        _STS_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "audience": wif_provider,
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": scope,
            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            "subject_token": entra_jwt,
            "options": json.dumps({"userProject": user_project}),
        },
        timeout=_STS_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise TokenExchangeError(
            f"STS token-exchange failed ({response.status_code}): {response.text}"
        )
    body = response.json()
    return body["access_token"], int(body.get("expires_in", 3600))


def _find_entra_token(state: Mapping[str, Any]) -> str | None:
    """Locate the GE-injected Entra JWT in state by scanning for any value
    whose `iss` claim is a Microsoft endpoint. The state-key GE chooses is
    undocumented and irrelevant — issuer match is what we trust."""
    for value in state.values():
        if _looks_like_entra_jwt(value):
            return value  # type: ignore[return-value]
    return None


def _looks_like_entra_jwt(value: object) -> bool:
    if not isinstance(value, str) or value.count(".") != 2:
        return False
    try:
        claims = _decode_jwt_claims(value)
    except Exception:
        return False
    issuer = claims.get("iss", "")
    return (
        isinstance(issuer, str)
        and ("login.microsoftonline.com" in issuer or "sts.windows.net" in issuer)
    )


def _decode_jwt_claims(jwt: str) -> dict[str, Any]:
    _, payload_b64, _ = jwt.split(".")
    pad = "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64 + pad))


def _subject_of(jwt: str) -> str:
    """Cache key: Entra `oid` (stable across sessions), else `sub`, else raw token."""
    try:
        claims = _decode_jwt_claims(jwt)
    except Exception:
        return jwt
    return claims.get("oid") or claims.get("sub") or jwt


def _state_keys(state: Mapping[str, Any]) -> list[str]:
    try:
        return sorted(state.keys())
    except Exception:
        return []


def _reset_cache_for_tests() -> None:
    """Drop the module-level token cache. Intended for unit tests only."""
    with _cache_lock:
        _token_cache.clear()
