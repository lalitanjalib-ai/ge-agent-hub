"""Parse A2UI userAction events from ADK web button clicks."""

from __future__ import annotations

import json
import re
from typing import Any

from google.adk.models.llm_request import LlmRequest

VIEW_RESOURCE_DETAILS = "view_resource_details"


def normalize_action_context(context: object) -> dict[str, Any]:
    """Normalize v0.8 context list or flat dict into a string-keyed map."""
    if isinstance(context, dict):
        return {str(key): value for key, value in context.items()}

    if isinstance(context, list):
        normalized: dict[str, Any] = {}
        for entry in context:
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            if not key:
                continue
            value = entry.get("value")
            if isinstance(value, dict):
                if "literalString" in value:
                    normalized[str(key)] = value["literalString"]
                elif "literalNumber" in value:
                    normalized[str(key)] = value["literalNumber"]
                elif "path" in value:
                    normalized[str(key)] = value["path"]
                else:
                    normalized[str(key)] = value
            else:
                normalized[str(key)] = value
        return normalized

    return {}


def _user_action_from_value(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if "userAction" in value:
        action = value["userAction"]
        return action if isinstance(action, dict) else None
    if "name" in value and ("context" in value or value.get("name")):
        return value
    return None


def _parse_text_for_user_action(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text or "userAction" not in text and VIEW_RESOURCE_DETAILS not in text:
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict):
        action = _user_action_from_value(parsed)
        if action:
            return action

    match = re.search(
        r"\{[^{}]*\"userAction\"\s*:\s*(\{.*?\})[^{}]*\}",
        text,
        re.DOTALL,
    )
    if match:
        try:
            action = json.loads(match.group(1))
            if isinstance(action, dict):
                return action
        except json.JSONDecodeError:
            pass

    if VIEW_RESOURCE_DETAILS in text:
        name_match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
        if name_match:
            return {
                "name": VIEW_RESOURCE_DETAILS,
                "context": {"name": name_match.group(1)},
            }

    return None


def extract_user_action(llm_request: LlmRequest) -> dict[str, Any] | None:
    """Return the latest userAction payload from the inbound LLM request."""
    for content in reversed(llm_request.contents):
        if content.role != "user" or not content.parts:
            continue
        for part in content.parts:
            if part.text:
                action = _parse_text_for_user_action(part.text)
                if action:
                    return action

            for candidate in (
                getattr(part, "data", None),
                getattr(part, "inline_data", None),
            ):
                if candidate is None:
                    continue
                if hasattr(candidate, "data") and candidate.data:
                    try:
                        payload = json.loads(candidate.data.decode("utf-8"))
                    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                        payload = None
                    if isinstance(payload, dict):
                        action = _user_action_from_value(payload)
                        if not action and "data" in payload:
                            action = _user_action_from_value(payload.get("data"))
                        if action:
                            return action

    return None
