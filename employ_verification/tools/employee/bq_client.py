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

PROJECT_ID = os.environ.get("PROJECT_ID", "kpmg-452019")

# BigQuery needs an OAuth scope on the federated token.
_BQ_SCOPES = ["https://www.googleapis.com/auth/bigquery"]


def get_bigquery_client() -> bigquery.Client:
    """Return a BigQuery client for the current user (OBO) or ADC fallback."""
    try:
        from agents._base.user_context import get_user_gcp_credentials

        user_credentials = get_user_gcp_credentials(
            scopes=_BQ_SCOPES, quota_project_id=PROJECT_ID
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("OBO: error resolving user credentials, using ADC: %s", exc)
        user_credentials = None

    if user_credentials is not None:
        logger.info("BigQuery: using On-Behalf-Of user (federated) credentials")
        return bigquery.Client(project=PROJECT_ID, credentials=user_credentials)

    logger.info("BigQuery: using Application Default Credentials (service account)")
    return bigquery.Client(project=PROJECT_ID)
