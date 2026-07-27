"""
Base A2UI Executor — Shared A2A protocol handler for all agents.

Each agent creates a thin subclass that just specifies which agent config
to load. All A2A/A2UI parsing, session management, and response handling
lives here.

Usage:
    class MyAgentExecutor(BaseA2UIExecutor):
        AGENT_CONFIG_NAME = "my_agent"
"""

import logging
import re
import json
import uuid
from typing import List

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    TaskState, TextPart, DataPart, UnsupportedOperationError,
    Message, Role, Part,
)
from a2a.utils.errors import ServerError

try:
    from a2a.utils import new_agent_parts_message
except ImportError:
    def new_agent_parts_message(parts, context_id, task_id):
        return Message(
            message_id=str(uuid.uuid4()),
            role=Role.agent,
            parts=parts,
        )

from google.adk.runners import Runner
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

from agents._base.config_loader import load_agent_config
from agents._base.exceptions import OBOAuthError
from agents._base.user_context import (
    extract_user_token,
    find_forwarded_token_in_mapping,
    get_credential_mode,
    reset_credential_mode,
    reset_user_token,
    set_credential_mode,
    set_user_token,
)

logger = logging.getLogger(__name__)

# Fixed, non-technical message shown to the end user whenever the underlying
# tool call failed due to an OBO auth misconfiguration (OBOAuthError /
# OBO_AUTH_ERROR sentinel). The real diagnostic detail is only ever logged
# server-side (logger.error calls in agents/_base/user_context.py and the
# individual tool modules) — never exposed to the chat UI.
_OBO_AUTH_ERROR_USER_MESSAGE = (
    "Sorry, I'm unable to access your employee records right now due to an "
    "authentication issue. Please try again later or contact your administrator."
)


A2UI_MIME_TYPE = "application/json+a2ui"
A2UI_OPEN_TAG = "<a2ui-json>"
A2UI_CLOSE_TAG = "</a2ui-json>"

_A2UI_BLOCK_RE = re.compile(
    f"{re.escape(A2UI_OPEN_TAG)}(.*?){re.escape(A2UI_CLOSE_TAG)}", re.DOTALL
)


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------

def _sanitize_json(raw: str) -> str:
    """Remove markdown code fences from JSON strings."""
    s = raw.strip()
    if s.startswith("```json"):
        s = s[len("```json"):]
    elif s.startswith("```"):
        s = s[len("```"):]
    if s.endswith("```"):
        s = s[:-len("```")]
    return s.strip()


def _create_a2ui_part(data: dict) -> Part:
    """Create an A2UI DataPart."""
    return Part(root=DataPart(data=data, metadata={"mimeType": A2UI_MIME_TYPE}))


def parse_response_to_parts(content: str) -> List[Part]:
    """Parse LLM response text, extracting A2UI JSON blocks as DataParts."""
    matches = list(_A2UI_BLOCK_RE.finditer(content))
    if not matches:
        clean = content.strip()
        return [Part(root=TextPart(text=clean))] if clean else []

    parts: List[Part] = []
    last_end = 0

    for match in matches:
        start, end = match.span()
        text_before = content[last_end:start].strip()
        if text_before:
            parts.append(Part(root=TextPart(text=text_before)))
        try:
            json_str = _sanitize_json(match.group(1))
            payload = json.loads(json_str)
            if isinstance(payload, list):
                for item in payload:
                    parts.append(_create_a2ui_part(item))
            else:
                parts.append(_create_a2ui_part(payload))
        except Exception as e:
            logger.error(f"Failed to parse A2UI JSON block: {e}")
        last_end = end

    trailing = content[last_end:].strip()
    if trailing:
        parts.append(Part(root=TextPart(text=trailing)))

    return parts


# ---------------------------------------------------------------------------
# Action-to-query builder (config-driven)
# ---------------------------------------------------------------------------

def build_query_from_action(action_name: str, context: dict, actions_config: dict) -> str:
    """Convert a UI action into a natural language query using config templates.

    Args:
        action_name: The action name from the A2UI button click.
        context: The action context dict from the button.
        actions_config: The 'agent.actions' section from the YAML config.
    """
    action_def = actions_config.get(action_name)

    if action_def is None:
        return f"User submitted a UI action: {action_name} with data: {context}"

    template = action_def.get("template", "")
    field_keys = action_def.get("fields", [])

    # Build fields string if this action has field definitions
    if field_keys:
        fields_parts = []
        for key in field_keys:
            if key in context:
                fields_parts.append(f"{key}={context[key]}")
        context["fields"] = ", ".join(fields_parts)

    # Format template with context values
    try:
        return template.format(**context)
    except KeyError:
        # Fallback: just dump what we have
        return f"{action_name}: {context}"


