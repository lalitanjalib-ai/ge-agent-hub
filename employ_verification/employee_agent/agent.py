"""
Employee Verification Agent — BYOC A2A, built-in OAuth propagation (v5).

Auth model: this agent is deployed via Vertex AI Agent Engine's
Bring-Your-Own-Dockerfile (BYOC) mode and registered with Gemini Enterprise at
the Agent Engine V2 ingress URL. If the hosting project is on the
OAuth-propagation allowlist, GE attaches the end user's OAuth access token,
Agent Engine's gateway rewrites it onto the standard Authorization header,
and `main.py`'s TokenExtractorMiddleware captures it per-request. Tools read
it via `employee_agent.token_context.get_user_token()` and use it directly
for BigQuery — no STS/WIF exchange, no multi-location scanning.
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")

from google.adk.agents import Agent


try:
    from a2ui.schema.manager import A2uiSchemaManager
    from a2ui.basic_catalog.provider import BasicCatalog
    from a2ui.schema.common_modifiers import remove_strict_validation
    from a2ui.schema.constants import VERSION_0_8

    _A2UI_AVAILABLE = True
except ImportError:  # pragma: no cover - a2ui is an optional dependency
    _A2UI_AVAILABLE = False

from employee_agent.tools.lookup_employee import lookup_employee
from employee_agent.tools.update_employee_field import update_employee_field
from employee_agent.tools.verify_employee import verify_employee

logger = logging.getLogger(__name__)

_ROLE_DESCRIPTION = """
You are an Employee Verification Assistant. Your job is to help employees
review, update, and verify their employment records. You have access to
the company's employee database and can look up records, update allowed
fields, and mark records as verified.

For simple greetings or small talk, respond briefly and ask how you can
help with employee verification. Do not call tools unless the user asks
about employee records.
"""

_WORKFLOW_DESCRIPTION = """
You follow this workflow:

1. EMPLOYEE LOOKUP:
   - When a user asks to verify their employment, look up their info,
     or find an employee, you MUST call `lookup_employee` first.
   - If a single employee is found, display their full verification
     form with editable fields.
   - If multiple employees are found, show a list for the user to
     select from.

2. EMPLOYEE VERIFICATION FORM:
   - After looking up an employee, present an A2UI verification form
     showing read-only and editable fields plus "Submit & Verify" and
     "Verify As Is" buttons.

3. HANDLING FORM SUBMISSIONS:
   - "Submit & Verify": update changed fields, then verify_employee.
   - "Verify As Is": verify_employee without updates.

4. HANDLING SELECT FROM LIST:
   - Call lookup_employee for the selected employee_id and show the form.

5. CONVERSATIONAL UPDATES:
   - Use update_employee_field for editable fields only.

IMPORTANT: Always call `lookup_employee` before showing any employee
data. Never make up employee information.
"""

_UI_DESCRIPTION = """
You MUST render A2UI components for employee data. Follow these rules:

- SINGLE employee: verification form with read-only Text fields, editable
  TextInput fields, and action buttons.
- MULTIPLE results: list card with a Verify button per employee.
- VERIFICATION SUCCESS: success card with checkCircle icon.
- All A2UI JSON MUST be wrapped in `<a2ui-json>` and `</a2ui-json>` tags.
"""

_PLAIN_TEXT_UI_NOTE = """
Present employee information as clear, well-formatted plain text (no A2UI
JSON blocks are available in this deployment). Use simple lists/labels for
fields, and clearly state success/failure after update or verify actions.
"""


def _build_instruction() -> str:
    if _A2UI_AVAILABLE:
        schema_manager = A2uiSchemaManager(
            version=VERSION_0_8,
            catalogs=[
                BasicCatalog.get_config(
                    version=VERSION_0_8,
                    examples_path=None,
                )
            ],
            schema_modifiers=[remove_strict_validation],
        )
        return schema_manager.generate_system_prompt(
            role_description=_ROLE_DESCRIPTION,
            workflow_description=_WORKFLOW_DESCRIPTION,
            ui_description=_UI_DESCRIPTION,
            include_schema=True,
            include_examples=True,
            validate_examples=False,
        )

    logger.warning(
        "a2ui package not available — falling back to plain-text instruction "
        "(interactive A2UI forms will not render)."
    )
    return "\n\n".join([_ROLE_DESCRIPTION, _WORKFLOW_DESCRIPTION, _PLAIN_TEXT_UI_NOTE])


def create_agent() -> Agent:
    """Create and return the Employee Verification agent."""
    model = os.environ.get("GOOGLE_GENAI_MODEL", "gemini-2.5-flash")
    instruction = _build_instruction()

    agent = Agent(
        name="EmployeeVerificationAgent",
        model=model,
        description=(
            "An HR agent that helps employees review, update, and verify "
            "their employment records. BigQuery calls run as the logged-in "
            "Gemini Enterprise user via propagated Google OAuth (BYOC Agent "
            "Engine V2 ingress), when available."
        ),
        instruction=instruction,
        tools=[lookup_employee, update_employee_field, verify_employee],
    )

    logger.info(f"Created agent '{agent.name}' with model={model}")
    return agent


_root_agent = None


def get_agent() -> Agent:
    """Get or create the singleton agent instance."""
    global _root_agent
    if _root_agent is None:
        _root_agent = create_agent()
    return _root_agent


root_agent = get_agent()
