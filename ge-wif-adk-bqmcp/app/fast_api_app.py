# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""A2A entrypoint for Cloud Run. Mirrors `agent_runtime_app.py` for the second
deployment target. Imports the same `app` from `app.agent`; no changes to the
agent definition.

Auth shim: Gemini Enterprise sends the end-user Entra JWT in
`Authorization: Bearer <jwt>` on the A2A RPC POST. `EntraAuthMiddleware`
captures it into a per-request `ContextVar`, and `_AuthInjectingSessionService`
writes it into ADK session state where `entra_wif_header_provider` locates it
by its Microsoft `iss` claim — the state-key name is purely internal.
"""

import contextvars
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.apps import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentExtension
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)
from fastapi import FastAPI, Response
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor
from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder
from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, Session

from app.agent import app as adk_app
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback

setup_telemetry()

logger = logging.getLogger("app.fast_api_app")

# Internal state-key slot used by the auth shim. The reader
# (entra_wif_header_provider) locates the JWT by issuer, not by key, so this
# name is private to this module.
_ENTRA_TOKEN_STATE_KEY = "entra_jwt"

# Per-request bearer captured by EntraAuthMiddleware and read by the session
# service at create_session() time. ContextVar (not request.state) because
# A2aAgentExecutor never sees the FastAPI Request object.
_entra_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "entra_token", default=None
)


class EntraAuthMiddleware:
    """Pure-ASGI middleware: pulls `Authorization: Bearer <jwt>` into a
    ContextVar. Cloud Run's IAM-invoker token rides on
    `X-Serverless-Authorization` and is irrelevant to user identity.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        token: str | None = None
        if scope["type"] == "http":
            for name, value in scope["headers"]:
                if name == b"authorization":
                    if value[:7].lower() == b"bearer ":
                        token = value[7:].decode("latin-1")
                    break
        reset = _entra_token_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _entra_token_var.reset(reset)


class _AuthInjectingSessionService(InMemorySessionService):
    """Seeds new-session state with the per-request Entra JWT.

    `A2aAgentExecutor._prepare_session` always calls `create_session(state={},
    ...)`. We intercept that, fill in `state[state_key] = token` when a bearer
    is on the ContextVar, and delegate to the in-memory implementation. The
    existing fast-path in `entra_wif._find_entra_token` then picks it up.
    """

    def __init__(self, *, state_key: str) -> None:
        super().__init__()
        self._state_key = state_key

    async def create_session(
        self, *, state: dict[str, Any] | None = None, **kwargs: Any
    ) -> Session:
        token = _entra_token_var.get()
        if token and not state:
            state = {self._state_key: token}
        return await super().create_session(state=state, **kwargs)


logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
artifact_service = (
    GcsArtifactService(bucket_name=logs_bucket_name)
    if logs_bucket_name
    else InMemoryArtifactService()
)

runner = Runner(
    app=adk_app,
    artifact_service=artifact_service,
    session_service=_AuthInjectingSessionService(state_key=_ENTRA_TOKEN_STATE_KEY),
)

request_handler = DefaultRequestHandler(
    agent_executor=A2aAgentExecutor(runner=runner),
    task_store=InMemoryTaskStore(),
)

A2A_RPC_PATH = f"/a2a/{adk_app.name}"


async def build_dynamic_agent_card() -> AgentCard:
    """Build the Agent Card dynamically from the root_agent."""
    agent_card_builder = AgentCardBuilder(
        agent=adk_app.root_agent,
        capabilities=AgentCapabilities(
            streaming=True,
            extensions=[
                AgentExtension(
                    uri="https://google.github.io/adk-docs/a2a/a2a-extension/",
                    description="Ability to use the new agent executor implementation",
                ),
            ],
        ),
        rpc_url=f"{os.getenv('APP_URL', 'http://0.0.0.0:8000')}{A2A_RPC_PATH}",
        agent_version=os.getenv("AGENT_VERSION", "0.1.0"),
    )
    return await agent_card_builder.build()


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    agent_card = await build_dynamic_agent_card()
    a2a_app = A2AFastAPIApplication(agent_card=agent_card, http_handler=request_handler)
    a2a_app.add_routes_to_app(
        app_instance,
        agent_card_url=f"{A2A_RPC_PATH}{AGENT_CARD_WELL_KNOWN_PATH}",
        rpc_url=A2A_RPC_PATH,
        extended_agent_card_url=f"{A2A_RPC_PATH}{EXTENDED_AGENT_CARD_PATH}",
    )
    yield


app = FastAPI(
    title="bqmcp-a2a",
    description="A2A (Cloud Run) entrypoint for the bqmcp agent",
    lifespan=lifespan,
)
app.add_middleware(EntraAuthMiddleware)


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Log feedback to Cloud Logging if available, else stdlib logger."""
    try:
        from google.cloud import logging as google_cloud_logging  # noqa: PLC0415

        client = google_cloud_logging.Client()
        client.logger(__name__).log_struct(feedback.model_dump(), severity="INFO")
    except Exception:
        logger.info("feedback %s", feedback.model_dump())
    return {"status": "success"}


@app.get("/healthz")
def healthz() -> Response:
    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
