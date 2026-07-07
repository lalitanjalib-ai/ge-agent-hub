# KPMG A2UI Widgets Library

Reusable, KPMG-branded A2UI widget patterns for agents deployed to **Gemini Enterprise** and local **`adk web`** development.

All widgets compose **standard A2UI v0.8 catalog components** (`Card`, `Column`, `Row`, `Text`, `Button`, `Icon`, `Divider`, `Modal`, `List`, `Slider`, etc.) so they render natively without a custom client renderer.

## Directory layout

```
widgets/
├── README.md                          # This file
├── catalog/
│   └── kpmg_catalog_definition.json   # Semantic widget schemas + theme tokens
├── examples/0.8/                      # Few-shot example payloads (one per widget)
├── python/
│   ├── theme.py                       # Brand color tokens
│   ├── builders.py                    # Programmatic payload builders
│   ├── a2ui_utils.py                  # adk web rendering callback
│   └── provider.py                    # KpmgWidgetsCatalog for A2uiSchemaManager
└── client/
    └── README.md                      # Optional custom frontend registration
```

## KPMG theme

Every surface should set `beginRendering.styles` from the shared theme:

| Token | Value | Usage |
|-------|-------|-------|
| `primaryColor` | `#00338D` | Buttons, accents, headings |
| `font` | `Roboto` | All text |
| `secondaryColor` | `#0091DA` | Optional highlights |
| `successColor` | `#00A3A1` | Healthy / success states |
| `warningColor` | `#FF6A00` | Warning states |
| `errorColor` | `#C8102E` | Error states |

```python
from widgets.python.theme import KPMG_SURFACE_STYLES
# {"primaryColor": "#00338D", "font": "Roboto"}
```

Full tokens: `python/theme.py` and `catalog/kpmg_catalog_definition.json`.

---

## Widget reference

Each widget is a **semantic pattern** documented in the catalog, illustrated by an example JSON file, and available as a Python builder. Gemini Enterprise renders the underlying standard components — there is no separate native `Kpmg*` renderer.

### Quick index

