"""
BYOC A2A server for the Employee Verification agent, deployed to Vertex AI
Agent Engine via bring-your-own-Dockerfile mode.

Why BYOC instead of the managed `A2aAgent` template
----------------------------------------------------
The managed `A2aAgent` template builds and owns the Starlette app
internally, which leaves no way to attach custom ASGI middleware before the
A2A routes are registered — and capturing the propagated end-user token off
the raw `Authorization` header requires exactly that. BYOC gives us our own
Starlette `app` object, so we control the exact routes/URLs and can install
`TokenExtractorMiddleware` directly.

How Gemini Enterprise delivers the end-user Entra ID token here
------------------------------------------------------------------
This agent is registered with GE at the Agent Engine V2 ingress URL
(the `.../reasoningEngines/{id}/api/...` pattern) with a Microsoft Entra ID
authorization resource attached. Per platform fix CL/963035042: GE mints and
attaches a Discovery Engine P4SA token on `Authorization` (for Google edge
auth) and places the end-user's Entra JWT on
`X-Goog-Agent-User-Authorization`. Agent Engine's V2 ingress gateway then
rewrites `X-Goog-Agent-User-Authorization` onto the standard
`Authorization: Bearer <Entra_JWT>` header before delivering the request to
this container.

`TokenExtractorMiddleware` below captures that `Authorization` header into a
per-request ContextVar (see `employee_agent.token_context`). The BigQuery
tools then exchange that Entra JWT for a Google federated access token via
Workforce Identity Federation / RFC 8693 STS (see `employee_agent.entra_wif`
and `employee_agent.tools.bq_client`) before calling BigQuery.
"""

from __future__ import annotations

import logging
import os

from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from employee_agent.agent import root_agent
from employee_agent.token_context import reset_user_token, set_user_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

# The path segment under which the A2A app is served. Required by the Agent
# Engine V2 ingress feature — the `/api` prefix is consumed by Agent Engine's
# own routing/gateway logic and does NOT appear in the path this container
# actually sees; Starlette below mounts the A2A app at this same prefix so
# local testing (uvicorn) and the deployed behavior match.
API_PREFIX = "/api"


class TokenExtractorMiddleware:
    """Pure-ASGI middleware: captures `Authorization: Bearer <Entra_JWT>` off
    the inbound request (rewritten there by Agent Engine's V2 ingress
    gateway from `X-Goog-Agent-User-Authorization`) into a per-request
    ContextVar, before any A2A/ADK processing begins. See
    `employee_agent.token_context` for the reader side used by the BigQuery
    tools to perform the Entra->Google WIF/STS exchange.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        token: str | None = None
        if scope["type"] == "http":
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    if value[:7].lower() == b"bearer ":
                        token = value[7:].decode("latin-1")
                    break
        reset = set_user_token(token)
        if token:
            logger.info("OBO: propagated Entra Authorization header captured on this request.")
        else:
            logger.info("OBO: no Authorization header on this request.")
        try:
            await self.app(scope, receive, send)
        finally:
            reset_user_token(reset)


def _build_agent_card() -> AgentCard:
    """Build the A2A agent card.

    Per Google's own BYOC/V2-ingress guidance, the `url` baked into this card
    object is effectively a placeholder: Gemini Enterprise is registered
    separately (see scripts/deploy.py) with the authoritative V2 ingress URL,
    which is what actually determines routing and OAuth-token propagation.
    """
    skills = [
        AgentSkill(
            id="employee-lookup",
            name="Employee Lookup",
            description="Search and find employee records by name, employee ID, or department.",
            tags=["employee", "lookup", "search", "hr"],
            examples=["Find employee John Smith", "Look up employee E-1001"],
        ),
        AgentSkill(
            id="employee-update",
            name="Employee Field Update",
            description="Update editable employee fields like address, phone, email, and emergency contact.",
            tags=["employee", "update", "edit", "hr"],
            examples=["Update my address to 123 Main St"],
        ),
        AgentSkill(
            id="employee-verification",
            name="Employee Verification",
            description="Verify employee records and mark records as verified.",
            tags=["employee", "verification", "verify", "hr"],
            examples=["Verify my employment"],
        ),
    ]
    return AgentCard(
        name="Employee Verification Agent",
        description=(
            "An HR agent that helps employees review, update, and verify "
            "their employment records, using Microsoft Entra ID 3P OAuth "
            "propagated via Agent Engine V2 ingress and exchanged for "
            "Google Cloud credentials via Workforce Identity Federation."
        ),
        url="https://placeholder.example.com/api/a2a",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        skills=skills,
        supports_authenticated_extended_card=False,
    )


def build_app() -> Starlette:
    runner = Runner(
        app_name="employee_verification_agent",
        agent=root_agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
    )
    agent_card = _build_agent_card()
    request_handler = DefaultRequestHandler(
        agent_executor=A2aAgentExecutor(runner=runner),
        task_store=InMemoryTaskStore(),
    )

    a2a_application = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    # Build the A2A app at its own root ("/") so that once mounted under
    # API_PREFIX below, the effective external paths are:
    #   {API_PREFIX}/a2a/.well-known/agent-card.json  (GET)
    #   {API_PREFIX}/a2a/                              (POST, JSON-RPC)
    a2a_app = a2a_application.build(rpc_url="/")

    async def healthz(_request):
        return JSONResponse({"status": "ok"})

    root = Starlette(routes=[])
    root.mount("/a2a", app=a2a_app)
    root.add_route("/healthz", healthz, methods=["GET"])

    outer = Starlette(routes=[])
    outer.mount(API_PREFIX, app=root)
    # Also expose a plain, unprefixed health check for platform liveness
    # probes that may not know about API_PREFIX.
    outer.add_route("/healthz", healthz, methods=["GET"])

    outer.add_middleware(TokenExtractorMiddleware)
    return outer


app = build_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    logger.info("Starting Employee Verification A2A server on port %s (prefix %s)", port, API_PREFIX)
    uvicorn.run(app, host="0.0.0.0", port=port)
