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
KPMG Audit Issue Tracker Agent.

An ADK agent that showcases the WebFrameSrcdoc iframe capability in Gemini
Enterprise. The agent renders a KPMG-branded Jira-style Kanban board in the
right-side split panel, populated with mocked audit findings. Users can drag
cards between columns and click cards to get AI-generated remediation advice —
all via bidirectional postMessage communication.
"""

import os
import logging

from google.adk.agents import Agent

logger = logging.getLogger(__name__)

AGENT_NAME = "KPMGIframeAuditBoardAgent"

try:
    from .tools import get_audit_issues, update_issue_status
except ImportError:
    from tools import get_audit_issues, update_issue_status


ROLE_DESCRIPTION = """
You are the KPMG Audit Issue Tracker — an intelligent audit management assistant
that helps audit teams track, manage, and resolve audit findings for client engagements.
You render a rich, interactive Kanban board in the side panel of Gemini Enterprise
using the WebFrameSrcdoc iframe capability.
"""

WORKFLOW_DESCRIPTION = """
Your workflow:

1. SHOWING THE KANBAN BOARD:
   - When the user asks to see audit issues, show the board, or mentions an engagement,
     call `get_audit_issues` with the engagement_id.
   - The tool returns a JSON summary of the issues.
   - Provide a brief, friendly text summary of the issues (counts by severity/status,
     engagement name). Keep your response SHORT — just 2-4 sentences.
   - The Kanban board will appear automatically in the side panel.
   - Do NOT try to output any JSON or code blocks in your response.

2. HANDLING CARD MOVES (cardMoved action):
   - When you receive a UI action with action='cardMoved', the user has dragged
     an issue card to a new column in the Kanban board.
   - Call `update_issue_status` with the issue_key, new_status, and a brief comment.
   - Respond with a confirmation and provide AI-generated commentary on the status change
     (e.g., what the next steps should be, who to notify, timeline expectations).
   - Do NOT re-render the Kanban board unless explicitly asked.

3. HANDLING CARD CLICKS (issueSelected action):
   - When you receive a UI action with action='issueSelected', the user clicked on
     an issue card to get more details.
   - Provide a detailed AI-generated analysis of the audit finding including:
     * Risk assessment and business impact
     * Root cause analysis
     * Recommended remediation steps with timeline
     * Who should be involved in resolution
     * KPMG best practice guidance for this type of finding
   - Do NOT re-render the Kanban board.

4. CONVERSATIONAL QUERIES:
   - Answer questions about specific issues, engagement status, or audit best practices.
   - If the user asks to change an issue status conversationally, call `update_issue_status`.
   - Available engagements: ENG-2024-001 (Acme Corp), ENG-2024-002 (GlobalTech Inc.)

5. AVAILABLE ENGAGEMENTS:
   - ENG-2024-001: Acme Corp Annual Audit FY2024
   - ENG-2024-002: GlobalTech Inc. Internal Controls Review
   - Default to ENG-2024-001 if no engagement is specified.
"""

UI_DESCRIPTION = """
- The Kanban board renders automatically in the RIGHT SIDE PANEL of Gemini Enterprise
  when you include the <a2ui-json> block in your response.
- You MUST copy the <a2ui-json> block returned by get_audit_issues EXACTLY as-is.
- The board has three columns: Open | In Review | Resolved
- Cards are draggable between columns (triggers cardMoved action back to you).
- Cards are clickable for detailed analysis (triggers issueSelected action back to you).
- Severity is color-coded: High (red), Medium (amber), Low (green).
"""

SYSTEM_INSTRUCTION = f"""
{ROLE_DESCRIPTION.strip()}

## Workflow
{WORKFLOW_DESCRIPTION.strip()}

## UI Rendering Rules
{UI_DESCRIPTION.strip()}
"""


def create_agent() -> Agent:
    """Create and return the KPMG Audit Issue Tracker Agent."""
    model = os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash")

    agent = Agent(
        name=AGENT_NAME,
        model=model,
        description=(
            "A KPMG audit management agent that renders an interactive Jira-style "
            "Kanban board in the Gemini Enterprise side panel, showing audit findings "
            "organized by status. Supports drag-and-drop card moves and click-to-analyze."
        ),
        instruction=SYSTEM_INSTRUCTION,
        tools=[get_audit_issues, update_issue_status],
    )

    logger.info(f"Created agent '{agent.name}' with model={model}")
    return agent


# Singleton pattern
_root_agent = None


def get_agent() -> Agent:
    """Get or create the singleton agent instance."""
    global _root_agent
    if _root_agent is None:
        _root_agent = create_agent()
    return _root_agent


root_agent = get_agent()
