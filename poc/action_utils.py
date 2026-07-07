"""Parse A2UI userAction events from ADK web button clicks."""

from __future__ import annotations

import json
import re
from typing import Any

from google.adk.models.llm_request import LlmRequest

VIEW_RESOURCE_DETAILS = "view_resource_details"
_A2A_DATAPART_RE = re.compile(
    r"<a2a_datapart_json>(.*?)</a2a_datapart_json>",
    re.DOTALL,
)


def _unwrap_literal(value: object) -> object:
    if not isinstance(value, dict):
        return value
    if "literalString" in value:
        return value["literalString"]
    if "literalNumber" in value:
        return value["literalNumber"]
    if "literalBoolean" in value:
        return value["literalBoolean"]
    return value


def normalize_action_context(context: object) -> dict[str, Any]:
    """Normalize v0.8 context list or flat dict into a string-keyed map."""
    if isinstance(context, dict):
        return {
            str(key): _unwrap_literal(value)
            for key, value in context.items()
        }

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
    for key in ("userAction", "action"):
        if key in value:
            action = value[key]
            return action if isinstance(action, dict) else None
    if value.get("name") == VIEW_RESOURCE_DETAILS:
        return value
    return None


def _decode_part_bytes(data: bytes) -> object | None:
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return None

    match = _A2A_DATAPART_RE.search(text)
    if match:
        text = match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _parse_text_for_user_action(text)


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

    if VIEW_RESOURCE_DETAILS not in text:
        return None

    context_match = re.search(
        r'"context"\s*:\s*(\{[^{}]*"name"\s*:\s*"([^"]+)"[^{}]*\})',
        text,
    )
    if context_match:
        try:
            context = json.loads(context_match.group(1))
        except json.JSONDecodeError:
            context = {"name": context_match.group(2)}
        return {"name": VIEW_RESOURCE_DETAILS, "context": context}

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

            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                payload = _decode_part_bytes(inline.data)
                if isinstance(payload, dict):
                    action = _user_action_from_value(payload)
                    if not action and isinstance(payload.get("data"), dict):
                        action = _user_action_from_value(payload["data"])
                    if action:
                        return action

            metadata = getattr(part, "part_metadata", None)
            if isinstance(metadata, dict):
                action = _user_action_from_value(metadata)
                if action:
                    return action

    return None
