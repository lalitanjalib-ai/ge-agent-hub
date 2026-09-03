"""
Employee Verification Agent — BYOC A2A with Microsoft Entra ID 3P OAuth.

Auth model: this agent is deployed via Vertex AI Agent Engine's
Bring-Your-Own-Dockerfile (BYOC) mode and registered with Gemini Enterprise
at the Agent Engine V2 ingress URL with a Microsoft Entra ID authorization
resource attached. GE forwards the end user's Entra JWT on
`X-Goog-Agent-User-Authorization`; Agent Engine's V2 ingress gateway rewrites
it onto the standard `Authorization` header, and `main.py`'s
TokenExtractorMiddleware captures it per-request. Tools read it via
`employee_agent.token_context.get_user_token()` and exchange it for a Google
federated access token via Workforce Identity Federation / RFC 8693 STS
(see `employee_agent.entra_wif`) before calling BigQuery.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")

logger = logging.getLogger(__name__)

# Few-shot A2UI example payloads (see employee_agent/a2ui_examples/0.8/),
# carried over from the earlier, working employee_verification_v2 agent.
# Without real examples, the model has to infer the component-nesting rules
# (e.g. that Button.child must be a component-ID string, never an inline
# Text object) from the JSON schema alone, and it gets this wrong in
# practice — producing payloads the a2ui validator rejects, which silently
# degrades to a plain-text fallback with no rendered form.
def _resolve_a2ui_examples_path() -> str | None:
    """Return the a2ui examples directory, or None if a2ui's (urlparse-based)
    resolve_examples_path() would misinterpret it.

    a2ui's resolve_examples_path() runs urlparse() on this value and rejects
    anything whose scheme isn't "" or "file". On Linux (this container's
    actual deployment target — see Dockerfile) a plain absolute POSIX path
    parses with an empty scheme and works correctly. On Windows (local dev
    only) a bare "C:\\..." path is misparsed as scheme "c", so we fall back
    to None there instead of crashing agent import at module load time —
    local `uvicorn main:app` testing on Windows just won't get few-shot
    A2UI examples (schema-only prompting still works).
    """
    import urllib.parse

    path = str(Path(__file__).resolve().parent / "a2ui_examples" / "0.8")
    parsed = urllib.parse.urlparse(path)
    if parsed.scheme in ("", "file"):
        return path
    logger.warning(
        "Skipping A2UI examples_path=%r on this platform (urlparse scheme "
        "%r unsupported by a2ui) — falling back to schema-only prompting.",
        path,
        parsed.scheme,
    )
    return None


_A2UI_EXAMPLES_PATH = _resolve_a2ui_examples_path()

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
from employee_agent.token_context import set_user_token

_AUTH_ID = os.environ.get("AUTH_ID", "")

# Shared A2UI schema manager (also used by main.py's A2uiAwareExecutor via
# get_a2ui_parser() to turn <a2ui-json> blocks in the model's final response
# into A2A DataParts). Built once here so the prompt's schema/catalog and the
# response parser always agree on the same version/catalog.
_a2ui_schema_manager = None


def _inject_ge_oauth_token(*, tool, args, tool_context, **kwargs):
    """Copy Entra OAuth token GE stored in session state into token_context.

    When registered via adkAgentDefinition, GE passes the user's OAuth token
    through ADK tool_context.session.state[AUTH_ID], not via the A2A
    Authorization header.
    """
    if not _AUTH_ID or tool_context is None:
        return None
    state = {}
    session = getattr(tool_context, "session", None)
    if session is not None:
        state = getattr(session, "state", None) or {}
    if not state:
        state = getattr(tool_context, "state", None) or {}
    raw = state.get(_AUTH_ID) or state.get(f"temp:{_AUTH_ID}")
    token = None
    if isinstance(raw, str):
        token = raw
    elif isinstance(raw, dict):
        token = raw.get("access_token") or raw.get("token")
    if token:
        set_user_token(token)
        logger.info("OBO: Entra token loaded from GE session state (auth=%s).", _AUTH_ID)
    return None

_ROLE_DESCRIPTION = """
You are an Employee Verification Assistant. Your job is to help employees
review, update, and verify their employment records. You have access to
the company's employee database and can look up records, update allowed
fields, and mark records as verified.

For simple greetings or small talk, respond briefly, then ask how you can
help and offer a few example questions the user could try, such as:
- "Find employee John Smith"
- "Show me all employees"
- "Update my address to 123 Main St"
- "Verify my employment"
Do not call tools unless the user asks about employee records.
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

6. HANDLING UI ACTIONS (button clicks):
   - Messages that start with "[UI action] The user clicked the '<name>'
     control with <key>=<value>, ..." represent a button click in a
     previously rendered A2UI surface, not typed text — treat the action
     name and context values as if the user had typed the equivalent
     request. Known action names from the A2UI examples below and what
     each means:
       - select_employee (context: employeeId, employeeName) -> call
         `lookup_employee` for that employeeId and show their
         verification form.
       - submit_verification (context: employeeId, plus any edited field
         values) -> call `update_employee_field` for each changed field,
         then `verify_employee` for that employeeId.
       - verify_as_is (context: employeeId) -> call `verify_employee`
         for that employeeId directly, with no field updates.
       - dismiss_modal -> acknowledge briefly with plain text; no tool
         call needed.

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


def _get_a2ui_schema_manager():
    """Build (once) and return the shared A2UI schema manager/parser."""
    global _a2ui_schema_manager
    if _a2ui_schema_manager is None:
        _a2ui_schema_manager = A2uiSchemaManager(
            version=VERSION_0_8,
            catalogs=[
                BasicCatalog.get_config(
                    version=VERSION_0_8,
                    examples_path=_A2UI_EXAMPLES_PATH,
                )
            ],
            schema_modifiers=[remove_strict_validation],
        )
    return _a2ui_schema_manager


def get_a2ui_parser():
    """Return the A2UI response parser for main.py's A2uiAwareExecutor to use,
    or None if the a2ui package is not available."""
    if not _A2UI_AVAILABLE:
        return None
    return _get_a2ui_schema_manager().parser


def _build_instruction() -> str:
    if _A2UI_AVAILABLE:
        schema_manager = _get_a2ui_schema_manager()
        return schema_manager.generate_system_prompt(
            role_description=_ROLE_DESCRIPTION,
            workflow_description=_WORKFLOW_DESCRIPTION,
            ui_description=_UI_DESCRIPTION,
            include_schema=True,
            include_examples=True,
            # Fail loudly at container startup if the few-shot example files
            # in a2ui_examples/0.8/ ever drift out of sync with the actual
            # a2ui-agent-sdk schema (e.g. an invalid component/icon name),
            # instead of silently teaching the model bad patterns that only
            # surface later as runtime A2UI validation failures.
            validate_examples=True,
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
            "Gemini Enterprise (Microsoft Entra ID) user via Workforce "
            "Identity Federation, when available."
        ),
        instruction=instruction,
        tools=[lookup_employee, update_employee_field, verify_employee],
        before_tool_callback=_inject_ge_oauth_token,
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
