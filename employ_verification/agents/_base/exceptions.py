"""
Shared exception types for agent auth / OBO handling.

Kept in their own module (rather than co-located with user_context.py) to
avoid import cycles as more modules need to catch/raise these types.
"""

from __future__ import annotations


class OBOAuthError(Exception):
    """Raised when the On-Behalf-Of credential flow receives a token that is
    definitively unusable for the target Google API (e.g. Gemini Enterprise
    forwarded a Google id_token instead of an OAuth access_token in
    ``google_direct`` mode).

    This is NOT a transient/retryable error — it indicates a GE authorization
    resource / OAuth scope misconfiguration that requires an operator to fix
    the authorization resource, not a code-level retry. Callers should let
    this propagate (or convert it to the ``OBO_AUTH_ERROR`` sentinel for tool
    responses) rather than silently falling back to ADC, since silently
    querying as the service account defeats the entire purpose of OBO.
    """

    #: Sentinel string embedded in tool JSON error responses and checked by
    #: agents/_base/base_executor.py against the final LLM answer text, so
    #: the user always sees a fixed, generic message regardless of how the
    #: LLM chooses to paraphrase the underlying tool error.
    ERROR_CODE = "OBO_AUTH_ERROR"
