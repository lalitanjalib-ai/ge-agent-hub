"""
Local A2UI sample agent — ADK + A2UI SDK (codelab pattern).

Codelab: https://codelabs.developers.google.com/next26/adk-a2ui

Run:
    cd poc
    python run_local.py

Then open http://127.0.0.1:8080/dev-ui/?app=poc (or select **poc** in the dropdown).
"""

from __future__ import annotations

import json
import os
import sys
from functools import cached_property
from pathlib import Path
from typing import Any

# Repo root on sys.path for the shared widgets library.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
except ImportError:
    pass

from google.adk.agents import Agent
from google.adk.models import Gemini
from google.genai import types
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import VERSION_0_8

from dashboard_callback import before_model_callback, cloud_dashboard_callback
from resources import get_resource_details, get_resources, get_user_profile
from ssl_config import apply_ssl_env, gemini_http_options
from widgets.python.provider import KpmgWidgetsCatalog
from widgets.python.theme import KPMG_SURFACE_STYLES

_ssl_verify_disabled = apply_ssl_env()
_gemini_http_options = gemini_http_options(_ssl_verify_disabled)


def _build_model():
    model_name = os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash")
    if not _gemini_http_options:
        return model_name

    class CorporateGemini(Gemini):
        @cached_property
        def api_client(self):
            from google.genai import Client

            base_url, api_version = self._base_url_and_api_version
            kwargs_for_http_options: dict[str, Any] = {
                "headers": self._tracking_headers(),
                "retry_options": self.retry_options,
                "base_url": base_url,
                **_gemini_http_options,
            }
            if api_version:
                kwargs_for_http_options["api_version"] = api_version

            kwargs: dict[str, Any] = {
                "http_options": types.HttpOptions(**kwargs_for_http_options),
            }
            if self.model.startswith("projects/"):
                kwargs["enterprise"] = True

            return Client(**kwargs)

    return CorporateGemini(model=model_name)

_kpmg_styles = json.dumps(KPMG_SURFACE_STYLES)

schema_manager = A2uiSchemaManager(
    version=VERSION_0_8,
    catalogs=KpmgWidgetsCatalog.get_catalogs_for_agent(),
    schema_modifiers=[remove_strict_validation],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a cloud infrastructure assistant. When users ask about "
        "their cloud resources, use the get_resources tool to fetch the "
        "current state."
    ),
    workflow_description=(
        "When users ask about cloud resources, call get_resources. When they "
        "click View Details on a resource card, the view_resource_details "
        "action is handled automatically — you may also call "
        "get_resource_details(name) if asked for a single resource. "
        "When users ask to see their profile, call get_user_profile."
    ),
    ui_description=(
        "Use KPMG-branded A2UI surfaces with beginRendering styles: "
        f"{_kpmg_styles}. "
        "When displaying cloud resources after get_resources, the UI is rendered "
        "from the shared KpmgResourceDashboard widget (KpmgBrandedHeader + "
        "KpmgMetricCard summary row + KpmgStatusPanel/KpmgDataFieldRow resource "
        "cards). View Details buttons use action name view_resource_details with "
        "context paths for name, type, region, and status. "
        "Detail views use KpmgDataFieldRow for all resource attributes. "
        "User profiles use the KpmgUserProfile widget (avatar Image, name, bio, "
        "follower stats, Follow button with follow action). "
        "For List templates, bind list data with valueMap (keyed entries), "
        "not valueList. Use standard icon names: check, warning, info. "
        "Do NOT use markdown formatting in text values. Use the usageHint "
        "property for heading levels instead. "
        "Output ONLY A2UI protocol messages (beginRendering, surfaceUpdate, "
        "dataModelUpdate) inside <a2ui-json> tags. Never output kind/data/"
        "metadata wire-format wrappers — adk web renders those as raw JSON. "
        "Put all explanations into Text components."
    ),
    include_schema=True,
    include_examples=True,
    validate_examples=False,
)

root_agent = Agent(
    model=_build_model(),
    name="cloud_dashboard",
    description="A cloud infrastructure assistant that renders rich A2UI interfaces.",
    instruction=instruction,
    tools=[get_resources, get_resource_details, get_user_profile],
    before_model_callback=before_model_callback,
    after_model_callback=cloud_dashboard_callback,
)
