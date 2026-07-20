"""
Auth Middleware — capture the inbound ``Authorization`` header.

The A2A SDK strips the raw ``Authorization`` header before it reaches the
executor's ``RequestContext``. When running a self-hosted A2A server (Cloud Run,
GKE, local), mount :class:`AuthHeaderMiddleware` so the header is captured into a
``ContextVar`` that :func:`agents._base.user_context.extract_user_token` can read
as a last resort.

On Vertex AI Agent Engine the forwarded token normally arrives via the A2A
message metadata / call context instead, so this middleware is a no-op fallback
there — it is safe to include either way.

Usage (self-hosted Starlette/FastAPI app):
    app.add_middleware(AuthHeaderMiddleware)
"""

from __future__ import annotations

import contextvars

_captured_authorization: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "captured_authorization", default=None
)


def get_captured_authorization() -> str | None:
    """Return the Authorization header captured for the current context."""
    return _captured_authorization.get()


class AuthHeaderMiddleware:
    """ASGI middleware that captures the ``Authorization`` header per request."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            raw = headers.get(b"authorization", b"")
            if raw:
                _captured_authorization.set(raw.decode("latin1"))
        await self.app(scope, receive, send)
