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
}


def main() -> int:
    from a2ui.schema.common_modifiers import remove_strict_validation
    from a2ui.schema.constants import VERSION_0_8
    from a2ui.schema.manager import A2uiSchemaManager
    from widgets.python.a2ui_utils import _extract_a2ui_messages
    from widgets.python.builders import resource_dashboard, resource_status_icon
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
    for required_id in (
        "header_card",
        "metrics_row",
        "resource_status_row",
        "resource_kpmg_brand",
        "resource_usage_slider",
    ):
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

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1

    print("OK: widgets library has all 7 POC widgets; agent integration verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
