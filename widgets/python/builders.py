"""
Programmatic builders for KPMG-branded A2UI payloads.

These compose standard A2UI v0.8 catalog components (Card, Column, Row, Text,
Button, Icon, Divider, Modal, etc.) so they render in Gemini Enterprise without
a custom client renderer.
"""

from __future__ import annotations

from typing import Any

from widgets.python.theme import KPMG_SURFACE_STYLES

STATUS_ICON_MAP = {
    "healthy": "check",
    "warning": "warning",
    "error": "info",
    "info": "info",
}

STATUS_LABEL_MAP = {
    "healthy": "Healthy",
    "warning": "Needs Attention",
    "error": "Critical",
    "info": "Info",
}


def resource_status_icon(status: str) -> str:
    """Map a resource status label to a Material icon name."""
    return STATUS_ICON_MAP.get(status.lower(), "info")


def resource_status_label(status: str) -> str:
    """Map a resource status label to display text."""
    return STATUS_LABEL_MAP.get(status.lower(), status.title())


VIEW_RESOURCE_ACTION: dict[str, Any] = {
    "name": "view_resource_details",
    "context": [
        {"key": "name", "value": {"path": "/name"}},
        {"key": "type", "value": {"path": "/type"}},
        {"key": "region", "value": {"path": "/region"}},
        {"key": "status", "value": {"path": "/status_label"}},
    ],
}


_DETAIL_FIELD_SPECS: list[tuple[str, str, str]] = [
    ("type", "Service Type", "category"),
    ("region", "Region", "place"),
    ("status", "Status", "info"),
    ("cpu", "CPU", "memory"),
    ("memory", "Memory", "memory"),
    ("instances", "Instances", "dns"),
    ("url", "URL", "link"),
    ("tier", "Tier", "layers"),
    ("storage", "Storage", "storage"),
    ("connections", "Connections", "hub"),
    ("version", "Version", "info"),
    ("last_deployed", "Last Deployed", "schedule"),
    ("issue", "Issue", "warning"),
]


