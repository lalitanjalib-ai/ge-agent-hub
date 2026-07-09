#!/usr/bin/env python3
"""Smoke test: shared widgets library + POC agent integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_POC_ROOT = Path(__file__).resolve().parent
for path in (_REPO_ROOT, _POC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

EXPECTED_EXAMPLES = {
    "kpmg_action_bar.json",
    "kpmg_branded_header.json",
    "kpmg_confirmation_modal.json",
    "kpmg_data_field_row.json",
    "kpmg_metric_card.json",
    "kpmg_resource_dashboard.json",
    "kpmg_status_panel.json",
    "kpmg_user_profile.json",
}


def main() -> int:
    from a2ui.schema.common_modifiers import remove_strict_validation
    from a2ui.schema.constants import VERSION_0_8
    from a2ui.schema.manager import A2uiSchemaManager
    from widgets.python.a2ui_utils import _extract_a2ui_messages
    from widgets.python.builders import (
        resource_dashboard,
        resource_detail,
        resource_status_icon,
    )
    from widgets.python.provider import EXAMPLES_DIR, KpmgWidgetsCatalog

    errors: list[str] = []

    found = {p.name for p in EXAMPLES_DIR.glob("*.json")}
    missing = EXPECTED_EXAMPLES - found
    if missing:
        errors.append(f"Missing widget examples: {sorted(missing)}")

    sm = A2uiSchemaManager(
        version=VERSION_0_8,
        catalogs=KpmgWidgetsCatalog.get_catalogs_for_agent(),
        schema_modifiers=[remove_strict_validation],
    )
    prompt = sm.generate_system_prompt(
        "role", "workflow", "ui", include_examples=True, validate_examples=False
    )
    if "kpmg-resource-dashboard" not in prompt and "header_card" not in prompt:
        errors.append("System prompt missing KpmgResourceDashboard example")

    dash_path = EXAMPLES_DIR / "kpmg_resource_dashboard.json"
    dash = json.loads(dash_path.read_text(encoding="utf-8"))
    tagged = f"<a2ui-json>{json.dumps(dash)}</a2ui-json>"
    messages = _extract_a2ui_messages(tagged)
    if len(messages) != 3:
        errors.append(f"Expected 3 A2UI messages from dashboard example, got {len(messages)}")

    dm = next((m for m in messages if "dataModelUpdate" in m), None)
    if dm:
        resources = next(
            (c for c in dm["dataModelUpdate"]["contents"] if c["key"] == "resources"),
            None,
        )
        if not resources or "valueMap" not in resources:
            errors.append("kpmg_resource_dashboard.json must use valueMap for /resources")

    from resources import RESOURCES, get_resources

    if len(get_resources()) != 3:
        errors.append("POC get_resources() should return 3 mock resources")

    built = resource_dashboard(resources=RESOURCES)
    built_surface = next(m for m in built if "surfaceUpdate" in m)
    component_ids = {c["id"] for c in built_surface["surfaceUpdate"]["components"]}
    for suffix in ("_status_row", "_kpmg_brand", "_usage_slider"):
        if not any(component_id.endswith(suffix) for component_id in component_ids):
            errors.append(f"resource_dashboard missing KPMG component suffix: {suffix}")
    for required_id in ("header_card", "metrics_row"):
        if required_id not in component_ids:
            errors.append(f"resource_dashboard missing KPMG component: {required_id}")
    built_dm = next(m for m in built if "dataModelUpdate" in m)
    built_resources = next(
        c for c in built_dm["dataModelUpdate"]["contents"] if c["key"] == "resources"
    )
    if len(built_resources["valueMap"]) != 3:
        errors.append("resource_dashboard builder did not emit 3 resource entries")

    icons = {
        next(f["valueString"] for f in entry["valueMap"] if f["key"] == "status_icon")
        for entry in built_resources["valueMap"]
    }
    expected_icons = {
        resource_status_icon("healthy"),
        resource_status_icon("warning"),
        resource_status_icon("error"),
    }
    if icons != expected_icons:
        errors.append(f"Unexpected status icons: {icons} (expected {expected_icons})")

    from agent import root_agent

    if root_agent.name != "cloud_dashboard":
        errors.append(f"Unexpected agent name: {root_agent.name}")
    if root_agent.after_model_callback.__name__ != "cloud_dashboard_callback":
        errors.append("Agent must use cloud_dashboard_callback for KPMG dashboard rendering")
    if root_agent.before_model_callback.__name__ != "before_model_callback":
        errors.append("Agent must use before_model_callback for View Details actions")

    from action_utils import extract_user_action, normalize_action_context
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    action_request = LlmRequest(
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=(
                            '{"userAction": {"name": "view_resource_details", '
                            '"context": {"name": "events-db", "type": "Cloud SQL"}}}'
                        )
                    )
                ],
            )
        ]
    )
    parsed_action = extract_user_action(action_request)
    if not parsed_action or parsed_action.get("name") != "view_resource_details":
        errors.append("extract_user_action failed to parse view_resource_details")
    context = normalize_action_context(parsed_action.get("context") if parsed_action else {})
    if context.get("name") != "events-db":
        errors.append("normalize_action_context failed")

    detail = resource_detail(RESOURCES[1])
    detail_surface = next(m for m in detail if "surfaceUpdate" in m)
    detail_ids = {c["id"] for c in detail_surface["surfaceUpdate"]["components"]}
    if "detail_title" not in detail_ids:
        errors.append("resource_detail missing detail_title component")

    button = next(
        (
            c
            for c in built_surface["surfaceUpdate"]["components"]
            if c["id"].endswith("_action_button")
            and c["component"].get("Button", {}).get("action", {}).get("name")
            == "view_resource_details"
        ),
        None,
    )
    if button is None:
        errors.append("resource dashboard missing view_resource_details button")
    else:
        action = button["component"]["Button"]["action"]
        if not action.get("context"):
            errors.append("view_resource_details button missing action context")

    from widgets.python.builders import user_profile

    profile_payload = user_profile()
    profile_surface = next(m for m in profile_payload if "surfaceUpdate" in m)
    profile_ids = {c["id"] for c in profile_surface["surfaceUpdate"]["components"]}
    for required_id in ("header", "stats_row", "follow_btn", "name", "profile_kpmg_brand"):
        if required_id not in profile_ids:
            errors.append(f"user_profile missing component: {required_id}")
    profile_dm = next(m for m in profile_payload if "dataModelUpdate" in m)
    profile_name = next(
        c for c in profile_dm["dataModelUpdate"]["contents"] if c["key"] == "name"
    )
    if profile_name.get("valueString") != "Sarah Chen":
        errors.append("user_profile sample data mismatch")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("OK: widgets library has all 8 POC widgets; agent integration verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
