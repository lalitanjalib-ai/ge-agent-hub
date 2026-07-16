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

import logging
import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.genai import types

from app.entra_wif import entra_wif_header_provider

# Agent Runtime sets GOOGLE_CLOUD_LOCATION to the engine's deploy region, which
# may not match where the chosen Gemini model is published. Set GEMINI_LOCATION
# (e.g. `global`, `eu`, `us`) to redirect model calls; otherwise leave as-is.
_gemini_location = os.getenv("GEMINI_LOCATION")
if _gemini_location:
    os.environ["GOOGLE_CLOUD_LOCATION"] = _gemini_location
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")

# Runtime filters non-stdlib loggers to WARNING.
logging.getLogger("app").setLevel(logging.INFO)

USER_PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
WIF_PROVIDER = os.environ["WIF_PROVIDER_RESOURCE"]
BQ_MCP_URL = "https://bigquery.googleapis.com/mcp"


bq_mcp = McpToolset(
    # sse_read_timeout must be strictly less than Cloud Run's request timeout
    # (300s default), or Cloud Run cuts the HTTP connection before the
    # McpError reaches on_tool_error_callback — leaving the user with no
    # response instead of an explicit failure message.
    connection_params=StreamableHTTPConnectionParams(
        url=BQ_MCP_URL,
        sse_read_timeout=180,
    ),
    header_provider=entra_wif_header_provider(
        wif_provider=WIF_PROVIDER,
        user_project=USER_PROJECT,
    ),
)


def _surface_tool_error(tool, args, tool_context, error):
    """Convert tool exceptions into model-visible function responses.

    Without this, an McpError (e.g. 300s timeout when BQ MCP silently drops
    an unauthorized call) propagates up and aborts the whole A2A request —
    the user sees no response and the model never gets to apply the
    instruction about reporting tool failures.
    """
    del tool, args, tool_context
    return {"error": f"{type(error).__name__}: {error}"}


root_agent = Agent(
    name="bigquery_analyst",
    description=(
        "BigQuery analytics assistant. Discovers datasets, inspects schemas, "
        "and runs SQL on the user's behalf — every BigQuery call executes as "
        "the end user via Workforce Identity Federation."
    ),
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "You are a BigQuery analytics assistant. Use the available BigQuery MCP "
        "tools to discover datasets, inspect table schemas, and run SQL queries "
        "on the user's behalf. Always show the SQL you run, then summarize the "
        "results. BigQuery enforces per-user IAM via Workforce Identity "
        "Federation, so if you hit a 403/permission-denied, report the exact "
        "error back to the user — it means they don't have access to that "
        "resource.\n\n"
        "Never describe, summarize, or guess at BigQuery contents (datasets, "
        "tables, schemas, rows, counts) in the same turn as the tool call that "
        "would retrieve them. In a turn that issues a tool call, emit only the "
        "tool call — no answer text alongside it. Wait for the tool result in "
        "the next turn, then answer using only what the tool actually returned. "
        "If a tool call fails or times out, say so explicitly and do not "
        "substitute an assumed answer."
    ),
    tools=[bq_mcp],
    on_tool_error_callback=_surface_tool_error,
)

app = App(
    root_agent=root_agent,
    name="app",
)
