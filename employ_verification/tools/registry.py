"""
Tool Registry — Central catalog of all available tools.

This registry provides metadata about each tool for management, documentation,
and discoverability. The ADK agent uses Python docstrings for LLM-facing metadata,
but this registry is useful for:
  - Programmatic discovery of tools
  - Filtering tools by tags, operation type, or data source
  - Generating documentation
  - Adding new tools without touching agent code

To add a new tool:
  1. Create a new file in tools/<domain>/ with the tool function
  2. Add an entry here in TOOL_REGISTRY
  3. Import the tool in tools/<domain>/__init__.py
  4. Add the dot-path to the relevant agent's config YAML (config/<agent>.yaml)
"""

TOOL_REGISTRY = {
    # =========================================================================
    # Employee domain tools
    # =========================================================================
    "lookup_employee": {
        "name": "lookup_employee",
        "module": "tools.employee.lookup_employee",
        "function": "lookup_employee",
        "description": "Fetches employee records from BigQuery by name, employee ID, or department.",
        "source": "BigQuery",
        "dataset": "employee_verification",
        "table": "employee_records",
        "operation": "READ",
        "parameters": {
            "name": {"type": "str", "required": False, "description": "Employee name to search (partial match)"},
            "employee_id": {"type": "str", "required": False, "description": "Exact employee ID"},
            "department": {"type": "str", "required": False, "description": "Department name filter"},
        },
        "tags": ["employee", "lookup", "read", "bigquery"],
    },
    "update_employee_field": {
        "name": "update_employee_field",
        "module": "tools.employee.update_employee_field",
        "function": "update_employee_field",
        "description": "Updates an allowed field in an employee's record in BigQuery.",
        "source": "BigQuery",
        "dataset": "employee_verification",
        "table": "employee_records",
        "operation": "WRITE",
        "parameters": {
            "employee_id": {"type": "str", "required": True, "description": "Employee ID to update"},
            "field_name": {"type": "str", "required": True, "description": "Field to update (must be editable)"},
            "new_value": {"type": "str", "required": True, "description": "New value for the field"},
        },
        "editable_fields": ["address", "phone", "email", "emergency_contact", "emergency_phone"],
        "protected_fields": [
            "employee_id", "name", "title", "department", "hire_date",
            "termination_date", "employment_status", "manager", "salary_band",
        ],
        "tags": ["employee", "update", "write", "bigquery"],
    },
    "verify_employee": {
        "name": "verify_employee",
        "module": "tools.employee.verify_employee",
        "function": "verify_employee",
        "description": "Marks an employee's record as verified in BigQuery.",
        "source": "BigQuery",
        "dataset": "employee_verification",
        "table": "employee_records",
        "operation": "WRITE",
        "parameters": {
            "employee_id": {"type": "str", "required": True, "description": "Employee ID to verify"},
        },
        "tags": ["employee", "verify", "write", "bigquery"],
    },
}


# =============================================================================
# Registry query helpers
# =============================================================================

def get_tools_by_tag(tag: str) -> list[dict]:
    """Return tool metadata entries that match a given tag."""
    return [t for t in TOOL_REGISTRY.values() if tag in t.get("tags", [])]


def get_tools_by_operation(operation: str) -> list[dict]:
    """Return tool metadata entries that match a given operation (READ/WRITE)."""
    return [t for t in TOOL_REGISTRY.values() if t.get("operation") == operation]


def get_tools_by_domain(domain: str) -> list[dict]:
    """Return tool metadata entries for a given domain (e.g., 'employee')."""
    return [t for t in TOOL_REGISTRY.values() if domain in t.get("tags", [])]


def get_tool_metadata(tool_name: str) -> dict | None:
    """Return metadata for a specific tool."""
    return TOOL_REGISTRY.get(tool_name)


def list_all_tools() -> list[str]:
    """Return a sorted list of all tool names."""
    return sorted(TOOL_REGISTRY.keys())


def get_tool_dotpath(tool_name: str) -> str | None:
    """Return the full dot-path for a tool (module.function)."""
    meta = TOOL_REGISTRY.get(tool_name)
    if meta:
        return f"{meta['module']}.{meta['function']}"
    return None
