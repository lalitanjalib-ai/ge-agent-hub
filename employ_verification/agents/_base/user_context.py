"""
User Context — On-Behalf-Of (OBO) credential propagation for tool calls.

Gemini Enterprise forwards the logged-in user's OAuth token to this agent when
an authorization resource is configured. This module:

  1. Extracts that forwarded token from the incoming A2A request (the executor
     calls :func:`extract_user_token`), and stashes it in a ``ContextVar`` so
     that tool functions — which the LLM calls without any request object — can
     reach it.
  2. Builds ``google.oauth2.credentials.Credentials`` for BigQuery (:func:`get_user_gcp_credentials`).

Two credential modes (``OBO_CREDENTIAL_MODE`` / ``deploy.obo_credential_mode``):

  * ``wif_sts`` (default) — Entra token → Google STS → federated access token
  * ``google_direct`` — GE forwards a Google OAuth access token; use it directly (no STS)

Design notes:
  * A ``ContextVar`` is the right primitive here because ADK runs each request in
    its own asyncio task; the value set by the executor is visible to the tools
    invoked within that same turn and cannot leak across concurrent users.
  * If no user token is present (authorization disabled, or a machine-to-machine
    call), the credential helpers return ``None`` and callers fall back to ADC.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import json
import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Microsoft Entra ID token issuers. The GE-injected user token is trusted based
# on its issuer claim, not on the (undocumented, transport-dependent) state key
# it happens to be stored under.
_ENTRA_ISSUER_FRAGMENTS = ("login.microsoftonline.com", "sts.windows.net")
_GOOGLE_ISSUER_FRAGMENTS = ("accounts.google.com", "https://accounts.google.com")

OBO_MODE_WIF_STS = "wif_sts"
OBO_MODE_GOOGLE_DIRECT = "google_direct"
_VALID_OBO_MODES = frozenset({OBO_MODE_WIF_STS, OBO_MODE_GOOGLE_DIRECT})

# The raw user token forwarded by Gemini Enterprise (OAuth access token or JWT).
_user_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ge_user_token", default=None
)
_credential_mode_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "obo_credential_mode", default=None
)


# ---------------------------------------------------------------------------
# ContextVar accessors
# ---------------------------------------------------------------------------

def set_user_token(token: str | None) -> contextvars.Token:
    """Store the forwarded user token for the current execution context."""
    return _user_token_var.set(token)


def reset_user_token(token: contextvars.Token) -> None:
    """Reset the ContextVar to its previous value (call in a finally block)."""
    _user_token_var.reset(token)


def get_user_token() -> str | None:
    """Return the forwarded user token for the current execution context."""
    return _user_token_var.get()


def get_credential_mode() -> str:
    """Return the active OBO credential mode for this request/context."""
    import os

    override = _credential_mode_var.get()
    if override in _VALID_OBO_MODES:
        return override
    mode = os.environ.get("OBO_CREDENTIAL_MODE", OBO_MODE_WIF_STS).strip().lower()
    if mode not in _VALID_OBO_MODES:
        logger.warning(
            "Unknown OBO_CREDENTIAL_MODE '%s' — falling back to %s",
            mode,
            OBO_MODE_WIF_STS,
        )
        return OBO_MODE_WIF_STS
    return mode


def set_credential_mode(mode: str | None) -> contextvars.Token:
    """Override OBO credential mode for the current execution context."""
    return _credential_mode_var.set(mode)


def reset_credential_mode(token: contextvars.Token) -> None:
    """Reset the credential mode ContextVar."""
    _credential_mode_var.reset(token)


# ---------------------------------------------------------------------------
# Extraction from the A2A request
# ---------------------------------------------------------------------------

def _first_access_token_from_authorizations(authorizations: Any) -> str | None:
    """Pull an access token out of a GE ``authorizations`` mapping.

    Gemini Enterprise forwards tokens as:
        {"authorizations": {"<auth-name>": {"access_token": "..."}}}
    """
    if isinstance(authorizations, dict):
        for value in authorizations.values():
            if isinstance(value, dict) and value.get("access_token"):
                return value["access_token"]
            if isinstance(value, str) and value:
                return value
    return None


def _strip_bearer(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value or None


# ---------------------------------------------------------------------------
# Issuer-based JWT discovery
#
# Gemini Enterprise forwards the Entra token into transport-specific and
# undocumented locations (message metadata, call-context state, or ADK session
# state). Rather than depend on a specific key name, we scan candidate values
# and trust any value that is a JWT whose ``iss`` claim is a Microsoft endpoint.
# This mirrors the production-proven ge-wif-adk-bqmcp pattern.
# ---------------------------------------------------------------------------

def _decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload (claims) without verifying the signature."""
    payload_b64 = token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    raw = base64.urlsafe_b64decode(payload_b64 + padding)
    return json.loads(raw)


