"""On-Behalf-Of (OBO) token propagation — BYOC A2A server, single source of truth.

This BYOC deployment owns the raw ASGI request/response cycle directly, so
there is exactly ONE place the propagated end-user Entra ID token can
arrive: the standard ``Authorization: Bearer <Entra_JWT>`` HTTP header on
the incoming A2A request.

This agent is registered with Gemini Enterprise via the Agent Engine V2
`/api/...` ingress URL with a Microsoft Entra ID authorization resource
attached. GE places the end user's Entra JWT on
`X-Goog-Agent-User-Authorization`; Agent Engine's V2 ingress gateway then
rewrites that onto the standard `Authorization` header before the request
reaches this container. Cloud Run/Agent Engine's own IAM-invoker token (if
any) rides on a separate header and is never confused with this.

`TokenExtractorMiddleware` (see main.py) captures that header into the
ContextVar below, once per request, before the ADK Runner/executor ever
starts processing the message. Tool functions read it via `get_user_token()`
with no knowledge of HTTP/ASGI at all, then exchange it for a Google Cloud
access token via `employee_agent.entra_wif` before calling BigQuery.
"""

from __future__ import annotations

import contextvars
import logging

logger = logging.getLogger(__name__)

# Populated per-request by TokenExtractorMiddleware in main.py from the
# inbound `Authorization: Bearer <Entra_JWT>` header. A ContextVar (not a
# global) is required because ADK/Starlette handle concurrent requests as
# separate asyncio tasks — this guarantees no cross-user leakage.
_user_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_entra_token", default=None
)


def set_user_token(token: str | None) -> contextvars.Token:
    """Store the forwarded user token for the current request. Called once,
    by the middleware, at the very start of request handling."""
    return _user_token_var.set(token)


def reset_user_token(token: contextvars.Token) -> None:
    """Reset the ContextVar to its previous value. Call in a finally block."""
    _user_token_var.reset(token)


def get_user_token() -> str | None:
    """Return the forwarded end-user Entra ID JWT for the current request,
    or None if none was propagated (e.g. the agent was invoked directly
    without going through Gemini Enterprise, or the GE authorization
    resource is not linked to this agent)."""
    return _user_token_var.get()
