"""Shared BigQuery client factory with On-Behalf-Of (OBO) support.

Every employee tool goes through :func:`get_bigquery_client` so that queries
run in the identity of the logged-in Gemini Enterprise user whenever a
propagated Microsoft Entra ID token is available on this request:

  * If an Entra JWT is present, it is exchanged for a short-lived Google
    Cloud access token via Workforce Identity Federation / RFC 8693 STS
    (see `employee_agent.entra_wif`), and that federated token is used to
    build the BigQuery client. This enforces user-level access control
    (row/column ACLs, dataset permissions) and produces user-attributed
    Cloud Audit Logs.

  * Otherwise (no propagated token), the client falls back to Application
    Default Credentials (the Reasoning Engine's own service account) unless
    STRICT_OBO is enabled, in which case it raises instead.
"""

from __future__ import annotations

import logging
import os

from google.auth.exceptions import RefreshError
from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from employee_agent.entra_wif import TokenExchangeError, exchange_entra_token_for_google_token
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

WIF_PROVIDER_RESOURCE = os.environ.get("WIF_PROVIDER_RESOURCE")

_BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]

# When true, a missing propagated token (or missing WIF config) raises
# instead of silently falling back to ADC. Use this to validate OBO
# end-to-end without ambiguity.
_STRICT_OBO = os.environ.get("STRICT_OBO", "").strip().lower() in ("true", "1", "yes")


class OBOSessionExpiredError(RuntimeError):
    """Raised when the propagated end-user session cannot be used to build
    working BigQuery credentials (expired Entra token, failed STS exchange,
    or an expired federated token that BigQuery rejects). Tool functions
    catch this and surface a clear, non-technical message asking the user
    to retry."""


def get_bigquery_client() -> bigquery.Client:
    """Return a BigQuery client for the current user (OBO via Entra+WIF) or
    ADC fallback."""
    entra_token = get_user_token()

    if entra_token:
        if not WIF_PROVIDER_RESOURCE:
            msg = (
                "OBO: propagated Entra token present but WIF_PROVIDER_RESOURCE "
                "is not configured. Set WIF_PROVIDER_RESOURCE to the workforce "
                "pool provider resource, e.g. "
                "//iam.googleapis.com/locations/global/workforcePools/<POOL>/providers/<PROVIDER>."
            )
            if _STRICT_OBO:
                raise RuntimeError(msg)
            logger.warning(msg + " Falling back to Application Default Credentials.")
            return bigquery.Client(project=PROJECT_ID)

        try:
            google_token = exchange_entra_token_for_google_token(
                entra_token, wif_provider=WIF_PROVIDER_RESOURCE, user_project=PROJECT_ID
            )
        except TokenExchangeError as exc:
            logger.error("OBO: Entra->Google STS exchange failed: %s", exc)
            raise OBOSessionExpiredError(
                "Your session has expired or could not be verified. Please "
                "try again — Gemini Enterprise will refresh your credentials "
                "automatically."
            ) from exc

        logger.info(
            "OBO: Entra token exchanged via WIF/STS — BigQuery calls will "
            "run as the end user."
        )
        user_credentials = Credentials(token=google_token, scopes=_BQ_SCOPES)
        return bigquery.Client(project=PROJECT_ID, credentials=user_credentials)

    msg = (
        "OBO: no propagated Entra token found on this request (Authorization "
        "header was empty/absent). Falling back to Application Default "
        "Credentials (the Reasoning Engine service account). If this is "
        "unexpected, confirm: (1) the agent is registered with Gemini "
        "Enterprise at the Agent Engine V2 '/api/...' ingress URL, (2) the "
        "GE authorization resource is a Microsoft Entra ID OAuth resource "
        "linked to this agent, and (3) WIF_PROVIDER_RESOURCE is set."
    )
    if _STRICT_OBO:
        raise RuntimeError(msg)
    logger.warning(msg)
    return bigquery.Client(project=PROJECT_ID)


def run_query(client: bigquery.Client, query: str, job_config=None):
    """Execute a BigQuery query, translating an expired/invalid federated
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
            "OBO: the federated Google token appears to have expired and "
            "could not be refreshed client-side (we only ever hold a bare "
            "access token, never a refresh_token). Ask the user to retry; a "
            "fresh Entra token will be exchanged on their next message: %s",
            exc,
        )
        raise OBOSessionExpiredError(
            "Your session has expired. Please try again — Gemini Enterprise "
            "will refresh your credentials automatically."
        ) from exc
