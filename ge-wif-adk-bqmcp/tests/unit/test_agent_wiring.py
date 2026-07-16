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
"""Smoke tests that `app.agent` is wired the way the auth pipeline expects.

Cheap import-time checks — no LLM, no STS, no subprocess. Catches the
"I refactored agent.py and broke the wiring" class of regression that
would otherwise only surface in the live A2A e2e test.
"""

from __future__ import annotations

from google.adk.tools.mcp_tool.mcp_toolset import McpToolset


def test_root_agent_identity() -> None:
    from app.agent import root_agent

    assert root_agent.name == "bigquery_analyst"
    assert root_agent.tools, "root_agent must expose at least one tool"


def test_bq_mcp_toolset_uses_entra_header_provider() -> None:
    """The BigQuery MCP toolset must carry the Entra->WIF header provider;
    without it, BQ would be hit as the runtime service account instead of
    the GE end-user."""
    from app.agent import bq_mcp

    assert isinstance(bq_mcp, McpToolset)
    provider = bq_mcp._header_provider
    assert provider is not None, "bq_mcp is missing its header_provider"
    # The provider is a closure returned by entra_wif_header_provider; its
    # qualname pins it to that factory.
    assert "entra_wif_header_provider" in provider.__qualname__, provider.__qualname__