# ---------------------------------------------------------------------------
# Base Executor
# ---------------------------------------------------------------------------

class BaseA2UIExecutor(AgentExecutor):
    """Base A2A executor with A2UI support. Subclass and set AGENT_CONFIG_NAME."""

    # Subclasses MUST override this
    AGENT_CONFIG_NAME: str = None

    def __init__(self):
        if self.AGENT_CONFIG_NAME is None:
            raise ValueError(
                f"{self.__class__.__name__} must set AGENT_CONFIG_NAME "
                f"(e.g., AGENT_CONFIG_NAME = 'employee_verification')"
            )
        self.agent = None
        self.runner = None
        self._config = None

    def _get_config(self) -> dict:
        """Load and cache the agent config."""
        if self._config is None:
            self._config = load_agent_config(self.AGENT_CONFIG_NAME)
        return self._config

    def _init_agent(self):
        """Lazy-initialize the ADK agent and Runner."""
        if self.agent is not None:
            return

        # Import here to avoid circular imports at module level
        agent_module_name = f"agents.{self.AGENT_CONFIG_NAME}.agent"
        import importlib
        agent_module = importlib.import_module(agent_module_name)
        self.agent = agent_module.get_agent()

        config = self._get_config()
        agent_name = config.get("agent", {}).get("name", self.AGENT_CONFIG_NAME)

        self.runner = Runner(
            app_name=agent_name,
            agent=self.agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )
        logger.info(f"{self.__class__.__name__} initialized runner for {agent_name}")

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute a request — handles both text input and UI button actions."""
        self._init_agent()
        config = self._get_config()

        query = ""
        ui_event_part = None

        # Check for A2UI button action in the message parts
        if context.message and context.message.parts:
            for part in context.message.parts:
                if isinstance(part.root, DataPart) and "userAction" in part.root.data:
                    ui_event_part = part.root.data["userAction"]
                    break

        if ui_event_part:
            logger.info(f"Received A2UI ClientEvent: {ui_event_part}")
            action_name = ui_event_part.get("name")
            action_context = ui_event_part.get("context", {})
            actions_config = config.get("agent", {}).get("actions", {})
            query = build_query_from_action(action_name, action_context, actions_config)
        else:
            query = context.get_user_input()

        logger.info(f"{self.__class__.__name__} executing query: {query}")

        deploy_cfg = config.get("deploy", {})
        obo_mode = deploy_cfg.get("obo_credential_mode")
        mode_reset = set_credential_mode(obo_mode) if obo_mode else None
        logger.info("OBO credential mode: %s", get_credential_mode())

        # On-Behalf-Of: capture the user token forwarded by Gemini Enterprise so
        # downstream tools can act as the logged-in user (see agents/_base/user_context).
        user_token = extract_user_token(context)

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.submit()
        await updater.start_work()

        token_reset = None
        try:
            # Get or create session. Seed the forwarded user token into session
            # state so ADK-native auth-aware tools can also consume it.
            initial_state = {"ge_user_token": user_token} if user_token else {}
            session = await self.runner.session_service.get_session(
                app_name=self.runner.app_name,
                user_id="user",
                session_id=context.context_id,
            )
            if session is None:
                session = await self.runner.session_service.create_session(
                    app_name=self.runner.app_name,
                    user_id="user",
                    state=initial_state,
                    session_id=context.context_id,
                )
            elif user_token:
                # Refresh token on existing sessions — GE forwards per-request but
                # Agent Engine reuses session state across turns in the same context.
                state = getattr(session, "state", None)
                if isinstance(state, dict):
                    state["ge_user_token"] = user_token

            # Fallback: some hosting paths (e.g. GE Agent Runtime) inject the
            # Entra token directly into ADK session state under an undocumented
            # key. If request-level extraction found nothing, scan the resolved
            # session state for a Microsoft-issued JWT.
            if not user_token and session is not None:
                session_token = find_forwarded_token_in_mapping(
                    getattr(session, "state", None)
                )
                if session_token:
                    logger.info(
                        "OBO: forwarded token found by scan of ADK session state (mode=%s)",
                        get_credential_mode(),
                    )
                    user_token = session_token

            if user_token:
                logger.info(
                    "OBO: forwarded user token resolved (mode=%s) — tools will run on behalf of the user",
                    get_credential_mode(),
                )
                from agents._base.user_context import log_obo_principal

                log_obo_principal(user_token)
            else:
                logger.info("OBO: no forwarded user token — tools will fall back to ADC (service account)")

            token_reset = set_user_token(user_token)

            content = types.Content(role="user", parts=[types.Part(text=query)])

            # NOTE: runner.run_async() returns an async generator that wraps
            # the ADK agent invocation (and its OpenTelemetry span for the
            # root agent node). Breaking out of an `async for` loop over it
            # early — as soon as we see the final response — leaves the
            # generator half-consumed. Python then only calls its `aclose()`
            # during garbage collection, which can run in a *different*
            # asyncio task/context than the one that opened the span. That
            # cross-context close is what produces:
            #     WARNING: Root node <Agent> was cancelled.
            #     ERROR:   Failed to detach context (OpenTelemetry GeneratorExit)
            # and can race with the A2A request handler delivering the
            # "completed" status back to the client, causing the reply to
            # never surface even though the model already generated it.
            #
            # Fix: explicitly close the generator ourselves, in this same
            # task/context, right after we're done consuming it — regardless
            # of whether we exit via `break`, fall through, or raise.
            run_agen = self.runner.run_async(
                session_id=session.id,
                user_id="user",
                new_message=content,
            )
            try:
                async for event in run_agen:
                    if hasattr(event, "is_final_response") and event.is_final_response():
                        answer_text = ""
                        if event.content and event.content.parts:
                            answer_text = "\n".join(
                                [part.text for part in event.content.parts if part.text]
                            )

                        if answer_text:
                            # Defense-in-depth: the tool functions embed the
                            # OBOAuthError.ERROR_CODE sentinel in their JSON
                            # error response when the forwarded token is
                            # definitively unusable (see tools/employee/*.py).
                            # ADK's function-calling loop may fold that JSON
                            # straight into the LLM's final prose response
                            # before we ever see a distinct "tool response"
                            # event, so the most reliable place to catch it is
                            # here: a simple substring check on the final
                            # answer text, replacing it wholesale with a
                            # fixed, non-technical message. Full diagnostic
                            # detail is already in the server logs (see
                            # agents/_base/user_context.py and the tool
                            # modules) — never re-derived or shown here.
                            if OBOAuthError.ERROR_CODE in answer_text:
                                logger.error(
                                    "OBO: tool response contained %s sentinel — "
                                    "replacing final answer with generic user-facing "
                                    "message (see earlier OBO error logs for detail)",
                                    OBOAuthError.ERROR_CODE,
                                )
                                answer_text = _OBO_AUTH_ERROR_USER_MESSAGE

                            final_parts = parse_response_to_parts(answer_text)
                            await updater.update_status(
                                TaskState.completed,
                                new_agent_parts_message(
                                    final_parts,
                                    context.context_id,
                                    context.task_id,
                                ),
                                final=True,
                            )

                        else:
                            await updater.update_status(
                                TaskState.completed,
                                new_agent_parts_message(
                                    [Part(root=TextPart(text="No response generated."))],
                                    context.context_id,
                                    context.task_id,
                                ),
                                final=True,
                            )
                        break
            finally:
                # Always close the generator in this context, before we
                # return, so OpenTelemetry span/contextvar teardown happens
                # synchronously here rather than during unpredictable GC.
                await run_agen.aclose()

        except OBOAuthError as e:
            # Also catch OBOAuthError here in case it propagates all the way
            # up uncaught (e.g. ADK re-raises rather than swallowing it into
            # a function response) — always show the same fixed, generic
            # message; full detail already logged where it was raised.
            logger.error(
                f"OBO auth error in {self.__class__.__name__}: {e}"
            )
            await updater.update_status(
                TaskState.failed,
                message=Message(
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=_OBO_AUTH_ERROR_USER_MESSAGE))],
                ),
            )
            return
        except Exception as e:
            logger.error(
                f"Error in {self.__class__.__name__}: {e}", exc_info=True
            )
            await updater.update_status(
                TaskState.failed,
                message=Message(
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[Part(root=TextPart(text=f"An error occurred: {str(e)}"))],
                ),
            )
            return

        finally:
            # Always clear the per-request user token from the context.
            if token_reset is not None:
                reset_user_token(token_reset)
            if mode_reset is not None:
                reset_credential_mode(mode_reset)

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise ServerError(error=UnsupportedOperationError())
