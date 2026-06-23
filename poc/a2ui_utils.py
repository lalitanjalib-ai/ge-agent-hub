"""
Convert A2UI JSON in LLM output into rendered components in adk web.

From the ADK + A2UI Codelab:
https://codelabs.developers.google.com/next26/adk-a2ui

Handles both raw JSON arrays (codelab) and <a2ui-json> tagged blocks (A2UI SDK).
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


def _normalize_data_model(message: dict) -> dict:
    """Fix List template data: adk web expects valueMap, not valueList."""
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


def _wrap_a2ui_part(a2ui_message: dict) -> types.Part:
    """Wrap a single A2UI message for rendering in adk web."""
    datapart_json = json.dumps(
        {
            "kind": "data",
            "metadata": {"mimeType": "application/json+a2ui"},
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


def _extract_a2ui_messages(text: str) -> list[dict]:
    """Parse A2UI JSON from LLM text, tolerating fences and tagged blocks."""
    text = text.strip()
    if not text:
        return []

    if not any(key in text for key in A2UI_MESSAGE_KEYS):
        return []

    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].strip()

    # A2UI SDK wraps each message in its own <a2ui-json> block.
    if "<a2ui-json>" in text:
        messages: list[dict] = []
        for block in re.findall(r"<a2ui-json>(.*?)</a2ui-json>", text, re.DOTALL):
            messages.extend(_extract_a2ui_messages(block.strip()))
        return messages

    json_start = None
    for index, char in enumerate(text):
        if char in ("[", "{"):
            json_start = index
            break
    if json_start is None:
        return []

    json_text = text[json_start:]
    try:
        parsed, _ = json.JSONDecoder().raw_decode(json_text)
    except json.JSONDecodeError:
        try:
            fixed = "[" + re.sub(r"\}\s*\{", "},{", json_text) + "]"
            parsed, _ = json.JSONDecoder().raw_decode(fixed)
        except json.JSONDecodeError:
            return []

    if not isinstance(parsed, list):
        parsed = [parsed]

    return [
        message
        for message in parsed
        if isinstance(message, dict)
        and any(key in message for key in A2UI_MESSAGE_KEYS)
    ]


def a2ui_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """Convert A2UI JSON in text output to rendered components."""
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
