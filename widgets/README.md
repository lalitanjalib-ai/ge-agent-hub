# KPMG A2UI Widgets Library

Reusable, KPMG-branded A2UI widget patterns for agents deployed to **Gemini Enterprise**.

All widgets compose **standard A2UI v0.8 catalog components** (`Card`, `Column`, `Row`, `Text`, `Button`, `Icon`, `Divider`, `Modal`, etc.) so they render natively in Gemini Enterprise without a custom client renderer.

## References

| Resource | URL |
|----------|-----|
| Gemini Enterprise + A2UI integration | [Google Cloud Blog](https://cloud.google.com/blog/topics/developers-practitioners/guide-to-gemini-enterprise-and-a2ui-integration) |
| A2UI Composer (visual prototyping) | [a2ui-composer.ag-ui.com](https://a2ui-composer.ag-ui.com/) |
| Authoring custom components | [a2ui.org/guides/authoring-components](https://a2ui.org/guides/authoring-components/#2-implementing-the-component-client) |

## Directory layout

```
widgets/
├── README.md
├── catalog/
│   └── kpmg_catalog_definition.json   # Widget schema + KPMG theme tokens
├── examples/0.8/                      # Example payloads for agent few-shot learning
│   ├── kpmg_branded_header.json
│   ├── kpmg_metric_card.json
│   ├── kpmg_status_panel.json
│   ├── kpmg_data_field_row.json
│   ├── kpmg_confirmation_modal.json
│   └── kpmg_action_bar.json
├── python/
│   ├── theme.py                       # Brand tokens (#00338D, Roboto, etc.)
│   ├── builders.py                    # Programmatic payload builders
│   └── provider.py                    # A2uiSchemaManager catalog integration
└── client/
    └── README.md                      # Custom frontend registration (Lit/Angular)
```

## Widget catalog

| Widget | Purpose | Example file |
|--------|---------|--------------|
| **KpmgBrandedHeader** | Centered icon + title + subtitle | `kpmg_branded_header.json` |
| **KpmgMetricCard** | KPI cards in a row (verified / pending / total) | `kpmg_metric_card.json` |
| **KpmgStatusPanel** | Status summary with icon and detail caption | `kpmg_status_panel.json` |
| **KpmgDataFieldRow** | Read-only icon + label + value rows | `kpmg_data_field_row.json` |
| **KpmgConfirmationModal** | Confirm/cancel modal dialog | `kpmg_confirmation_modal.json` |
| **KpmgActionBar** | Primary + secondary action buttons | `kpmg_action_bar.json` |

### KPMG theme

All examples use consistent surface styles:

```json
{
  "primaryColor": "#00338D",
  "font": "Roboto"
}
```

Additional tokens are defined in `python/theme.py` and `catalog/kpmg_catalog_definition.json`.

## Agent integration (Gemini Enterprise)

### 1. Register KPMG examples with the schema manager

In your agent's `agent.py`, replace `BasicCatalog.get_config(...)` with `KpmgWidgetsCatalog`:

```python
from a2ui.schema.manager import A2uiSchemaManager
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.schema.constants import VERSION_0_8
from widgets.python.provider import KpmgWidgetsCatalog

schema_manager = A2uiSchemaManager(
    version=VERSION_0_8,
    catalogs=KpmgWidgetsCatalog.get_catalogs_for_agent(
        agent_examples_path=examples_path,
    ),
    schema_modifiers=[remove_strict_validation],
)
```

This merges KPMG widget examples with your agent-specific examples so the LLM learns both patterns.

### 2. Prompt the agent to use KPMG styling

Add to your agent config YAML under `prompts.ui`:

```yaml
ui: >
  Use KPMG-branded A2UI surfaces with primaryColor #00338D and font Roboto.
  Prefer KpmgBrandedHeader for page headers, KpmgDataFieldRow for read-only
  fields, KpmgActionBar for form actions, and KpmgStatusPanel for status updates.
  All A2UI JSON MUST be wrapped in <a2ui-json> and </a2ui-json> tags.
```

### 3. Deploy to Gemini Enterprise

Register the agent with A2UI extension support (already handled by `scripts/deploy.py`):

- Extension URI: `https://a2ui.org/a2a-extension/a2ui/v0.8`
- Catalog: `https://a2ui.org/specification/v0_8/standard_catalog_definition.json`

Gemini Enterprise validates payloads against the **standard catalog** and renders them in its built-in A2UI renderer.

## Programmatic builders

Use `widgets/python/builders.py` to generate payloads from Python instead of hand-writing JSON:

```python
import json
from widgets.python.builders import (
    branded_header,
    data_field_row,
    action_bar,
    surface_messages,
)

components = []
components += branded_header("header", "Employee Verification", subtitle_path="/name")
components += data_field_row("field_empid", "Employee ID", "/employee_id", icon="badge")
components += action_bar(
    "actions",
    primary_label="Submit & Verify",
    primary_action="submit_verification",
    secondary_label="Verify As Is",
    secondary_action="verify_as_is",
)

payload = surface_messages(
    surface_id="employee-form",
    root="main_card",
    components=components,
    data_contents=[
        {"key": "name", "valueString": "John Smith"},
        {"key": "employee_id", "valueString": "E-1001"},
    ],
)

print(f"<a2ui-json>{json.dumps(payload)}</a2ui-json>")
```

## Prototyping with A2UI Composer

Use [A2UI Composer](https://a2ui-composer.ag-ui.com/) to visually design surfaces, then export JSON into `widgets/examples/0.8/` or your agent's examples directory. Apply KPMG theme values from `python/theme.py` in the `beginRendering.styles` block.

## Custom frontend (optional)

If you build a standalone web app (Lit, Angular, CopilotKit) instead of Gemini Enterprise, see `client/README.md` for registering custom component renderers per the [Authoring Components guide](https://a2ui.org/guides/authoring-components/#2-implementing-the-component-client).

For Gemini Enterprise deployments, **no custom client is required** — standard component compositions are sufficient.
