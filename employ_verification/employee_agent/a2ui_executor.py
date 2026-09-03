"""A2A AgentExecutor that renders A2UI components in the final response.

`google.adk.a2a.executor.a2a_agent_executor.A2aAgentExecutor` (the stock ADK
executor) streams the model's final text back as a plain `TextPart` — it has
no awareness of the `<a2ui-json>...</a2ui-json>` blocks the agent's
instruction (see `employee_agent/agent.py`) tells the model to emit for
interactive forms/lists. Without this executor, those blocks show up as
literal JSON text in the chat instead of a rendered A2UI component.

This executor is a thin wrapper: it drives the same ADK `Runner` the stock
executor would, then pipes the final response text through the `a2ui`
package's `parse_response_to_parts` (see `a2ui.a2a.parts`) to split it into
`TextPart`s and A2UI `DataPart`s (mimeType `application/json+a2ui`), which is
what the Gemini Enterprise client actually looks for to render rich UI
instead of raw text.
"""

from __future__ import annotations

import contextlib
import logging
import re
import uuid

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import DataPart, Message, Part, Role, TaskState, TextPart, UnsupportedOperationError
from a2a.utils.errors import ServerError
from google.adk.runners import Runner
from google.genai import types

logger = logging.getLogger(__name__)

# Matches a well-formed <a2ui-json>...</a2ui-json> block, or an *unterminated*
# one (open tag with no matching close tag — e.g. output truncated by a
# token/length limit) through to the end of the string.
_A2UI_BLOCK_RE = re.compile(
    r"<a2ui-json>.*?(?:</a2ui-json>|$)", re.DOTALL
)


def _strip_a2ui_markup(text: str) -> str:
    """Remove any (possibly broken/unterminated) <a2ui-json> block from
    fallback plain text, so a failed-to-parse UI payload never leaks to the
    user as raw/garbled JSON — which the client may render as nothing at all
    now that this agent advertises the A2UI extension on its AgentCard."""
    cleaned = _A2UI_BLOCK_RE.sub("", text).strip()
    return cleaned or "Sorry, I couldn't format that response. Please try again."


def _extract_client_action_query(context: RequestContext) -> str | None:
    """Detect an A2UI client action (e.g. a "Verify" button click) on the
    incoming message and turn it into a natural-language instruction the
    ADK agent can act on.

    `RequestContext.get_user_input()` only extracts TextPart content and
    silently ignores DataParts — but when a user clicks a button in a
    rendered A2UI surface, Gemini Enterprise sends back an
    A2uiClientActionMessage (`{"action": {"name": ..., "surfaceId": ...,
    "sourceComponentId": ..., "context": {...}}}`) inside a DataPart, not as
    text. Without this, the agent sees an empty/unrelated query on every
    button click and has nothing to act on.
    """
    message = context.message
    if message is None or not message.parts:
        logger.info("No incoming message/parts to inspect for a client action.")
        return None

    # TEMP DEBUG: log the raw shape of every incoming part so we can confirm
    # (or correct) the exact wire format Gemini Enterprise sends for a
    # button click, instead of guessing at the A2uiClientActionMessage shape.
    for i, part in enumerate(message.parts):
        root = part.root
        logger.info(
            "Incoming message part[%d]: type=%s data=%r",
            i,
            type(root).__name__,
            getattr(root, "data", None) if isinstance(root, DataPart) else getattr(root, "text", None),
        )

    for part in message.parts:
        root = part.root
        if not isinstance(root, DataPart) or not isinstance(root.data, dict):
            continue
        # Tolerate both the documented A2uiClientActionMessage shape
        # ({"action": {"name": ..., "context": ...}}) and a possible
        # flattened shape ({"name": ..., "context": ...} at the top level).
        action = root.data.get("action")
        if not isinstance(action, dict) or not action.get("name"):
            if root.data.get("name"):
                action = root.data
            else:
                continue

        name = action["name"]
        action_context = action.get("context") or {}
        if isinstance(action_context, dict):
            context_str = ", ".join(f"{k}={v}" for k, v in action_context.items())
        else:
            context_str = str(action_context)

        logger.info("Detected A2UI client action: name=%s context=%s", name, action_context)
        return (
            f"[UI action] The user clicked the '{name}' control"
            + (f" with {context_str}" if context_str else "")
            + ". Handle this the same way you would the equivalent typed request."
        )

    return None


