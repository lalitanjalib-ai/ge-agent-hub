"""
Shared BigQuery client factory with On-Behalf-Of (OBO) support — BYOC edition.

Every employee tool goes through :func:`get_bigquery_client` so that queries run
in the identity of the logged-in Gemini Enterprise user whenever a propagated
OAuth access token is available on this request:

  * If the token is present, the BigQuery client authenticates as that user
    directly (Google OAuth access token, no STS/WIF exchange needed since GE
    is registered with a Google OAuth authorization resource). This enforces
    user-level access control (row/column ACLs, dataset permissions) and
    produces user-attributed Cloud Audit Logs.

  * Otherwise (no propagated token — e.g. the project is not yet on the
    OAuth-propagation allowlist, or this is a direct/non-GE invocation), the
    client falls back to Application Default Credentials (the Reasoning
    Engine's own service account), and this is logged clearly so the gap is
    never silently invisible.

No MCP (Model Context Protocol) server is used or needed here. This module
uses the standard `google-cloud-bigquery` Python client, which talks directly
to the BigQuery REST API (`bigquery.googleapis.com`) via a plain
`google.auth.credentials.Credentials` object — the same mechanism any
Google Cloud client library uses. MCP is a distinct, separate protocol used
by some other reference implementations (e.g. a BigQuery MCP server) but is
not part of this agent's dependency chain.

How the forwarded token is actually applied (verified against the installed
google-auth / google-cloud-bigquery source):
  1. `Credentials(token=token, scopes=[...])` wraps the raw OAuth token
     string in a real `google.auth.credentials.Credentials` object with no
     `expiry` set.
  2. `bigquery.Client(project=..., credentials=creds)` wires those
     credentials into an internal `AuthorizedSession`.
  3. On every BigQuery REST call, `AuthorizedSession.request()` calls
     `credentials.before_request(...)`, which only calls `.refresh()` if
     `.valid` is False. Since `expiry` is unset, `.valid` is always True and
     `.refresh()` is never invoked — the flow goes straight to `.apply()`,
     which sets `headers["authorization"] = f"Bearer {token}"` verbatim.
  4. If the *real* forwarded token has actually expired, BigQuery's server
     will reject the request with a 401. `AuthorizedSession` will then try
     to call `.refresh()` as a retry — but since we never populate
     `refresh_token`/`client_id`/`client_secret` (we don't have them; GE
     only forwards a bare access token), that refresh attempt itself fails
     with `google.auth.exceptions.RefreshError`. We catch that specific
     error below and translate it into a clear, user-facing message instead
     of letting a raw library stack trace leak into the tool's JSON output.
"""

from __future__ import annotations

import logging
import os

from google.auth.exceptions import RefreshError
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from employee_agent.token_context import get_user_token

logger = logging.getLogger(__name__)

# NOTE: No hardcoded fallback project — fail loudly instead of silently
# querying the wrong GCP project if PROJECT_ID is ever missing.
PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise RuntimeError(
        "PROJECT_ID (or GOOGLE_CLOUD_PROJECT) is not set in the environment. "
        "Set PROJECT_ID explicitly in .env or the Agent Engine env_vars."
    )

_BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]

# When true, an empty propagated token raises instead of silently falling
# back to ADC. Use this to validate OBO end-to-end without ambiguity.
_STRICT_OBO = os.environ.get("STRICT_OBO", "").strip().lower() in ("true", "1", "yes")


class OBOSessionExpiredError(RuntimeError):
    """Raised when the propagated end-user token has expired and cannot be
    refreshed client-side (we only ever hold a bare access token, never a
    refresh_token). Tool functions catch this and surface a clear,
    non-technical message asking the user to retry."""


def get_bigquery_client() -> bigquery.Client:
    """Return a BigQuery client for the current user (OBO) or ADC fallback."""
    token = get_user_token()

    if token:
        logger.info(
            "OBO: propagated OAuth token present — BigQuery calls will run "
            "as the end user (no STS/WIF exchange, direct Google OAuth)."
        )
        user_credentials = Credentials(token=token, scopes=_BQ_SCOPES)
        return bigquery.Client(project=PROJECT_ID, credentials=user_credentials)

    msg = (
        "OBO: no propagated end-user OAuth token found on this request "
        "(Authorization header was empty/absent). Falling back to "
        "Application Default Credentials (the Reasoning Engine service "
        "account). If this is unexpected, confirm: (1) the hosting project "
        "is on the OAuth-propagation allowlist, (2) the agent is registered "
        "with Gemini Enterprise at the Agent Engine V2 '/api/...' ingress "
        "URL (not the legacy '/a2a/v1' URL), and (3) the GE authorization "
        "resource is correctly linked to this agent."
    )
    if _STRICT_OBO:
        raise RuntimeError(msg)
    logger.warning(msg)
    return bigquery.Client(project=PROJECT_ID)


def run_query(client: bigquery.Client, query: str, job_config=None):
    """Execute a BigQuery query, translating an expired/invalid forwarded
    token into a clear OBOSessionExpiredError instead of letting a raw
    google.auth.exceptions.RefreshError (or the resulting 401 from
    BigQuery) propagate as an opaque stack trace to the caller.

    All employee tools should call BigQuery through this helper (rather than
    calling client.query(...).result() directly) so this translation is
    applied consistently everywhere OBO credentials are used.
    """
    try:
        return client.query(query, job_config=job_config).result()
    except RefreshError as exc:
        logger.error(
            "OBO: the propagated end-user token appears to have expired and "
            "could not be refreshed (we only ever hold a bare access token, "
            "never a refresh_token — this is expected for GE-forwarded "
            "tokens). Ask the user to retry; a fresh token will be "
            "propagated on their next message: %s",
            exc,
        )
        raise OBOSessionExpiredError(
            "Your session has expired. Please try again — Gemini Enterprise "
            "will refresh your credentials automatically."
        ) from exc
