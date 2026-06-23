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
    "healthy": "checkCircle",
    "warning": "warning",
    "error": "error",
    "info": "info",
}


def resource_status_icon(status: str) -> str:
    """Map a resource status label to a Material icon name."""
    return STATUS_ICON_MAP.get(status.lower(), "info")


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
