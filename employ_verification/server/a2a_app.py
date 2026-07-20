"""
Cloud Run A2A entrypoint — self-hosted A2A agent (not Agent Engine).

Gemini Enterprise calls this service directly over the A2A protocol. The user's
Entra JWT arrives on ``Authorization: Bearer``; :class:`AuthHeaderMiddleware`
captures it for OBO/WIF token exchange in tools.

Set ``AGENT_CONFIG_NAME`` to the config stem (e.g. ``emp_verify_v2``).
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from a2a.server.apps import A2AFastAPIApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, EXTENDED_AGENT_CARD_PATH
from dotenv import load_dotenv
from fastapi import FastAPI, Response

from agents._base.agent_card import build_a2ui_agent_card
from agents._base.auth_middleware import AuthHeaderMiddleware

load_dotenv()

logger = logging.getLogger(__name__)

AGENT_CONFIG_NAME = os.environ.get("AGENT_CONFIG_NAME", "emp_verify_v2")
A2A_RPC_PATH = os.environ.get("A2A_RPC_PATH", "/a2a/v1")


def _load_executor_class():
    module = importlib.import_module(f"agents.{AGENT_CONFIG_NAME}.executor")
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and hasattr(attr, "AGENT_CONFIG_NAME")
            and attr_name != "BaseA2UIExecutor"
        ):
            return attr
    raise RuntimeError(f"No executor found in agents.{AGENT_CONFIG_NAME}.executor")


def _build_agent_card() -> AgentCard:
    app_url = os.environ.get("APP_URL", "http://localhost:8080")
    card_dict = build_a2ui_agent_card(
        AGENT_CONFIG_NAME,
        service_url=app_url,
        rpc_path=A2A_RPC_PATH,
    )
    return AgentCard(**card_dict)


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    executor_class = _load_executor_class()
    request_handler = DefaultRequestHandler(
        agent_executor=executor_class(),
        task_store=InMemoryTaskStore(),
    )
    agent_card = _build_agent_card()
    a2a_app = A2AFastAPIApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    a2a_app.add_routes_to_app(
        app_instance,
        agent_card_url=f"{A2A_RPC_PATH}{AGENT_CARD_WELL_KNOWN_PATH}",
        rpc_url=A2A_RPC_PATH,
        extended_agent_card_url=f"{A2A_RPC_PATH}{EXTENDED_AGENT_CARD_PATH}",
    )
    logger.info(
        "A2A server ready for agent=%s rpc=%s card=%s",
        AGENT_CONFIG_NAME,
        A2A_RPC_PATH,
        f"{A2A_RPC_PATH}{AGENT_CARD_WELL_KNOWN_PATH}",
    )
    yield


app = FastAPI(
    title=f"a2a-{AGENT_CONFIG_NAME}",
    description=f"Self-hosted A2A agent: {AGENT_CONFIG_NAME}",
    lifespan=lifespan,
)
app.add_middleware(AuthHeaderMiddleware)


@app.get("/healthz")
def healthz() -> Response:
    return Response(status_code=200)