def _looks_like_jwt(value: object) -> bool:
    return isinstance(value, str) and value.count(".") == 2


def _looks_like_entra_jwt(value: object) -> bool:
    """Return True if ``value`` is a JWT issued by Microsoft Entra ID."""
    if not _looks_like_jwt(value):
        return False
    try:
        claims = _decode_jwt_claims(value)  # type: ignore[arg-type]
    except (ValueError, binascii.Error, json.JSONDecodeError, IndexError):
        return False
    issuer = claims.get("iss", "")
    return isinstance(issuer, str) and any(
        fragment in issuer for fragment in _ENTRA_ISSUER_FRAGMENTS
    )


def _looks_like_google_jwt(value: object) -> bool:
    """Return True if ``value`` is a JWT issued by Google (typically an id_token)."""
    if not _looks_like_jwt(value):
        return False
    try:
        claims = _decode_jwt_claims(value)  # type: ignore[arg-type]
    except (ValueError, binascii.Error, json.JSONDecodeError, IndexError):
        return False
    issuer = claims.get("iss", "")
    return isinstance(issuer, str) and any(
        fragment in issuer for fragment in _GOOGLE_ISSUER_FRAGMENTS
    )


def _looks_like_google_access_token(value: object) -> bool:
    """Heuristic for a Google OAuth2 access token (opaque or JWT)."""
    if not isinstance(value, str) or not value.strip():
        return False
    token = value.strip()
    if token.startswith("ya29."):
        return True
    if _looks_like_google_jwt(token):
        return True
    # GE usually forwards access_token strings that are not Entra JWTs.
    return not _looks_like_entra_jwt(token) and len(token) > 20


def _classify_entra_token_type(token: str) -> str:
    """Best-effort classification of a forwarded Entra JWT as 'id_token' or
    'access_token' based on its claims, so a mismatch against the configured
    ``WIF_SUBJECT_TOKEN_TYPE`` can be logged loudly instead of silently
    producing an STS 401/400.

    Heuristics (Entra v2.0 tokens):
      * id_tokens carry ``nonce`` and/or a top-level ``aud`` equal to the
        client (app) ID with no ``scp``/``roles`` claim structure typical of
        API access tokens.
      * access_tokens carry ``scp`` (delegated scopes) or ``roles`` (app
        roles) and their ``aud`` is the target API's Application ID URI
        (e.g. ``api://<APP_ID>``), not the client ID requesting the token.

    Returns 'id_token', 'access_token', or 'unknown' if it cannot tell.
    """
    try:
        claims = _decode_jwt_claims(token)
    except (ValueError, binascii.Error, json.JSONDecodeError, IndexError):
        return "unknown"

    aud = claims.get("aud", "")
    has_scp_or_roles = bool(claims.get("scp") or claims.get("roles"))
    looks_like_api_audience = isinstance(aud, str) and (
        aud.startswith("api://") or "/" in aud
    )

    if has_scp_or_roles or looks_like_api_audience:
        return "access_token"
    if "nonce" in claims:
        return "id_token"
    return "unknown"