try:
    from a2ui.a2a.parts import parse_content_to_parts

    from employee_agent.agent import get_a2ui_parser

    _A2UI_PARSER = get_a2ui_parser()
except ImportError:  # pragma: no cover - a2ui is an optional dependency
    _A2UI_PARSER = None
    logger.warning(
        "a2ui package not available — falling back to plain TextPart "
        "responses (interactive A2UI forms will not render)."
    )


class A2uiAwareExecutor(AgentExecutor):
    """Runs an ADK `Runner` against an A2A request and splits the final
    response into text + A2UI DataParts."""

    def __init__(self, *, runner: Runner) -> None:
        self._runner = runner

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        query = _extract_client_action_query(context) or context.get_user_input()
        user_id = "user"
        session_id = context.context_id

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.submit()
        await updater.start_work()

        try:
            session = await self._runner.session_service.get_session(
                app_name=self._runner.app_name,
                user_id=user_id,
                session_id=session_id,
            )
            if session is None:
                session = await self._runner.session_service.create_session(
                    app_name=self._runner.app_name,
                    user_id=user_id,
                    state={},
                    session_id=session_id,
                )

            content = types.Content(role="user", parts=[types.Part(text=query)])

            final_text = ""
            # Use aclosing() (not a bare `break`) so the underlying async
            # generator's OpenTelemetry span/context managers unwind
            # synchronously in this same task, instead of being finalized
            # later via GeneratorExit from a different context (which was
            # producing "ContextVar ... was created in a different Context"
            # errors and, worse, could abandon the response mid-flight
            # before update_status() below ever ran).
            async with contextlib.aclosing(
                self._runner.run_async(
                    user_id=user_id,
                    session_id=session.id,
                    new_message=content,
                )
            ) as event_stream:
                async for event in event_stream:
                    if event.is_final_response():
                        if event.content and event.content.parts:
                            final_text = "\n".join(
                                p.text for p in event.content.parts if p.text
                            )
                        break

            logger.info(
                "Final response text (%d chars): %s",
                len(final_text),
                final_text[:200],
            )

            clean_fallback = _strip_a2ui_markup(final_text) if final_text else ""

            parts = None
            if _A2UI_PARSER is not None and final_text:
                try:
                    parts = parse_content_to_parts(
                        final_text, parser=_A2UI_PARSER, fallback_text=clean_fallback
                    )
                except Exception as parse_exc:  # noqa: BLE001
                    # Never let a malformed/truncated A2UI JSON block from the
                    # model turn into a dropped/empty response — degrade to
                    # plain text instead.
                    logger.warning(
                        "A2UI parsing raised unexpectedly, falling back to "
                        "plain text: %s",
                        parse_exc,
                    )
                    parts = None
                else:
                    # parse_content_to_parts() only substitutes fallback_text
                    # when it produced zero parts; if it partially succeeded
                    # (e.g. text before a broken block) it may still include
                    # raw/unterminated <a2ui-json> markup in a TextPart —
                    # scrub those too.
                    for i, part in enumerate(parts):
                        if isinstance(part.root, TextPart) and "<a2ui-json>" in part.root.text:
                            parts[i] = Part(root=TextPart(text=_strip_a2ui_markup(part.root.text)))
            if not parts and clean_fallback:
                parts = [Part(root=TextPart(text=clean_fallback))]
            if not parts:
                parts = [Part(root=TextPart(text="No response generated."))]

            await updater.update_status(
                TaskState.completed,
                updater.new_agent_message(parts),
                final=True,
            )

        except Exception as e:  # noqa: BLE001 - surface any failure to the user
            logger.error("Error in A2uiAwareExecutor: %s", e, exc_info=True)
            await updater.update_status(
                TaskState.failed,
                message=Message(
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=f"An error occurred: {e}"))],
                ),
                final=True,
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise ServerError(error=UnsupportedOperationError())