def begin_surface(
    surface_id: str,
    root: str,
    styles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a beginRendering message."""
    return {
        "beginRendering": {
            "surfaceId": surface_id,
            "root": root,
            "styles": styles or dict(KPMG_SURFACE_STYLES),
        }
    }


def surface_update(surface_id: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a surfaceUpdate message."""
    return {
        "surfaceUpdate": {
            "surfaceId": surface_id,
            "components": components,
        }
    }


def data_model_update(
    surface_id: str,
    contents: list[dict[str, Any]],
    path: str = "/",
) -> dict[str, Any]:
    """Return a dataModelUpdate message."""
    return {
        "dataModelUpdate": {
            "surfaceId": surface_id,
            "path": path,
            "contents": contents,
        }
    }


def branded_header(
    prefix: str,
    title: str,
    subtitle_path: str | None = None,
    icon: str = "verifiedUser",
) -> list[dict[str, Any]]:
    """Branded header: centered icon, title, optional subtitle from data model."""
    children = [f"{prefix}_icon", f"{prefix}_title"]
    components: list[dict[str, Any]] = [
        {
            "id": prefix,
            "component": {
                "Column": {
                    "children": {"explicitList": children},
                    "alignment": "center",
                }
            },
        },
        {
            "id": f"{prefix}_icon",
            "component": {
                "Icon": {"name": {"literalString": icon}}
            },
        },
        {
            "id": f"{prefix}_title",
            "component": {
                "Text": {
                    "text": {"literalString": title},
                    "usageHint": "h2",
                }
            },
        },
    ]

    if subtitle_path:
        children.append(f"{prefix}_subtitle")
        components[0]["component"]["Column"]["children"]["explicitList"] = children
        components.append(
            {
                "id": f"{prefix}_subtitle",
                "component": {
                    "Text": {
                        "text": {"path": subtitle_path},
                        "usageHint": "h4",
                    }
                },
            }
        )

    return components


def data_field_row(
    row_id: str,
    label: str,
    value_path: str,
    icon: str = "info",
) -> list[dict[str, Any]]:
    """Read-only label/value row with leading icon."""
    return [
        {
            "id": row_id,
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            f"{row_id}_icon",
                            f"{row_id}_text_col",
                        ]
                    },
                    "alignment": "center",
                    "distribution": "start",
                }
            },
        },
        {
            "id": f"{row_id}_icon",
            "component": {
                "Icon": {"name": {"literalString": icon}}
            },
        },
        {
            "id": f"{row_id}_text_col",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            f"{row_id}_label",
                            f"{row_id}_value",
                        ]
                    },
                    "alignment": "start",
                }
            },
        },
        {
            "id": f"{row_id}_label",
            "component": {
                "Text": {
                    "text": {"literalString": label},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": f"{row_id}_value",
            "component": {
                "Text": {
                    "text": {"path": value_path},
                    "usageHint": "h5",
                }
            },
        },
    ]


def metric_card(
    card_id: str,
    label_path: str,
    value_path: str,
    trend_path: str | None = None,
) -> list[dict[str, Any]]:
    """KPI card with label, large value, optional trend caption."""
    children = [f"{card_id}_label", f"{card_id}_value"]
    if trend_path:
        children.append(f"{card_id}_trend")

    components: list[dict[str, Any]] = [
        {
            "id": card_id,
            "component": {
                "Card": {"child": f"{card_id}_column"}
            },
        },
        {
            "id": f"{card_id}_column",
            "component": {
                "Column": {
                    "children": {"explicitList": children},
                    "alignment": "center",
                }
            },
        },
        {
            "id": f"{card_id}_label",
            "component": {
                "Text": {
                    "text": {"path": label_path},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": f"{card_id}_value",
            "component": {
                "Text": {
                    "text": {"path": value_path},
                    "usageHint": "h2",
                }
            },
        },
    ]

    if trend_path:
        components.append(
            {
                "id": f"{card_id}_trend",
                "component": {
                    "Text": {
                        "text": {"path": trend_path},
                        "usageHint": "caption",
                    }
                },
            }
        )

    return components


def status_panel(prefix: str, status_path: str = "/status") -> list[dict[str, Any]]:
    """Status summary row with icon and dynamic status text."""
    return [
        {
            "id": prefix,
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            f"{prefix}_icon",
                            f"{prefix}_text",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": f"{prefix}_icon",
            "component": {
                "Icon": {"name": {"literalString": "flag"}}
            },
        },
        {
            "id": f"{prefix}_text",
            "component": {
                "Text": {
                    "text": {"path": status_path},
                    "usageHint": "h5",
                }
            },
        },
    ]


def action_bar(
    bar_id: str,
    primary_label: str,
    primary_action: str,
    secondary_label: str | None = None,
    secondary_action: str | None = None,
) -> list[dict[str, Any]]:
    """Primary/secondary action button row."""
    button_ids = [f"{bar_id}_primary"]
    if secondary_label and secondary_action:
        button_ids.append(f"{bar_id}_secondary")

    components: list[dict[str, Any]] = [
        {
            "id": bar_id,
            "component": {
                "Row": {
                    "children": {"explicitList": button_ids},
                    "alignment": "center",
                    "distribution": "spaceEvenly",
                }
            },
        },
        {
            "id": f"{bar_id}_primary",
            "component": {
                "Button": {
                    "child": f"{bar_id}_primary_text",
                    "action": {"name": primary_action},
                    "variant": "primary",
                }
            },
        },
        {
            "id": f"{bar_id}_primary_text",
            "component": {
                "Text": {"text": {"literalString": primary_label}}
            },
        },
    ]

    if secondary_label and secondary_action:
        components.extend(
            [
                {
                    "id": f"{bar_id}_secondary",
                    "component": {
                        "Button": {
                            "child": f"{bar_id}_secondary_text",
                            "action": {"name": secondary_action},
                            "variant": "secondary",
                        }
                    },
                },
                {
                    "id": f"{bar_id}_secondary_text",
                    "component": {
                        "Text": {"text": {"literalString": secondary_label}}
                    },
                },
            ]
        )

    return components


def confirmation_modal(
    surface_id: str,
    title_path: str = "/title",
    message_path: str = "/message",
    dismiss_action: str = "dismiss",
) -> list[dict[str, Any]]:
    """Modal confirmation pattern with KPMG styling."""
    return [
        {
            "id": "modal_wrapper",
            "component": {
                "Modal": {
                    "entryPointChild": "hidden_entry",
                    "contentChild": "modal_column",
                }
            },
        },
        {
            "id": "hidden_entry",
            "component": {
                "Text": {"text": {"literalString": ""}}
            },
        },
        {
            "id": "modal_column",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "modal_title",
                            "modal_message",
                            "modal_dismiss",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "modal_title",
            "component": {
                "Text": {
                    "text": {"path": title_path},
                    "usageHint": "h2",
                }
            },
        },
        {
            "id": "modal_message",
            "component": {
                "Text": {"text": {"path": message_path}}
            },
        },
        {
            "id": "modal_dismiss",
            "component": {
                "Button": {
                    "child": "modal_dismiss_text",
                    "action": {"name": dismiss_action},
                    "variant": "primary",
                }
            },
        },
        {
            "id": "modal_dismiss_text",
            "component": {
                "Text": {"text": {"literalString": "Dismiss"}}
            },
        },
    ]


def resource_entry(
    name: str,
    resource_type: str,
    region: str,
    status: str,
    issue: str = "",
    usage_percent: int = 0,
) -> dict[str, Any]:
    """Build a keyed valueMap entry for a resource list item."""
    entry: dict[str, Any] = {
        "key": name,
        "valueMap": [
            {"key": "name", "valueString": name},
            {"key": "type", "valueString": resource_type},
            {"key": "region", "valueString": region},
            {"key": "status_icon", "valueString": resource_status_icon(status)},
            {"key": "status_label", "valueString": resource_status_label(status)},
            {"key": "issue", "valueString": issue},
            {"key": "usage_percent", "valueNumber": usage_percent},
        ],
    }
    return entry


def _resource_dashboard_components() -> list[dict[str, Any]]:
    """KPMG widget composition: branded header, metric row, status-panel resource cards."""
    return [
        {
            "id": "main_column",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "header_card",
                            "metrics_row",
                            "resource_list",
                        ]
                    },
                    "alignment": "stretch",
                }
            },
        },
        {
            "id": "header_card",
            "component": {"Card": {"child": "header_column"}},
        },
        {
            "id": "header_column",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "header_icon",
                            "header_title",
                            "header_subtitle",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "header_icon",
            "component": {
                "Icon": {"name": {"literalString": "cloud"}}
            },
        },
        {
            "id": "header_title",
            "component": {
                "Text": {
                    "text": {"literalString": "Cloud Resource Dashboard"},
                    "usageHint": "h2",
                }
            },
        },
        {
            "id": "header_subtitle",
            "component": {
                "Text": {
                    "text": {"path": "/summary"},
                    "usageHint": "h4",
                }
            },
        },
        {
            "id": "metrics_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            "metric_healthy",
                            "metric_warning",
                            "metric_error",
                        ]
                    },
                    "distribution": "spaceEvenly",
                }
            },
        },
        {
            "id": "metric_healthy",
            "component": {"Card": {"child": "metric_healthy_col"}},
        },
        {
            "id": "metric_healthy_col",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "metric_healthy_label",
                            "metric_healthy_value",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "metric_healthy_label",
            "component": {
                "Text": {
                    "text": {"path": "/metrics/healthy/label"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "metric_healthy_value",
            "component": {
                "Text": {
                    "text": {"path": "/metrics/healthy/value"},
                    "usageHint": "h2",
                }
            },
        },
        {
            "id": "metric_warning",
            "component": {"Card": {"child": "metric_warning_col"}},
        },
        {
            "id": "metric_warning_col",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "metric_warning_label",
                            "metric_warning_value",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "metric_warning_label",
            "component": {
                "Text": {
                    "text": {"path": "/metrics/warning/label"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "metric_warning_value",
            "component": {
                "Text": {
                    "text": {"path": "/metrics/warning/value"},
                    "usageHint": "h2",
                }
            },
        },
        {
            "id": "metric_error",
            "component": {"Card": {"child": "metric_error_col"}},
        },
        {
            "id": "metric_error_col",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "metric_error_label",
                            "metric_error_value",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "metric_error_label",
            "component": {
                "Text": {
                    "text": {"path": "/metrics/error/label"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "metric_error_value",
            "component": {
                "Text": {
                    "text": {"path": "/metrics/error/value"},
                    "usageHint": "h2",
                }
            },
        },
        {
            "id": "resource_list",
            "component": {
                "List": {
                    "direction": "vertical",
                    "children": {
                        "template": {
                            "componentId": "resource_card_template",
                            "dataBinding": "/resources",
                        }
                    },
                }
            },
        },
        {
            "id": "resource_card_template",
            "component": {"Card": {"child": "resource_card_column"}},
        },
        {
            "id": "resource_card_column",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "resource_card_header_row",
                            "resource_status_row",
                            "resource_type_row",
                            "resource_region_row",
                            "resource_issue_text",
                            "resource_usage_slider",
                            "resource_action_button",
                        ]
                    },
                    "alignment": "stretch",
                }
            },
        },
        {
            "id": "resource_card_header_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            "resource_title",
                            "resource_kpmg_brand",
                        ]
                    },
                    "alignment": "center",
                    "distribution": "spaceBetween",
                }
            },
        },
        {
            "id": "resource_title",
            "component": {
                "Text": {
                    "text": {"path": "/name"},
                    "usageHint": "h5",
                }
            },
        },
        {
            "id": "resource_kpmg_brand",
            "component": {
                "Text": {
                    "text": {"literalString": "KPMG"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "resource_status_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            "resource_status_icon",
                            "resource_status_text",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "resource_status_icon",
            "component": {
                "Icon": {"name": {"path": "/status_icon"}}
            },
        },
        {
            "id": "resource_status_text",
            "component": {
                "Text": {
                    "text": {"path": "/status_label"},
                    "usageHint": "h5",
                }
            },
        },
        {
            "id": "resource_type_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            "resource_type_icon",
                            "resource_type_col",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "resource_type_icon",
            "component": {
                "Icon": {"name": {"literalString": "category"}}
            },
        },
        {
            "id": "resource_type_col",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "resource_type_label",
                            "resource_type_value",
                        ]
                    }
                }
            },
        },
        {
            "id": "resource_type_label",
            "component": {
                "Text": {
                    "text": {"literalString": "Service Type"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "resource_type_value",
            "component": {
                "Text": {
                    "text": {"path": "/type"},
                    "usageHint": "h5",
                }
            },
        },
        {
            "id": "resource_region_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": [
                            "resource_region_icon",
                            "resource_region_col",
                        ]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "resource_region_icon",
            "component": {
                "Icon": {"name": {"literalString": "place"}}
            },
        },
        {
            "id": "resource_region_col",
            "component": {
                "Column": {
                    "children": {
                        "explicitList": [
                            "resource_region_label",
                            "resource_region_value",
                        ]
                    }
                }
            },
        },
        {
            "id": "resource_region_label",
            "component": {
                "Text": {
                    "text": {"literalString": "Region"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "resource_region_value",
            "component": {
                "Text": {
                    "text": {"path": "/region"},
                    "usageHint": "h5",
                }
            },
        },
        {
            "id": "resource_issue_text",
            "component": {
                "Text": {
                    "text": {"path": "/issue"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "resource_usage_slider",
            "component": {
                "Slider": {
                    "value": {"path": "/usage_percent"},
                    "minValue": 0,
                    "maxValue": 100,
                }
            },
        },
        {
            "id": "resource_action_button",
            "component": {
                "Button": {
                    "child": "resource_action_text",
                    "primary": True,
                    "action": VIEW_RESOURCE_ACTION,
                }
            },
        },
        {
            "id": "resource_action_text",
            "component": {
                "Text": {
                    "text": {"literalString": "View Details"},
                    "usageHint": "h5",
                }
            },
        },
    ]


def _resource_dashboard_data(
    resources: list[dict[str, Any]],
    summary: str,
) -> list[dict[str, Any]]:
    counts = {"healthy": 0, "warning": 0, "error": 0}
    for resource in resources:
        status = str(resource.get("status", "")).lower()
        if status in counts:
            counts[status] += 1

    resource_entries = [
        resource_entry(
            name=r["name"],
            resource_type=r["type"],
            region=r["region"],
            status=r["status"],
            issue=r.get("issue", ""),
            usage_percent=int(r.get("usage_percent", 0) or 0),
        )
        for r in resources
    ]

    return [
        {"key": "summary", "valueString": summary},
        {
            "key": "metrics",
            "valueMap": [
                {
                    "key": "healthy",
                    "valueMap": [
                        {"key": "label", "valueString": "Healthy"},
                        {"key": "value", "valueString": str(counts["healthy"])},
                    ],
                },
                {
                    "key": "warning",
                    "valueMap": [
                        {"key": "label", "valueString": "Warning"},
                        {"key": "value", "valueString": str(counts["warning"])},
                    ],
                },
                {
                    "key": "error",
                    "valueMap": [
                        {"key": "label", "valueString": "Error"},
                        {"key": "value", "valueString": str(counts["error"])},
                    ],
                },
            ],
        },
        {"key": "resources", "valueMap": resource_entries},
    ]


def resource_dashboard(
    surface_id: str = "kpmg-resource-dashboard",
    title: str = "Cloud Resource Dashboard",
    summary: str = "",
    resources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    KPMG cloud resource dashboard composing BrandedHeader, MetricCard, and
    StatusPanel/DataFieldRow patterns from the shared widget library.
    """
    del title  # header title is fixed in the KPMG layout
    resource_list = resources or []
    if not summary and resource_list:
        counts = {"healthy": 0, "warning": 0, "error": 0}
        for resource in resource_list:
            status = str(resource.get("status", "")).lower()
            if status in counts:
                counts[status] += 1
        summary = (
            f"{len(resource_list)} resources found: "
            f"{counts['healthy']} healthy, {counts['warning']} warning, "
            f"{counts['error']} error."
        )

    return surface_messages(
        surface_id=surface_id,
        root="main_column",
        components=_resource_dashboard_components(),
        data_contents=_resource_dashboard_data(resource_list, summary),
    )


def resource_detail(
    resource: dict[str, Any],
    surface_id: str | None = None,
) -> list[dict[str, Any]]:
    """KPMG detail card for a single cloud resource (KpmgDataFieldRow pattern)."""
    name = str(resource["name"])
    surface_id = surface_id or f"kpmg-resource-detail-{name}"

    child_ids = ["detail_header_row", "detail_status_row"]
    field_ids: list[str] = []
    components: list[dict[str, Any]] = [
        {
            "id": "detail_column",
            "component": {
                "Column": {
                    "children": {"explicitList": child_ids},
                    "alignment": "stretch",
                }
            },
        },
        {
            "id": "detail_header_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": ["detail_title", "detail_kpmg_brand"]
                    },
                    "alignment": "center",
                    "distribution": "spaceBetween",
                }
            },
        },
        {
            "id": "detail_title",
            "component": {
                "Text": {
                    "text": {"literalString": name},
                    "usageHint": "h2",
                }
            },
        },
        {
            "id": "detail_kpmg_brand",
            "component": {
                "Text": {
                    "text": {"literalString": "KPMG"},
                    "usageHint": "caption",
                }
            },
        },
        {
            "id": "detail_status_row",
            "component": {
                "Row": {
                    "children": {
                        "explicitList": ["detail_status_icon", "detail_status_text"]
                    },
                    "alignment": "center",
                }
            },
        },
        {
            "id": "detail_status_icon",
            "component": {
                "Icon": {
                    "name": {
                        "literalString": resource_status_icon(
                            str(resource.get("status", ""))
                        )
                    }
                }
            },
        },
        {
            "id": "detail_status_text",
            "component": {
                "Text": {
                    "text": {
                        "literalString": resource_status_label(
                            str(resource.get("status", ""))
                        )
                    },
                    "usageHint": "h5",
                }
            },
        },
    ]

    for field_key, label, icon in _DETAIL_FIELD_SPECS:
        if field_key not in resource or resource[field_key] in (None, ""):
            continue
        row_id = f"detail_{field_key}"
        field_ids.append(row_id)
        components.extend(data_field_row(row_id, label, f"/{field_key}", icon=icon))

    child_ids.extend(field_ids)
    if resource.get("usage_percent"):
        child_ids.extend(["detail_usage_label", "detail_usage_slider"])
        components.extend(
            [
                {
                    "id": "detail_usage_label",
                    "component": {
                        "Text": {
                            "text": {
                                "literalString": (
                                    f"Storage usage: {resource['usage_percent']}%"
                                )
                            },
                            "usageHint": "caption",
                        }
                    },
                },
                {
                    "id": "detail_usage_slider",
                    "component": {
                        "Slider": {
                            "value": {"path": "/usage_percent"},
                            "minValue": 0,
                            "maxValue": 100,
                        }
                    },
                },
            ]
        )

    components[0]["component"]["Column"]["children"]["explicitList"] = child_ids

    data_contents: list[dict[str, Any]] = []
    for key, value in resource.items():
        if value in (None, ""):
            continue
        if key == "usage_percent":
            data_contents.append({"key": key, "valueNumber": int(value)})
        else:
            data_contents.append({"key": key, "valueString": str(value)})

    return surface_messages(
        surface_id=surface_id,
        root="detail_column",
        components=components,
        data_contents=data_contents,
    )


def surface_messages(
    surface_id: str,
    root: str,
    components: list[dict[str, Any]],
    data_contents: list[dict[str, Any]] | None = None,
    styles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Assemble a complete inline-pattern A2UI payload."""
    messages: list[dict[str, Any]] = [
        begin_surface(surface_id, root, styles),
        surface_update(surface_id, components),
    ]
    if data_contents:
        messages.append(data_model_update(surface_id, data_contents))
    return messages
