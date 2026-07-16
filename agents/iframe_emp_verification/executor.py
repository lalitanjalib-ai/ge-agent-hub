# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
KPMG Audit Issue Tracker — A2A Executor.

Handles A2A protocol execution with:
  - A2UI action routing (cardMoved, issueSelected) → natural language queries
  - Self-healing: if the LLM forgets to include the <a2ui-json> block,
    the executor programmatically appends it from the cached issues data.
"""

import json
import logging
import re
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

try:
    from .agent import get_agent, AGENT_NAME
    from .components import _LAST_ISSUES_DATA, generate_kanban_a2ui_tool
except ImportError:
    from agents.iframe_emp_verification.agent import get_agent, AGENT_NAME
    from agents.iframe_emp_verification.components import _LAST_ISSUES_DATA, generate_kanban_a2ui_tool

logger = logging.getLogger(__name__)

A2UI_MIME_TYPE = "application/json+a2ui"
A2UI_OPEN_TAG = "<a2ui-json>"
A2UI_CLOSE_TAG = "</a2ui-json>"

_A2UI_BLOCK_RE = re.compile(
    f"{re.escape(A2UI_OPEN_TAG)}(.*?){re.escape(A2UI_CLOSE_TAG)}", re.DOTALL
)


def _sanitize_json(raw: str) -> str:
    """Remove markdown code fences and line-continuation backslashes."""
    s = raw.strip()
    if s.startswith("```json"):
        s = s[len("```json"):]
    elif s.startswith("```"):
        s = s[len("```"):]
    if s.endswith("```"):
        s = s[:-len("```")]
    s = re.sub(r"\\\s*\n", "\n", s)
    return s.strip()


def _create_a2ui_part(data: dict) -> Part:
    return Part(root=DataPart(data=data, metadata={"mimeType": A2UI_MIME_TYPE}))


def parse_response_to_parts(content: str) -> List[Part]:
    """Parse LLM response, extracting <a2ui-json> blocks as DataParts."""
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


def _build_query_from_action(ui_event: dict) -> str:
    """Convert an A2UI postMessage action into a natural language query for the agent."""
    action = ui_event.get("action", "")
    data = ui_event.get("data", {})

    if action == "cardMoved":
        issue_key = data.get("issueKey", "")
        issue_title = data.get("issueTitle", "")
        old_state = data.get("oldState", "")
        new_state = data.get("newState", "")
        return (
            f"CARD_MOVED: The user dragged audit issue {issue_key} "
            f'("{issue_title}") from "{old_state}" to "{new_state}". '
            f"Call update_issue_status with issue_key={issue_key}, "
            f"new_status={new_state}. Then provide a brief confirmation and "
            f"AI-generated commentary on what this status change means, "
            f"next steps, and who should be notified."
        )

    elif action == "issueSelected":
        issue_key = data.get("issueKey", "")
        issue_title = data.get("issueTitle", "")
        severity = data.get("severity", "")
        status = data.get("status", "")
        assignee = data.get("assignee", "")
        return (
            f"ISSUE_SELECTED: The user clicked on audit issue {issue_key} "
            f'("{issue_title}"), Severity: {severity}, Status: {status}, '
            f"Assignee: {assignee}. "
            f"Provide a detailed AI-generated analysis including: "
            f"1) Risk assessment and business impact, "
            f"2) Root cause analysis, "
            f"3) Recommended remediation steps with timeline, "
            f"4) Who should be involved in resolution, "
            f"5) KPMG best practice guidance for this type of finding."
        )

    else:
        return f"User performed UI action '{action}' with data: {json.dumps(data)}"


class AuditIssueTrackerExecutor(AgentExecutor):
    """A2A executor for the KPMG Audit Issue Tracker agent with self-healing."""

    def __init__(self):
        self.agent = None
        self.runner = None

    def _init_agent(self):
        if self.agent is not None:
            return
        self.agent = get_agent()
        self.runner = Runner(
            app_name=AGENT_NAME,
            agent=self.agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )
        logger.info("AuditIssueTrackerExecutor initialized runner")

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Execute a request — handles both text input and A2UI button/drag actions."""
        self._init_agent()

        query = ""
        ui_event_part = None

        # Check for A2UI action in message parts (postMessage from iframe)
        if context.message and context.message.parts:
            for part in context.message.parts:
                if isinstance(part.root, DataPart):
                    data = part.root.data
                    # Handle both a2ui_action (from iframe postMessage) and userAction formats
                    if "userAction" in data:
                        ui_event_part = data["userAction"]
                        break
                    elif data.get("type") == "a2ui_action":
                        ui_event_part = data
                        break

        if ui_event_part:
            logger.info(f"Received A2UI action: {ui_event_part}")
            query = _build_query_from_action(ui_event_part)
        else:
            query = context.get_user_input()

        logger.info(f"AuditIssueTrackerExecutor executing: {query[:120]}...")

        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.submit()
        await updater.start_work()

        try:
            # Get or create session
            session = await self.runner.session_service.get_session(
                app_name=self.runner.app_name,
                user_id="user",
                session_id=context.context_id,
            )
            if session is None:
                session = await self.runner.session_service.create_session(
                    app_name=self.runner.app_name,
                    user_id="user",
                    state={},
                    session_id=context.context_id,
                )

            content = types.Content(role="user", parts=[types.Part(text=query)])

            async for event in self.runner.run_async(
                session_id=session.id,
                user_id="user",
                new_message=content,
            ):
                if hasattr(event, "is_final_response") and event.is_final_response():
                    answer_text = ""
                    if event.content and event.content.parts:
                        answer_text = "\n".join(
                            [p.text for p in event.content.parts if p.text]
                        )

                    if answer_text:
                        final_parts = parse_response_to_parts(answer_text)

                        # ── Always append Kanban board after get_audit_issues ──
                        # The LLM never outputs the <a2ui-json> block directly
                        # (it's ~16KB and gets truncated). The executor always
                        # generates it programmatically from the cache.
                        # Only append for non-action responses (don't re-render on card moves).
                        should_append_board = (
                            _LAST_ISSUES_DATA
                            and ui_event_part is None  # don't re-render on action responses
                        )

                        if should_append_board:
                            logger.info("Appending Kanban board from cache...")
                            try:
                                a2ui_block = generate_kanban_a2ui_tool(_LAST_ISSUES_DATA)
                                # Strip the tag wrappers to get raw JSON
                                inner = a2ui_block
                                if inner.startswith(A2UI_OPEN_TAG):
                                    inner = inner[len(A2UI_OPEN_TAG):].strip()
                                if inner.endswith(A2UI_CLOSE_TAG):
                                    inner = inner[:-len(A2UI_CLOSE_TAG)].strip()
                                payload = json.loads(inner)
                                if isinstance(payload, list):
                                    for item in payload:
                                        final_parts.append(_create_a2ui_part(item))
                                else:
                                    final_parts.append(_create_a2ui_part(payload))
                                logger.info("✓ Kanban board appended.")
                            except Exception as heal_err:
                                logger.error(f"Failed to append Kanban board: {heal_err}")

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

        except Exception as e:
            logger.error(f"Error in AuditIssueTrackerExecutor: {e}", exc_info=True)
            await updater.update_status(
                TaskState.failed,
                message=Message(
                    message_id=str(uuid.uuid4()),
                    role=Role.agent,
                    parts=[TextPart(text=f"An error occurred: {str(e)}")],
                ),
            )
            raise

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise ServerError(error=UnsupportedOperationError())