| Widget | Example | Builder | Typical use |
|--------|---------|---------|-------------|
| [KpmgBrandedHeader](#kpmgbrandedheader) | `kpmg_branded_header.json` | `branded_header()` | Page / section header |
| [KpmgMetricCard](#kpmgmetriccard) | `kpmg_metric_card.json` | `metric_card()` | KPI summary row |
| [KpmgStatusPanel](#kpmgstatuspanel) | `kpmg_status_panel.json` | `status_panel()` | Inline status message |
| [KpmgDataFieldRow](#kpmgdatafieldrow) | `kpmg_data_field_row.json` | `data_field_row()` | Read-only label + value |
| [KpmgActionBar](#kpmgactionbar) | `kpmg_action_bar.json` | `action_bar()` | Primary / secondary actions |
| [KpmgConfirmationModal](#kpmgconfirmationmodal) | `kpmg_confirmation_modal.json` | `confirmation_modal()` | Confirm / dismiss dialog |
| [KpmgResourceDashboard](#kpmgresourcedashboard) | `kpmg_resource_dashboard.json` | `resource_dashboard()` | Cloud resource overview |
| [Resource detail](#resource-detail-composite) | *(built at runtime)* | `resource_detail()` | Single-resource drill-down |

---

### KpmgBrandedHeader

Centered page header inside a `Card` with icon, title, and optional subtitle.

**When to use:** Top of a surface — employee forms, dashboards, verification flows.

**Composition:** `Card` → `Column` (center) → `Icon` + `Text` (h2 title) + `Text` (h4 subtitle)

| Property | Type | Description |
|----------|------|-------------|
| `title` | literal or path | Main heading |
| `subtitle` | literal or path | Secondary line (optional) |
| `icon` | string | Material icon name (default `verifiedUser`) |

**Example:** `examples/0.8/kpmg_branded_header.json`

```python
from widgets.python.builders import branded_header

components = branded_header(
    "header",
    title="Employee Verification",
    subtitle_path="/subtitle",
    icon="verifiedUser",
)
```

**Data model:** Bind subtitle via path, e.g. `{"key": "subtitle", "valueString": "Review your record"}`.

---

### KpmgMetricCard

KPI metric displayed in a `Card` with caption label, large value, and optional trend line.

**When to use:** Summary counts — verified / pending / total employees, healthy / warning / error resources.

**Composition:** `Row` of `Card` → `Column` (center) → caption `Text` + h2 value `Text` + optional trend caption

| Property | Type | Description |
|----------|------|-------------|
| `label` | literal or path | Metric name (e.g. "Healthy") |
| `value` | literal or path | Metric value (e.g. "128") |
| `trend` | literal or path | Optional delta caption (e.g. "+12% this month") |

**Example:** `examples/0.8/kpmg_metric_card.json` — three cards in a `Row` with `distribution: spaceEvenly`.

```python
from widgets.python.builders import metric_card

components = []
components += metric_card("metric_healthy", "/metrics/healthy/label", "/metrics/healthy/value")
```

**Data model:** Nested `valueMap` under `/metrics/{key}/label` and `/metrics/{key}/value`.

---

### KpmgStatusPanel

Status summary block with section title, icon + status text row, and detail caption.

**When to use:** Verification result, operation outcome, resource health summary.

**Composition:** `Card` → `Column` → title `Text` (h5) + `Row` (`Icon` + status `Text`) + detail `Text` (caption)

| Property | Type | Description |
|----------|------|-------------|
| `status` | literal or path | Main status line |
| `icon` | string | Material icon (example uses `checkCircle`) |

**Example:** `examples/0.8/kpmg_status_panel.json`

```python
from widgets.python.builders import status_panel

components = status_panel("status", status_path="/status")
```

**Data model:** `/status`, `/status_detail` (detail caption in example).

---

### KpmgDataFieldRow

Read-only field row: leading `Icon`, caption label, and bound value.

**When to use:** Display employee fields, resource attributes, any label + value pair.

**Composition:** `Row` → `Icon` + `Column` → label `Text` (caption) + value `Text` (h5, path-bound)

| Property | Type | Description |
|----------|------|-------------|
| `label` | string | Field caption |
| `value` | literal or path | Display value |
| `icon` | string | Material icon (e.g. `badge`, `person`, `category`) |

**Example:** `examples/0.8/kpmg_data_field_row.json` — stacked rows for Employee ID, Name, Department.

```python
from widgets.python.builders import data_field_row

components = data_field_row("field_empid", "Employee ID", "/employee_id", icon="badge")
```

**Data model:** Flat keys at surface root, e.g. `/employee_id`, `/name`, `/department`.

---

### KpmgActionBar

Horizontal row of primary and optional secondary `Button` components.

**When to use:** Form submit actions, workflow decisions (Submit / Cancel, Verify / Skip).

**Composition:** `Row` (`spaceEvenly`) → primary `Button` + optional secondary `Button`

| Property | Type | Description |
|----------|------|-------------|
| `primaryLabel` | string | Primary button text |
| `primaryAction` | string | Action name sent on click |
| `secondaryLabel` | string | Secondary button text (optional) |
| `secondaryAction` | string | Secondary action name (optional) |

**Example:** `examples/0.8/kpmg_action_bar.json`

```python
from widgets.python.builders import action_bar

components = action_bar(
    "actions",
    primary_label="Submit & Verify",
    primary_action="submit_verification",
    secondary_label="Verify As Is",
    secondary_action="verify_as_is",
)
```

**Actions:** Wire `action.name` to your agent's action handler. In v0.8, include `context` with data-model paths when the action needs field values.

---

### KpmgConfirmationModal

Modal dialog with title, message, and dismiss button.

**When to use:** Confirm destructive actions, acknowledge alerts, interrupt workflow for user decision.

**Composition:** `Modal` → `Column` (center) → title `Text` (h2) + message `Text` + dismiss `Button`

| Property | Type | Description |
|----------|------|-------------|
| `title` | literal or path | Modal heading |
| `message` | literal or path | Body text |
| `dismissAction` | string | Action name for dismiss button |

**Example:** `examples/0.8/kpmg_confirmation_modal.json`

```python
from widgets.python.builders import confirmation_modal

components = confirmation_modal(surface_id="confirm", dismiss_action="dismiss")
```

---

### KpmgResourceDashboard

Full cloud-infrastructure dashboard composing multiple KPMG widgets. Used by the [`poc/`](../poc/) sample agent.

**When to use:** Resource overview after a `get_resources` tool call — project status, health summary, drill-down list.

**Composition:**

1. **KpmgBrandedHeader** — cloud icon, "Cloud Resource Dashboard", summary subtitle
2. **KpmgMetricCard row** — Healthy / Warning / Error counts
3. **List** of resource cards, each containing:
   - Header row: resource name + **KPMG** brand label
   - **KpmgStatusPanel**-style status row (icon + status label)
   - **KpmgDataFieldRow** rows for Service Type and Region
   - Issue caption
   - **Slider** for `usage_percent` (storage metrics)
   - **View Details** button → `view_resource_details` action with context

**Example:** `examples/0.8/kpmg_resource_dashboard.json`

```python
from widgets.python.builders import resource_dashboard

payload = resource_dashboard(resources=[
    {"name": "auth-service", "type": "Cloud Run", "region": "us-west1", "status": "healthy"},
    {"name": "events-db", "type": "Cloud SQL", "region": "us-east1", "status": "warning",
     "issue": "Storage usage at 92%", "usage_percent": 92},
])
```

**Data model notes:**

- List items **must** use `valueMap` (keyed entries), not `valueList`
- Each resource entry includes: `name`, `type`, `region`, `status_icon`, `status_label`, `issue`, `usage_percent`
- Status icons: `check` (healthy), `warning`, `info` (critical/error)

**Button action:**

```json
{
  "name": "view_resource_details",
  "context": [
    { "key": "name", "value": { "path": "/name" } },
    { "key": "type", "value": { "path": "/type" } },
    { "key": "region", "value": { "path": "/region" } },
    { "key": "status", "value": { "path": "/status_label" } }
  ]
}
```

---

### Resource detail (composite)

Single-resource drill-down view built from **KpmgDataFieldRow** + status header. Not a separate catalog entry; generated by `resource_detail()` when a user clicks **View Details**.

**When to use:** After `view_resource_details` userAction or `get_resource_details(name)` tool call.

**Composition:** Title row (name + KPMG label) + status row + data field rows for all resource attributes + optional storage `Slider`.

```python
from widgets.python.builders import resource_detail

payload = resource_detail({
    "name": "events-db",
    "type": "Cloud SQL",
    "region": "us-east1",
    "status": "warning",
    "issue": "Storage usage at 92%",
    "usage_percent": 92,
    "storage": "500 GB SSD",
})
```

Implemented in the POC via `poc/dashboard_callback.py` (`before_model_callback`).

---

## Agent integration

### Register with A2uiSchemaManager

```python
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import VERSION_0_8
from widgets.python.provider import KpmgWidgetsCatalog

schema_manager = A2uiSchemaManager(
    version=VERSION_0_8,
    catalogs=KpmgWidgetsCatalog.get_catalogs_for_agent(
        agent_examples_path="path/to/agent/examples",  # optional
    ),
    schema_modifiers=[remove_strict_validation],
)

instruction = schema_manager.generate_system_prompt(
    role_description="...",
    workflow_description="...",
    ui_description=(
        "Use KPMG-branded surfaces with primaryColor #00338D and font Roboto. "
        "Prefer KpmgBrandedHeader for headers, KpmgMetricCard for KPIs, "
        "KpmgDataFieldRow for read-only fields, KpmgActionBar for actions, "
        "KpmgStatusPanel for status, and KpmgResourceDashboard for resource lists."
    ),
    include_schema=True,
    include_examples=True,
)
```

`KpmgWidgetsCatalog` merges all `examples/0.8/*.json` files into the agent's few-shot examples.

### Local development (`adk web`)

See the [`poc/`](../poc/) sample agent:

```bash
cd poc
uv sync
python run_local.py
```

Open http://127.0.0.1:8080/dev-ui/?app=poc

| Module | Role |
|--------|------|
| `widgets/python/a2ui_utils.py` | `a2ui_callback` — parse LLM A2UI JSON for rendering |
| `widgets/python/builders.py` | Programmatic dashboard / detail payloads |
| `poc/dashboard_callback.py` | Injects KPMG UI after tool calls and button clicks |

### Deploy to Gemini Enterprise

- Extension URI: `https://a2ui.org/a2a-extension/a2ui/v0.8`
- Standard catalog: `https://a2ui.org/specification/v0_8/standard_catalog_definition.json`

No custom client is required — widgets are standard component trees.

---

## Programmatic builders

All builders return A2UI protocol messages (`beginRendering`, `surfaceUpdate`, `dataModelUpdate`). Use `surface_messages()` to assemble custom surfaces:

```python
import json
from widgets.python.builders import (
    branded_header,
    data_field_row,
    action_bar,
    resource_dashboard,
    surface_messages,
)

components = []
components += branded_header("header", "Employee Verification", subtitle_path="/name")
components += data_field_row("field_empid", "Employee ID", "/employee_id", icon="badge")
components += action_bar("actions", "Submit", "submit", "Cancel", "cancel")

payload = surface_messages(
    surface_id="employee-form",
    root="main_card",
    components=components,
    data_contents=[{"key": "employee_id", "valueString": "E-1001"}],
)

print(f"<a2ui-json>{json.dumps(payload)}</a2ui-json>")
```

---

## References

| Resource | URL |
|----------|-----|
| ADK + A2UI Codelab | [codelabs.developers.google.com/next26/adk-a2ui](https://codelabs.developers.google.com/next26/adk-a2ui) |
| Gemini Enterprise + A2UI | [Google Cloud Blog](https://cloud.google.com/blog/topics/developers-practitioners/guide-to-gemini-enterprise-and-a2ui-integration) |
| A2UI Composer | [a2ui-composer.ag-ui.com](https://a2ui-composer.ag-ui.com/) |
| A2UI component gallery | [a2ui.org/reference/components](https://a2ui.org/reference/components/) |
| Custom frontend (optional) | [client/README.md](client/README.md) |

## Prototyping

Use [A2UI Composer](https://a2ui-composer.ag-ui.com/) to design surfaces visually, then export JSON into `examples/0.8/`. Apply KPMG theme values from `python/theme.py` in `beginRendering.styles`.
