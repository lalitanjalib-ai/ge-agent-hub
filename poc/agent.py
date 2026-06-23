"""
Local A2UI sample agent — ADK + A2UI SDK (codelab pattern).

Codelab: https://codelabs.developers.google.com/next26/adk-a2ui

Run:
    cd poc
    python scripts/run_local.py

Then open http://127.0.0.1:8080 and select this agent folder.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Repo root on sys.path for the shared widgets library.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from google.adk.agents import Agent
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.schema.constants import VERSION_0_8

from a2ui_utils import a2ui_callback
from resources import get_resources
from widgets.python.provider import KpmgWidgetsCatalog

schema_manager = A2uiSchemaManager(
    version=VERSION_0_8,
    catalogs=KpmgWidgetsCatalog.get_catalogs_for_agent(),
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a cloud infrastructure assistant. When users ask about "
        "their cloud resources, use the get_resources tool to fetch the "
        "current state."
    ),
    workflow_description=(
        "Analyze the user's request and return structured UI when appropriate."
    ),
    ui_description=(
        "Use cards for resource summaries, rows and columns for comparisons, "
        "icons for status indicators, and buttons for drill-down actions. "
        "Apply KPMG branding in beginRendering styles: "
        '{"primaryColor": "#00338D", "font": "Roboto"}. '
        "Do NOT use markdown formatting in text values. Use the usageHint "
        "property for heading levels instead. "
        "Respond ONLY with the A2UI JSON array. Do NOT include any text "
        "outside the JSON. Put all explanations into Text components."
    ),
    include_schema=True,
    include_examples=True,
)

model = os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash")

root_agent = Agent(
    model=model,
    name="cloud_dashboard",
    description="A cloud infrastructure assistant that renders rich A2UI interfaces.",
    instruction=instruction,
    tools=[get_resources],
    after_model_callback=a2ui_callback,
)
