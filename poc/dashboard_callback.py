"""A2UI render and action callbacks for the cloud dashboard POC."""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse

from action_utils import (
    VIEW_RESOURCE_DETAILS,
    extract_user_action,
    normalize_action_context,
)
from resources import (
    consume_pending_detail,
    consume_pending_profile,
    consume_pending_resources,
    find_resource,
    get_resource_details,
)
from widgets.python.a2ui_utils import a2ui_callback, llm_response_from_messages
from widgets.python.builders import resource_dashboard, resource_detail, user_profile


def _response_already_rendered(llm_response: LlmResponse) -> bool:
    if not llm_response.content or not llm_response.content.parts:
        return False
    for part in llm_response.content.parts:
        if part.inline_data:
            return True
    return False


def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """Handle View Details button clicks without an extra LLM round-trip."""
    del callback_context

    action = extract_user_action(llm_request)
    if not action or action.get("name") != VIEW_RESOURCE_DETAILS:
        return None

    context = normalize_action_context(action.get("context"))
    resource_name = context.get("name")
    if not resource_name:
        return None

    resource = find_resource(str(resource_name))
    if resource is None:
        return None

    messages = resource_detail(resource)
    return llm_response_from_messages(messages)


def cloud_dashboard_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Render KPMG dashboard or detail UI after tool calls and model output."""
    if _response_already_rendered(llm_response):
        return llm_response

    detail = consume_pending_detail()
    if detail:
        return llm_response_from_messages(resource_detail(detail))

    profile = consume_pending_profile()
    if profile:
        return llm_response_from_messages(user_profile(profile))

    resources = consume_pending_resources()
    if resources:
        return llm_response_from_messages(resource_dashboard(resources=resources))

    return a2ui_callback(callback_context, llm_response)
