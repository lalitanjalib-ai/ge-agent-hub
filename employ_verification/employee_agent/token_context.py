"""On-Behalf-Of (OBO) token propagation — BYOC A2A server, single source of truth.

Unlike the managed `A2aAgent` template (which forced us to guess at where
Gemini Enterprise might have stashed the forwarded token — message metadata,
call-context state, ADK session state, multiple key names, id_token vs.
access_token heuristics, etc.), this BYOC deployment owns the raw ASGI
request/response cycle directly. That means there is exactly ONE place the
propagated end-user OAuth token can arrive: the standard
``Authorization: Bearer <token>`` HTTP header on the incoming A2A request.

Per Google's Agent Engine V2 ingress documentation: when an agent is
registered with Gemini Enterprise via the V2 `/api/...` ingress URL (NOT the
old `/a2a/v1` URL served by the managed A2A template) and the hosting project
is on the OAuth-propagation allowlist, GE attaches the end user's OAuth
access token on `X-Goog-Agent-User-Authorization`. Agent Engine's own gateway
then rewrites that onto the standard `Authorization` header before the
request reaches this container. Cloud Run/Agent Engine's own IAM-invoker
token (if any) rides on a separate header and is never confused with this.

`TokenExtractorMiddleware` (see main.py) captures that header into the
ContextVar below, once per request, before the ADK Runner/executor ever
starts processing the message. Tool functions read it via `get_user_token()`
with no knowledge of HTTP/ASGI at all.
"""

from __future__ import annotations

import contextvars
import logging

logger = logging.getLogger(__name__)

# Populated per-request by TokenExtractorMiddleware in main.py from the
# inbound `Authorization: Bearer <token>` header. A ContextVar (not a global)
# is required because ADK/Starlette handle concurrent requests as separate
# asyncio tasks — this guarantees no cross-user leakage.
_user_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "user_oauth_token", default=None
)


def set_user_token(token: str | None) -> contextvars.Token:
    """Store the forwarded user token for the current request. Called once,
    by the middleware, at the very start of request handling."""
    return _user_token_var.set(token)


def reset_user_token(token: contextvars.Token) -> None:
    """Reset the ContextVar to its previous value. Call in a finally block."""
    _user_token_var.reset(token)


def get_user_token() -> str | None:
    """Return the forwarded end-user OAuth access token for the current
    request, or None if none was propagated (e.g. the hosting project is not
    yet on the OAuth-propagation allowlist, or the agent was invoked directly
    without going through Gemini Enterprise)."""
    return _user_token_var.get()
