"""
Shared BigQuery client factory with On-Behalf-Of (OBO) support.

Every employee tool goes through :func:`get_bigquery_client` so that queries run
in the *identity of the logged-in Gemini Enterprise user* whenever a forwarded
token is available:

  * If Gemini Enterprise forwarded the user's Entra token (authorization enabled),
    it is exchanged for a Google Workforce (WIF) access token and the BigQuery
    client authenticates as that user. This enforces user-level access control
    (row/column ACLs, dataset permissions) and produces user-attributed audit logs.

  * Otherwise (no forwarded token / machine-to-machine call), the client falls
    back to Application Default Credentials (the Reasoning Engine service account).
"""

from __future__ import annotations

import logging
import os

from google.cloud import bigquery

logger = logging.getLogger(__name__)

# NOTE: There is intentionally NO hardcoded fallback project here. A stale
# hardcoded default previously pointed at an unrelated POC project
# ("kpmg-452019"), which would silently query the WRONG GCP project if
# PROJECT_ID was ever missing from the environment. Fail loudly instead.
PROJECT_ID = os.environ.get("PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
if not PROJECT_ID:
    raise RuntimeError(
        "PROJECT_ID (or GOOGLE_CLOUD_PROJECT) is not set in the environment. "
        "Refusing to default to a hardcoded project — set PROJECT_ID explicitly "
        "(e.g. prj-us-bpg-spark-poc) in .env or the Agent Engine env_vars."
    )

# BigQuery needs an OAuth scope on the federated token.
_BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]

# When true, a failed OBO token exchange raises instead of silently falling
# back to ADC (the Reasoning Engine service account). Use this to validate
# that OBO is actually working end-to-end — without it, WIF misconfiguration
# (missing pool/provider env vars, STS errors, expired tokens) is invisible:
# the agent still returns data, just under the wrong identity.
_STRICT_OBO = os.environ.get("STRICT_OBO", "").strip().lower() in ("true", "1", "yes")


def get_bigquery_client() -> bigquery.Client:
    """Return a BigQuery client for the current user (OBO) or ADC fallback."""
    from agents._base.user_context import (
        OBO_MODE_GOOGLE_DIRECT,
        get_credential_mode,
        get_user_gcp_credentials,
        get_user_token,
        log_obo_principal,
    )

    had_user_token = bool(get_user_token())

    try:
        user_credentials = get_user_gcp_credentials(
            scopes=_BQ_SCOPES, quota_project_id=PROJECT_ID
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("OBO: error resolving user credentials, using ADC: %s", exc)
        user_credentials = None

    if had_user_token and get_credential_mode() == OBO_MODE_GOOGLE_DIRECT:
        log_obo_principal(get_user_token())

    if user_credentials is not None:
        if get_credential_mode() == OBO_MODE_GOOGLE_DIRECT:
            logger.info("BigQuery: using forwarded Google OAuth token (direct OBO, no STS)")
        else:
            logger.info("BigQuery: using On-Behalf-Of user (federated) credentials")
        return bigquery.Client(project=PROJECT_ID, credentials=user_credentials)

    if had_user_token:
        # We HAD a forwarded Entra token but OBO still failed (STS error, bad
        # WIF config, etc). Falling back here silently defeats the purpose of
        # OBO — the query will run as the service account with no per-user
        # audit attribution. Make this loud and grep-able in Cloud Logging.
        msg = (
            "OBO FAILED — FALLING BACK TO SERVICE ACCOUNT despite a forwarded "
            "user token being present. Check WORKFORCE_POOL_ID/PROVIDER_ID, "
            "STS errors, and token audience/scope configuration."
        )
        if _STRICT_OBO:
            raise RuntimeError(msg)
        logger.warning(msg)
    else:
        logger.info(
            "BigQuery: no forwarded user token — using Application Default "
            "Credentials (service account) as expected"
        )

    return bigquery.Client(project=PROJECT_ID)
