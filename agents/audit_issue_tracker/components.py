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
KPMG Audit Issue Tracker — A2UI WebFrameSrcdoc Component Builder.

Contains the inlined Kanban board HTML widget and the A2UI payload builder.
The HTML is injected with issue data via window.INJECTED_DATA and also
listens for postMessage INJECT_DATA events for dynamic updates.
"""

import json
import logging

logger = logging.getLogger(__name__)

# Global cache for self-healing in the executor
_LAST_ISSUES_DATA = {}

SURFACE_ID = "kpmg-audit-issue-tracker"


class KanbanUIBuilder:
    """Builds the KPMG-branded Kanban board A2UI payload."""

    KANBAN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy" content="connect-src 'none'">
    <title>KPMG Audit Issue Tracker</title>
    <style>
        :root {
            --kpmg-blue: #00338D;
            --kpmg-blue-light: #0047BB;
            --kpmg-blue-dark: #001F5B;
            --bg: #0f172a;
            --card-bg: #1e293b;
            --col-bg: #162032;
            --border: #1e3a5f;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --high: #ef4444;
            --high-bg: rgba(239,68,68,0.12);
            --medium: #f59e0b;
            --medium-bg: rgba(245,158,11,0.12);
            --low: #10b981;
            --low-bg: rgba(16,185,129,0.12);
            --open-accent: #3b82f6;
            --review-accent: #a855f7;
            --resolved-accent: #10b981;
            --shadow: 0 4px 12px rgba(0,0,0,0.4);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text-primary);
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
            font-size: 13px;
        }

        /* ── Header ── */
        .header {
            background: linear-gradient(135deg, var(--kpmg-blue-dark) 0%, var(--kpmg-blue) 60%, var(--kpmg-blue-light) 100%);
            padding: 10px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid var(--kpmg-blue-light);
            flex-shrink: 0;
        }
        .header-left { display: flex; align-items: center; gap: 10px; }
        .kpmg-logo {
            font-size: 1.1rem;
            font-weight: 900;
            letter-spacing: 0.08em;
            color: #fff;
            background: rgba(255,255,255,0.15);
            padding: 3px 8px;
            border-radius: 4px;
        }
        .header-title { font-size: 0.9rem; font-weight: 700; color: #fff; }
        .header-subtitle { font-size: 0.7rem; color: rgba(255,255,255,0.7); margin-top: 1px; }
        .engagement-badge {
            background: rgba(255,255,255,0.15);
            border: 1px solid rgba(255,255,255,0.3);
            color: #fff;
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 0.72rem;
            font-weight: 600;
        }

        /* ── KPI Bar ── */
        .kpi-bar {
            display: flex;
            gap: 8px;
            padding: 8px 16px;
            background: var(--card-bg);
            border-bottom: 1px solid var(--border);
            flex-shrink: 0;
        }
        .kpi {
            flex: 1;
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 6px 10px;
            text-align: center;
        }
        .kpi-val { font-size: 1.2rem; font-weight: 800; }
        .kpi-label { font-size: 0.62rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-top: 1px; }
        .kpi.total .kpi-val { color: var(--open-accent); }
        .kpi.high .kpi-val { color: var(--high); }
        .kpi.review .kpi-val { color: var(--review-accent); }
        .kpi.resolved .kpi-val { color: var(--resolved-accent); }

        /* ── Board ── */
        .board {
            display: flex;
            gap: 10px;
            padding: 10px 16px;
            flex: 1;
            overflow: hidden;
        }

        .column {
            flex: 1;
            background: var(--col-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .col-header {
            padding: 8px 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid var(--border);
            flex-shrink: 0;
        }
        .col-title {
            display: flex;
            align-items: center;
            gap: 6px;
            font-weight: 700;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .col-dot {
            width: 8px; height: 8px;
            border-radius: 50%;
        }
        .col-count {
            background: rgba(255,255,255,0.08);
            color: var(--text-secondary);
            font-size: 0.68rem;
            font-weight: 700;
            padding: 1px 7px;
            border-radius: 10px;
        }

        .col-open .col-dot { background: var(--open-accent); }
        .col-open .col-title { color: var(--open-accent); }
        .col-review .col-dot { background: var(--review-accent); }
        .col-review .col-title { color: var(--review-accent); }
        .col-resolved .col-dot { background: var(--resolved-accent); }
        .col-resolved .col-title { color: var(--resolved-accent); }

        .col-body {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .col-body::-webkit-scrollbar { width: 4px; }
        .col-body::-webkit-scrollbar-track { background: transparent; }
        .col-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

        /* ── Cards ── */
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px;
            cursor: grab;
            transition: transform 0.15s, box-shadow 0.15s, border-color 0.15s;
            user-select: none;
        }
        .card:hover {
            transform: translateY(-2px);
            box-shadow: var(--shadow);
            border-color: var(--kpmg-blue-light);
        }
        .card.dragging {
            opacity: 0.5;
            cursor: grabbing;
        }
        .card-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 6px;
        }
        .issue-key {
            font-size: 0.68rem;
            font-weight: 700;
            color: var(--kpmg-blue-light);
            background: rgba(0,71,187,0.15);
            padding: 2px 6px;
            border-radius: 4px;
            letter-spacing: 0.03em;
        }
        .severity-badge {
            font-size: 0.62rem;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 10px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .sev-high { background: var(--high-bg); color: var(--high); }
        .sev-medium { background: var(--medium-bg); color: var(--medium); }
        .sev-low { background: var(--low-bg); color: var(--low); }

        .card-title {
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--text-primary);
            line-height: 1.3;
            margin-bottom: 8px;
        }
        .card-meta {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .assignee {
            display: flex;
            align-items: center;
            gap: 5px;
            font-size: 0.68rem;
            color: var(--text-secondary);
        }
        .avatar {
            width: 20px; height: 20px;
            border-radius: 50%;
            background: linear-gradient(135deg, var(--kpmg-blue), var(--kpmg-blue-light));
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.6rem;
            font-weight: 700;
            color: #fff;
            flex-shrink: 0;
        }
        .card-date {
            font-size: 0.62rem;
            color: var(--text-muted);
        }

        /* Drop zone highlight */
        .col-body.drag-over {
            background: rgba(0,71,187,0.08);
            border-radius: 6px;
        }

        /* ── Toast notification ── */
        .toast {
            position: fixed;
            bottom: 16px;
            right: 16px;
            background: var(--kpmg-blue);
            color: #fff;
            padding: 10px 16px;
            border-radius: 8px;
            font-size: 0.78rem;
            font-weight: 600;
            box-shadow: var(--shadow);
            opacity: 0;
            transform: translateY(10px);
            transition: opacity 0.3s, transform 0.3s;
            pointer-events: none;
            z-index: 100;
            max-width: 280px;
        }
        .toast.show {
            opacity: 1;
            transform: translateY(0);
        }

        /* ── Empty state ── */
        .empty-state {
            text-align: center;
            color: var(--text-muted);
            font-size: 0.72rem;
            padding: 20px 10px;
        }
    </style>
</head>
<body>

<div class="header">
    <div class="header-left">
        <div class="kpmg-logo">KPMG</div>
        <div>
            <div class="header-title">Audit Issue Tracker</div>
            <div class="header-subtitle" id="engagement-subtitle">Loading engagement...</div>
        </div>
    </div>
    <div class="engagement-badge" id="engagement-badge">—</div>
</div>

<div class="kpi-bar">
    <div class="kpi total">
        <div class="kpi-val" id="kpi-total">0</div>
        <div class="kpi-label">Total Issues</div>
    </div>
    <div class="kpi high">
        <div class="kpi-val" id="kpi-high">0</div>
        <div class="kpi-label">High Severity</div>
    </div>
    <div class="kpi review">
        <div class="kpi-val" id="kpi-review">0</div>
        <div class="kpi-label">In Review</div>
    </div>
    <div class="kpi resolved">
        <div class="kpi-val" id="kpi-resolved">0</div>
        <div class="kpi-label">Resolved</div>
    </div>
</div>

<div class="board">
    <div class="column col-open" id="col-Open">
        <div class="col-header">
            <div class="col-title"><span class="col-dot"></span>Open</div>
            <span class="col-count" id="count-Open">0</span>
        </div>
        <div class="col-body" id="body-Open"
             ondragover="onDragOver(event)"
             ondragleave="onDragLeave(event)"
             ondrop="onDrop(event, 'Open')">
            <div class="empty-state">No open issues</div>
        </div>
    </div>

    <div class="column col-review" id="col-In Review">
        <div class="col-header">
            <div class="col-title"><span class="col-dot"></span>In Review</div>
            <span class="col-count" id="count-In Review">0</span>
        </div>
        <div class="col-body" id="body-In Review"
             ondragover="onDragOver(event)"
             ondragleave="onDragLeave(event)"
             ondrop="onDrop(event, 'In Review')">
            <div class="empty-state">No issues in review</div>
        </div>
    </div>

    <div class="column col-resolved" id="col-Resolved">
        <div class="col-header">
            <div class="col-title"><span class="col-dot"></span>Resolved</div>
            <span class="col-count" id="count-Resolved">0</span>
        </div>
        <div class="col-body" id="body-Resolved"
             ondragover="onDragOver(event)"
             ondragleave="onDragLeave(event)"
             ondrop="onDrop(event, 'Resolved')">
            <div class="empty-state">No resolved issues</div>
        </div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
    var issues = [];
    var draggedCard = null;
    var draggedIssueKey = null;
    var draggedOldState = null;

    // ── Trusted Types policy (required by GE sandbox) ──
    var policy = null;
    if (window.trustedTypes && window.trustedTypes.createPolicy) {
        try {
            policy = window.trustedTypes.createPolicy('kpmg-kanban', {
                createHTML: function(s) { return s; }
            });
        } catch(e) {
            try {
                policy = window.trustedTypes.createPolicy('default', {
                    createHTML: function(s) { return s; }
                });
            } catch(e2) {}
        }
    }

    function setHTML(el, html) {
        if (policy) {
            try { el.innerHTML = policy.createHTML(html); return; } catch(e) {}
        }
        var parser = new DOMParser();
        var doc = parser.parseFromString(html, 'text/html');
        while (el.firstChild) el.removeChild(el.firstChild);
        while (doc.body.firstChild) el.appendChild(doc.body.firstChild);
    }

    // ── Listen for data injection from parent ──
    window.addEventListener('message', function(event) {
        if (event.data && event.data.type === 'INJECT_DATA') {
            init(event.data.payload || {});
        }
    });

    function init(data) {
        issues = data.issues || [];
        var engId = data.engagement_id || 'ENG-2024';
        var engName = data.engagement_name || 'Audit Engagement';

        document.getElementById('engagement-subtitle').textContent = engName;
        document.getElementById('engagement-badge').textContent = engId;

        renderBoard();
        updateKPIs();
    }

    function renderBoard() {
        var columns = ['Open', 'In Review', 'Resolved'];
        columns.forEach(function(col) {
            var body = document.getElementById('body-' + col);
            var colIssues = issues.filter(function(i) { return i.status === col; });
            document.getElementById('count-' + col).textContent = colIssues.length;

            if (colIssues.length === 0) {
                var emptyMsgs = {
                    'Open': 'No open issues',
                    'In Review': 'No issues in review',
                    'Resolved': 'No resolved issues'
                };
                setHTML(body, '<div class="empty-state">' + emptyMsgs[col] + '</div>');
                return;
            }

            while (body.firstChild) body.removeChild(body.firstChild);
            colIssues.forEach(function(issue) {
                body.appendChild(createCard(issue));
            });
        });
    }

    function createCard(issue) {
        var card = document.createElement('div');
        card.className = 'card';
        card.draggable = true;
        card.dataset.issueKey = issue.key;
        card.dataset.status = issue.status;

        var initials = issue.assignee.split(' ').map(function(n) { return n[0]; }).join('').substring(0, 2);
        var sevClass = 'sev-' + issue.severity.toLowerCase();

        setHTML(card, [
            '<div class="card-top">',
            '  <span class="issue-key">' + issue.key + '</span>',
            '  <span class="severity-badge ' + sevClass + '">' + issue.severity + '</span>',
            '</div>',
            '<div class="card-title">' + issue.title + '</div>',
            '<div class="card-meta">',
            '  <div class="assignee">',
            '    <div class="avatar">' + initials + '</div>',
            '    <span>' + issue.assignee + '</span>',
            '  </div>',
            '  <span class="card-date">' + issue.date + '</span>',
            '</div>'
        ].join(''));

        card.addEventListener('dragstart', function(e) {
            draggedCard = card;
            draggedIssueKey = issue.key;
            draggedOldState = issue.status;
            card.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
        });

        card.addEventListener('dragend', function() {
            card.classList.remove('dragging');
            draggedCard = null;
        });

        card.addEventListener('click', function() {
            emitIssueSelected(issue);
        });

        return card;
    }

    function onDragOver(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        e.currentTarget.classList.add('drag-over');
    }

    function onDragLeave(e) {
        e.currentTarget.classList.remove('drag-over');
    }

    function onDrop(e, newStatus) {
        e.preventDefault();
        e.currentTarget.classList.remove('drag-over');

        if (!draggedIssueKey || draggedOldState === newStatus) return;

        // Update local state
        var issue = issues.find(function(i) { return i.key === draggedIssueKey; });
        if (!issue) return;

        var oldStatus = issue.status;
        issue.status = newStatus;

        renderBoard();
        updateKPIs();
        showToast('Moved ' + draggedIssueKey + ' → ' + newStatus);

        // Emit postMessage to agent
        window.parent.postMessage({
            type: 'a2ui_action',
            action: 'cardMoved',
            data: {
                issueKey: draggedIssueKey,
                issueTitle: issue.title,
                oldState: oldStatus,
                newState: newStatus,
                widgetId: 'kpmg-audit-issue-tracker'
            }
        }, '*');
    }

    function emitIssueSelected(issue) {
        showToast('Loading details for ' + issue.key + '...');
        window.parent.postMessage({
            type: 'a2ui_action',
            action: 'issueSelected',
            data: {
                issueKey: issue.key,
                issueTitle: issue.title,
                severity: issue.severity,
                status: issue.status,
                assignee: issue.assignee,
                widgetId: 'kpmg-audit-issue-tracker'
            }
        }, '*');
    }

    function updateKPIs() {
        document.getElementById('kpi-total').textContent = issues.length;
        document.getElementById('kpi-high').textContent = issues.filter(function(i) { return i.severity === 'High'; }).length;
        document.getElementById('kpi-review').textContent = issues.filter(function(i) { return i.status === 'In Review'; }).length;
        document.getElementById('kpi-resolved').textContent = issues.filter(function(i) { return i.status === 'Resolved'; }).length;
    }

    function showToast(msg) {
        var toast = document.getElementById('toast');
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(function() { toast.classList.remove('show'); }, 3000);
    }

    // ── Bootstrap from injected data ──
    window.addEventListener('load', function() {
        var injected = window.INJECTED_DATA || {};
        if (injected.issues && injected.issues.length > 0) {
            init(injected);
        }
        // Signal ready to parent
        if (window.parent) {
            window.parent.postMessage({ type: 'IFRAME_READY' }, '*');
        }
    });
</script>
</body>
</html>"""

    @staticmethod
    def build_kanban_board(surface_id: str, issues_data: dict) -> dict:
        """
        Build the A2UI v0.8 WebFrameSrcdoc payload for the KPMG Kanban board.
        Injects issue data as window.INJECTED_DATA into the HTML.
        """
        html = KanbanUIBuilder.KANBAN_HTML

        # Inject data as a script tag before </head>
        injected_script = (
            "<script>window.INJECTED_DATA = "
            + json.dumps(issues_data)
            + ";</script>"
        )
        html_injected = html.replace("</head>", injected_script + "\n</head>")

        begin_rendering = {
            "beginRendering": {
                "surfaceId": surface_id,
                "root": "root",
            }
        }

        surface_update = {
            "surfaceUpdate": {
                "surfaceId": surface_id,
                "components": [
                    {
                        "id": "root",
                        "component": {
                            "WebFrameSrcdoc": {
                                "htmlContent": {
                                    "literalString": html_injected
                                }
                            }
                        },
                    }
                ],
            }
        }

        data_model_update = {
            "dataModelUpdate": {
                "surfaceId": surface_id,
                "contents": [],
            }
        }

        return {
            "begin_rendering": begin_rendering,
            "surface_update": surface_update,
            "data_model_update": data_model_update,
        }


def generate_kanban_a2ui_tool(issues_data: dict) -> str:
    """
    Generate the full <a2ui-json> block for the KPMG Audit Issue Tracker Kanban board.

    Args:
        issues_data: Dict with keys: engagement_id, engagement_name, issues (list).

    Returns:
        A string containing the <a2ui-json>...</a2ui-json> block.
    """
    global _LAST_ISSUES_DATA
    _LAST_ISSUES_DATA.clear()
    _LAST_ISSUES_DATA.update(issues_data)

    ui = KanbanUIBuilder.build_kanban_board(
        surface_id=SURFACE_ID,
        issues_data=issues_data,
    )

    # A2UI sequence: surfaceUpdate → dataModelUpdate → beginRendering
    sequence = [
        ui["surface_update"],
        ui["data_model_update"],
        ui["begin_rendering"],
    ]

    return f"<a2ui-json>\n{json.dumps(sequence, indent=2)}\n</a2ui-json>"