def find_entra_token_in_mapping(state: Mapping[str, Any] | None) -> str | None:
    """Scan a mapping's values for a Microsoft-issued JWT.

    The GE-chosen state key is undocumented and irrelevant — an issuer match is
    what we trust. Nested dicts (e.g. ``{"access_token": "..."}``) are also
    inspected one level deep.
    """
    if not isinstance(state, Mapping):
        return None
    for value in state.values():
        if _looks_like_entra_jwt(value):
            return value  # type: ignore[return-value]
        if isinstance(value, Mapping):
            for inner in value.values():
                if _looks_like_entra_jwt(inner):
                    return inner  # type: ignore[return-value]
    return None


def find_google_token_in_mapping(state: Mapping[str, Any] | None) -> str | None:
    """Scan a mapping for a Google OAuth access token or Google-issued JWT."""
    if not isinstance(state, Mapping):
        return None
    for value in state.values():
        if isinstance(value, Mapping):
            access = value.get("access_token")
            if isinstance(access, str) and _looks_like_google_access_token(access):
                return access
            for inner in value.values():
                if isinstance(inner, str) and _looks_like_google_access_token(inner):
                    return inner
        elif isinstance(value, str) and _looks_like_google_access_token(value):
            return value
    return None


def find_forwarded_token_in_mapping(state: Mapping[str, Any] | None) -> str | None:
    """Issuer/token-type scan based on the active OBO credential mode."""
    if get_credential_mode() == OBO_MODE_GOOGLE_DIRECT:
        return find_google_token_in_mapping(state)
    return find_entra_token_in_mapping(state)


def extract_user_token(context: Any) -> str | None:
    """Best-effort extraction of the forwarded user token from an A2A request.

    Gemini Enterprise / Agent Engine can surface the user credential in a few
    different places depending on transport. We check all known locations, in
    priority order, and log which one matched (never the token value itself).

    Checked locations:
      1. ``message.metadata.authorizations`` (GE forwarded-token shape)
      2. ``message.metadata`` direct token keys
      3. ``call_context.state`` — ``authorizations`` / headers / token keys
      4. Issuer-based scan of metadata / state values (Microsoft-issued JWT)
    """
    # 1 & 2 — A2A message metadata (survives SDK processing)
    message = getattr(context, "message", None)
    metadata = getattr(message, "metadata", None) if message else None
    if isinstance(metadata, dict):
        token = _first_access_token_from_authorizations(metadata.get("authorizations"))
        if token:
            logger.info("OBO: user token found in message.metadata.authorizations")
            return token
        for key in ("access_token", "user_access_token", "id_token", "authorization"):
            token = _strip_bearer(metadata.get(key)) if metadata.get(key) else None
            if token:
                logger.info("OBO: user token found in message.metadata['%s']", key)
                return token

    # 3 — A2A server call context state
    call_context = getattr(context, "call_context", None)
    state = getattr(call_context, "state", None) if call_context else None
    if isinstance(state, dict):
        token = _first_access_token_from_authorizations(state.get("authorizations"))
        if token:
            logger.info("OBO: user token found in call_context.state.authorizations")
            return token
        headers = state.get("headers")
        if isinstance(headers, dict):
            token = _strip_bearer(headers.get("authorization") or headers.get("Authorization"))
            if token:
                logger.info("OBO: user token found in call_context headers")
                return token
        # GE surfaces authorizations as session-state keys prefixed by the auth name.
        for key, value in state.items():
            if isinstance(key, str) and key.startswith(("google", "auth", "entra")):
                token = value.get("access_token") if isinstance(value, dict) else _strip_bearer(value)
                if token:
                    logger.info("OBO: user token found in call_context.state['%s']", key)
                    return token

    # 4 — Issuer/token-type scan of metadata / state (transport-agnostic).
    if isinstance(metadata, dict):
        token = find_forwarded_token_in_mapping(metadata)
        if token:
            logger.info(
                "OBO: forwarded token found by scan of message.metadata (mode=%s)",
                get_credential_mode(),
            )
            return token
    if isinstance(state, dict):
        token = find_forwarded_token_in_mapping(state)
        if token:
            logger.info(
                "OBO: forwarded token found by scan of call_context.state (mode=%s)",
                get_credential_mode(),
            )
            return token

    # Nothing matched. Log the *shapes* of what we actually received (never
    # values / token contents) so a real mismatch between GE's forwarding
    # format and our extraction logic (e.g. the suspected "parts vs content"
    # protocol difference) can be diagnosed from Cloud Logging without
    # guessing. This is the #1 diagnostic gap once Conditional Access is
    # unblocked and end-to-end GE testing resumes.
    try:
        metadata_keys = list(metadata.keys()) if isinstance(metadata, dict) else None
        state_keys = list(state.keys()) if isinstance(state, dict) else None
        message_parts_types = None
        parts = getattr(message, "parts", None) if message else None
        if parts:
            message_parts_types = [type(getattr(p, "root", p)).__name__ for p in parts]
        logger.info(
            "OBO: no forwarded user token found on request — diagnostic shape: "
            "message.metadata keys=%s, call_context.state keys=%s, "
            "message.parts types=%s, has_message=%s, has_call_context=%s",
            metadata_keys,
            state_keys,
            message_parts_types,
            message is not None,
            call_context is not None,
        )
    except Exception:  # noqa: BLE001 — diagnostic logging must never break the request
        logger.info("OBO: no forwarded user token found on request (will fall back to ADC)")

    return None


