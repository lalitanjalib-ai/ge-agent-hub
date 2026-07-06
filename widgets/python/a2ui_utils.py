"""
A2UI rendering utilities for local ADK web development.

Based on the Google Codelab "Frontend Experiences with ADK and A2UI":
https://codelabs.developers.google.com/next26/adk-a2ui
"""

from __future__ import annotations

import json
import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_response import LlmResponse
from google.genai import types

A2UI_MESSAGE_KEYS = frozenset(
    {"beginRendering", "surfaceUpdate", "dataModelUpdate", "deleteSurface"}
)
_A2UI_MIME = "application/json+a2ui"


def _is_a2ui_message(value: object) -> bool:
    return (
        isinstance(value, dict)
        and any(key in value for key in A2UI_MESSAGE_KEYS)
    )


def _unwrap_wire_format(value: object) -> object | None:
    if not isinstance(value, dict) or value.get("kind") != "data":
        return None
    metadata = value.get("metadata") or {}
    if metadata.get("mimeType") != _A2UI_MIME:
        return None
    return value.get("data")


def _collect_a2ui_messages(value: object) -> list[dict]:
    if value is None:
        return []

    unwrapped = _unwrap_wire_format(value)
    if unwrapped is not None:
        return _collect_a2ui_messages(unwrapped)

    if isinstance(value, list):
        messages: list[dict] = []
        for item in value:
            messages.extend(_collect_a2ui_messages(item))
        return messages

    if _is_a2ui_message(value):
        return [value]

    return []


def _wrap_a2ui_part(a2ui_message: dict) -> types.Part:
    datapart_json = json.dumps(
        {
            "kind": "data",
            "metadata": {"mimeType": _A2UI_MIME},
            "data": a2ui_message,
        }
    )
    blob_data = (
        b"<a2a_datapart_json>"
        + datapart_json.encode("utf-8")
        + b"</a2a_datapart_json>"
    )
    return types.Part(
        inline_data=types.Blob(
            data=blob_data,
            mime_type="text/plain",
        )
    )


def _normalize_data_model(message: dict) -> dict:
    update = message.get("dataModelUpdate")
    if not update:
        return message

    for entry in update.get("contents", []):
        if "valueList" not in entry:
            continue
        items = entry.pop("valueList")
        entry["valueMap"] = [
            {
                "key": item.get("key") or f"item_{index}",
                "valueMap": item.get("valueMap", item),
            }
            for index, item in enumerate(items)
            if isinstance(item, dict)
        ]

    return message


def _parse_json_values(text: str) -> list[object]:
    values: list[object] = []
    index = 0
    decoder = json.JSONDecoder()

    while index < len(text):
        while index < len(text) and text[index] not in "[{":
            index += 1
        if index >= len(text):
            break
        try:
            parsed, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index += 1
            continue
        values.append(parsed)
        index = end

    return values


def _looks_like_a2ui_text(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "beginRendering",
            "surfaceUpdate",
            "dataModelUpdate",
            _A2UI_MIME,
            "<a2ui-json>",
            "<a2a_datapart_json>",
        )
    )


def _extract_a2ui_messages(text: str) -> list[dict]:
    text = text.strip()
    if not text or not _looks_like_a2ui_text(text):
        return []

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].strip()

    messages: list[dict] = []

    if "<a2a_datapart_json>" in text:
        for block in re.findall(
            r"<a2a_datapart_json>(.*?)</a2a_datapart_json>", text, re.DOTALL
        ):
            try:
                parsed = json.loads(block.strip())
            except json.JSONDecodeError:
                continue
            messages.extend(_collect_a2ui_messages(parsed))

    if "<a2ui-json>" in text:
        for block in re.findall(r"<a2ui-json>(.*?)</a2ui-json>", text, re.DOTALL):
            messages.extend(_extract_a2ui_messages(block.strip()))

    if messages:
        return messages

    parsed_values = _parse_json_values(text)
    if not parsed_values and "{" in text:
        try:
            fixed = "[" + re.sub(r"\}\s*\{", "},{", text) + "]"
            parsed_values = _parse_json_values(fixed)
        except re.error:
            parsed_values = []

    for parsed in parsed_values:
        messages.extend(_collect_a2ui_messages(parsed))

    seen: set[str] = set()
    unique: list[dict] = []
    for message in messages:
        key = json.dumps(message, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)

    return unique


def a2ui_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    del callback_context

    if not llm_response.content or not llm_response.content.parts:
        return None

    for part in llm_response.content.parts:
        if not part.text:
            continue

        a2ui_messages = _extract_a2ui_messages(part.text)
        if not a2ui_messages:
            continue

        new_parts = [
            _wrap_a2ui_part(_normalize_data_model(message))
            for message in a2ui_messages
        ]
        return LlmResponse(
            content=types.Content(role="model", parts=new_parts),
            custom_metadata={"a2a:response": "true"},
        )

    return None


def llm_response_from_messages(messages: list[dict]) -> LlmResponse:
    """Wrap A2UI protocol messages for adk web rendering."""
    new_parts = [
        _wrap_a2ui_part(_normalize_data_model(message))
        for message in messages
    ]
    return LlmResponse(
        content=types.Content(role="model", parts=new_parts),
        custom_metadata={"a2a:response": "true"},
    )
