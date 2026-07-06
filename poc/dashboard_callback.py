"""Render KPMG resource dashboard UI after get_resources tool calls."""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse

from resources import consume_pending_resources
from widgets.python.a2ui_utils import a2ui_callback, llm_response_from_messages
from widgets.python.builders import resource_dashboard


def cloud_dashboard_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Render the shared KPMG dashboard after get_resources instead of model JSON."""
    resources = consume_pending_resources()
    if resources:
        messages = resource_dashboard(resources=resources)
        return llm_response_from_messages(messages)

    return a2ui_callback(callback_context, llm_response)