# ---------------------------------------------------------------------------
# Credential construction
# ---------------------------------------------------------------------------

def _credentials_from_google_access_token(
    access_token: str,
    *,
    scopes: list[str] | None,
    quota_project_id: str | None,
):
    """Wrap a forwarded Google OAuth access token for Google client libraries."""
    from google.oauth2.credentials import Credentials

    if _looks_like_entra_jwt(access_token):
        logger.error(
            "OBO google_direct mode received an Entra JWT — check that the GE "
            "authorization resource uses Google OAuth (accounts.google.com), not Entra"
        )
        return None
    if _looks_like_google_jwt(access_token):
        logger.warning(
            "OBO google_direct mode received a Google JWT (likely id_token); "
            "BigQuery needs an OAuth access token — verify GE forwards access_token"
        )

    return Credentials(
        token=access_token,
        scopes=scopes,
        quota_project_id=quota_project_id,
    )


def get_user_gcp_credentials(
    scopes: list[str] | None = None,
    quota_project_id: str | None = None,
):
    """Build Google credentials for the current user.

    Mode ``google_direct`` uses the forwarded Google OAuth access token as-is.
    Mode ``wif_sts`` (default) exchanges an Entra token via STS/WIF.

    Returns ``None`` if no user token is present (caller should fall back to ADC).
    """
    import os

    subject_token = get_user_token()
    if not subject_token:
        return None

    quota_project_id = (
        quota_project_id
        or os.environ.get("PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )

    mode = get_credential_mode()
    if mode == OBO_MODE_GOOGLE_DIRECT:
        try:
            creds = _credentials_from_google_access_token(
                subject_token,
                scopes=scopes,
                quota_project_id=quota_project_id,
            )
            if creds is not None:
                logger.info("OBO: using forwarded Google OAuth access token directly (no STS)")
            return creds
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "OBO GOOGLE DIRECT FAILED — could not build credentials from forwarded "
                "token; caller will fall back to ADC unless STRICT_OBO is enabled: %s",
                exc,
            )
            return None

    try:
        from google.oauth2.credentials import Credentials

        from agents._base.token_exchange import get_federated_access_token

        access_token = get_federated_access_token(
            subject_token, user_project=quota_project_id
        )
        return Credentials(
            token=access_token,
            scopes=scopes,
            quota_project_id=quota_project_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface as ADC fallback, but log
        logger.error(
            "OBO STS EXCHANGE FAILED — a forwarded user token was present but "
            "could not be exchanged for a Google credential; caller will fall "
            "back to ADC (service account) unless STRICT_OBO is enabled: %s",
            exc,
        )
        return None
