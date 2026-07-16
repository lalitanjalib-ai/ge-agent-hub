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
KPMG Audit Issue Tracker — Agent Tools.

Tools:
  - get_audit_issues: Returns mocked KPMG audit findings for an engagement
                      and generates the Kanban board A2UI block.
  - update_issue_status: Updates an issue's status (in-memory for demo).
"""

import json
import logging

logger = logging.getLogger(__name__)

try:
    from .components import generate_kanban_a2ui_tool, _LAST_ISSUES_DATA
except ImportError:
    from components import generate_kanban_a2ui_tool, _LAST_ISSUES_DATA


# ---------------------------------------------------------------------------
# In-memory issue store (mocked data — no BigQuery needed for this demo)
# ---------------------------------------------------------------------------

_MOCK_ENGAGEMENTS = {
    "ENG-2024-001": {
        "engagement_id": "ENG-2024-001",
        "engagement_name": "Acme Corp Annual Audit FY2024",
        "issues": [
            {
                "key": "KPMG-001",
                "title": "Revenue Recognition Controls — Incomplete documentation for Q3 deferred revenue entries",
                "severity": "High",
                "status": "Open",
                "assignee": "Sarah Chen",
                "date": "2024-11-01",
            },
            {
                "key": "KPMG-002",
                "title": "IT Access Management — Privileged accounts lack quarterly recertification evidence",
                "severity": "Medium",
                "status": "In Review",
                "assignee": "James Park",
                "date": "2024-11-03",
            },
            {
                "key": "KPMG-003",
                "title": "Vendor Payment Approval — Three payments >$50K processed without dual authorization",
                "severity": "High",
                "status": "Open",
                "assignee": "Maria Rodriguez",
                "date": "2024-11-05",
            },
            {
                "key": "KPMG-004",
                "title": "Financial Statement Disclosure — Contingent liability footnote requires updated legal estimate",
                "severity": "Low",
                "status": "Resolved",
                "assignee": "David Kim",
                "date": "2024-10-28",
            },
            {
                "key": "KPMG-005",
                "title": "Segregation of Duties — Same individual initiates and approves journal entries in GL module",
                "severity": "Medium",
                "status": "Open",
                "assignee": "Lisa Wang",
                "date": "2024-11-07",
            },
            {
                "key": "KPMG-006",
                "title": "Third-Party Risk Assessment — Two critical vendors missing SOC 2 Type II reports for FY2024",
                "severity": "High",
                "status": "In Review",
                "assignee": "Tom Bradley",
                "date": "2024-11-08",
            },
        ],
    },
    "ENG-2024-002": {
        "engagement_id": "ENG-2024-002",
        "engagement_name": "GlobalTech Inc. Internal Controls Review",
        "issues": [
            {
                "key": "KPMG-101",
                "title": "Inventory Valuation — FIFO vs LIFO inconsistency detected across three warehouse locations",
                "severity": "High",
                "status": "Open",
                "assignee": "Rachel Moore",
                "date": "2024-11-10",
            },
            {
                "key": "KPMG-102",
                "title": "Cash Reconciliation — Unreconciled items >30 days in bank reconciliation for October",
                "severity": "Medium",
                "status": "In Review",
                "assignee": "Kevin Zhang",
                "date": "2024-11-12",
            },
            {
                "key": "KPMG-103",
                "title": "Payroll Controls — Terminated employee still active in payroll system for 45 days",
                "severity": "High",
                "status": "Resolved",
                "assignee": "Anna Patel",
                "date": "2024-11-02",
            },
        ],
    },
}

# Runtime mutable copy for status updates
_RUNTIME_ISSUES: dict = {}


def _get_runtime_engagement(engagement_id: str) -> dict | None:
    """Get engagement data from runtime store, initializing from mock if needed."""
    if engagement_id not in _RUNTIME_ISSUES:
        mock = _MOCK_ENGAGEMENTS.get(engagement_id)
        if not mock:
            return None
        import copy
        _RUNTIME_ISSUES[engagement_id] = copy.deepcopy(mock)
    return _RUNTIME_ISSUES[engagement_id]


# ---------------------------------------------------------------------------
# Tool: get_audit_issues
# ---------------------------------------------------------------------------

def get_audit_issues(engagement_id: str = "ENG-2024-001") -> str:
    """Retrieve audit issues for a KPMG engagement and render the interactive Kanban board.

    Fetches all audit findings for the specified engagement and generates a
    rich Kanban board widget displayed in the Gemini Enterprise side panel.
    The board shows issues organized by status: Open, In Review, and Resolved.
    Cards can be dragged between columns and clicked for AI-generated remediation advice.

    Args:
        engagement_id: The engagement identifier (e.g., 'ENG-2024-001', 'ENG-2024-002').
                       Defaults to 'ENG-2024-001' if not specified.

    Returns:
        A combined string with the issues summary and the <a2ui-json> Kanban board block.
    """
    logger.info(f"--- TOOL CALLED: get_audit_issues(engagement_id={engagement_id}) ---")

    engagement = _get_runtime_engagement(engagement_id)

    if not engagement:
        # Try a fuzzy match — if user said "ENG-2024" match first available
        available = list(_MOCK_ENGAGEMENTS.keys())
        for key in available:
            if engagement_id.upper() in key.upper() or key.upper() in engagement_id.upper():
                engagement = _get_runtime_engagement(key)
                break

    if not engagement:
        available_ids = ", ".join(_MOCK_ENGAGEMENTS.keys())
        return json.dumps({
            "error": f"Engagement '{engagement_id}' not found.",
            "available_engagements": available_ids,
        })

    issues = engagement["issues"]
    open_count = sum(1 for i in issues if i["status"] == "Open")
    review_count = sum(1 for i in issues if i["status"] == "In Review")
    resolved_count = sum(1 for i in issues if i["status"] == "Resolved")
    high_count = sum(1 for i in issues if i["severity"] == "High")

    summary = {
        "engagement_id": engagement["engagement_id"],
        "engagement_name": engagement["engagement_name"],
        "total_issues": len(issues),
        "open": open_count,
        "in_review": review_count,
        "resolved": resolved_count,
        "high_severity": high_count,
        "issues": issues,
    }

    logger.info(f"  Found {len(issues)} issues for {engagement['engagement_id']}")

    # Generate the Kanban board A2UI block
    a2ui_block = generate_kanban_a2ui_tool(engagement)

    return (
        f"Audit Issues Summary:\n{json.dumps(summary, indent=2)}"
        f"\n\nKanban Board UI Block:\n{a2ui_block}"
    )


# ---------------------------------------------------------------------------
# Tool: update_issue_status
# ---------------------------------------------------------------------------

def update_issue_status(
    issue_key: str,
    new_status: str,
    comment: str = "",
    engagement_id: str = "ENG-2024-001",
) -> str:
    """Update the status of an audit issue in the tracker.

    Call this tool when the user drags a card to a new column (cardMoved action)
    or explicitly asks to change an issue's status. Valid statuses are:
    'Open', 'In Review', 'Resolved'.

    Args:
        issue_key: The issue identifier (e.g., 'KPMG-001').
        new_status: The new status. Must be one of: 'Open', 'In Review', 'Resolved'.
        comment: Optional comment or reason for the status change.
        engagement_id: The engagement the issue belongs to (default: 'ENG-2024-001').

    Returns:
        JSON string confirming the update or describing any error.
    """
    logger.info(
        f"--- TOOL CALLED: update_issue_status("
        f"issue_key={issue_key}, new_status={new_status}) ---"
    )

    valid_statuses = ["Open", "In Review", "Resolved"]
    if new_status not in valid_statuses:
        return json.dumps({
            "error": f"Invalid status '{new_status}'. Must be one of: {valid_statuses}"
        })

    # Search across all engagements if engagement_id not found
    engagement = _get_runtime_engagement(engagement_id)
    if not engagement:
        for eid in _MOCK_ENGAGEMENTS:
            eng = _get_runtime_engagement(eid)
            if any(i["key"] == issue_key for i in eng["issues"]):
                engagement = eng
                break

    if not engagement:
        return json.dumps({"error": f"Could not find engagement for issue '{issue_key}'"})

    issue = next((i for i in engagement["issues"] if i["key"] == issue_key), None)
    if not issue:
        return json.dumps({"error": f"Issue '{issue_key}' not found."})

    old_status = issue["status"]
    issue["status"] = new_status

    result = {
        "success": True,
        "issue_key": issue_key,
        "issue_title": issue["title"],
        "old_status": old_status,
        "new_status": new_status,
        "comment": comment,
        "engagement_id": engagement["engagement_id"],
    }

    logger.info(f"  Updated {issue_key}: {old_status} → {new_status}")
    return json.dumps(result)
