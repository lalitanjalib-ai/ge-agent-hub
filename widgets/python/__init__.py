"""Python helpers for KPMG A2UI widgets."""

from widgets.python.a2ui_utils import a2ui_callback
from widgets.python.builders import (
    action_bar,
    begin_surface,
    branded_header,
    confirmation_modal,
    data_field_row,
    metric_card,
    resource_dashboard,
    resource_entry,
    resource_status_icon,
    resource_status_label,
    resource_detail,
    status_panel,
    surface_messages,
)
from widgets.python.provider import KpmgWidgetsCatalog
from widgets.python.theme import KPMG_SURFACE_STYLES, KPMG_THEME

__all__ = [
    "KpmgWidgetsCatalog",
    "KPMG_THEME",
    "KPMG_SURFACE_STYLES",
    "a2ui_callback",
    "begin_surface",
    "branded_header",
    "data_field_row",
    "metric_card",
    "resource_dashboard",
    "resource_entry",
    "resource_status_icon",
    "resource_status_label",
    "resource_detail",
    "status_panel",
    "confirmation_modal",
    "action_bar",
    "surface_messages",
]
