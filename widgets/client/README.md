# KPMG Widgets — Custom Client Registration

This folder is for **optional** custom frontend renderers when you build a standalone A2UI client (Lit, Angular, CopilotKit/AG-UI) rather than using Gemini Enterprise's built-in renderer.

> **Gemini Enterprise agents do not need this.** Widgets in `examples/0.8/` use standard catalog components and render natively in GE.

## When to use a custom client

| Deployment | Client needed? |
|------------|----------------|
| Gemini Enterprise (A2A agent) | No — GE renders standard A2UI |
| Custom web app + CopilotKit | Yes — register renderer in your app |
| Standalone Lit/Angular shell | Yes — implement + register components |

## Implementation steps

Follow the [A2UI Authoring Components guide](https://a2ui.org/guides/authoring-components/):

### 1. Define the catalog schema

Schema lives at `../catalog/kpmg_catalog_definition.json`. It documents semantic widget types (`KpmgMetricCard`, `KpmgBrandedHeader`, etc.).

### 2. Implement components

**Angular** — extend `DynamicComponent` from `@a2ui/angular`:

```typescript
import { DynamicComponent } from '@a2ui/angular';
import { Component, computed, input } from '@angular/core';

@Component({
  selector: 'kpmg-metric-card',
  template: `
    <div class="kpmg-metric-card">
      <span class="label">{{ resolvedLabel() }}</span>
      <span class="value">{{ resolvedValue() }}</span>
    </div>
  `,
})
export class KpmgMetricCard extends DynamicComponent {
  readonly label = input.required<Primitives.StringValue>();
  readonly value = input.required<Primitives.StringValue>();

  protected resolvedLabel = computed(() => this.resolvePrimitive(this.label()));
  protected resolvedValue = computed(() => this.resolvePrimitive(this.value()));
}
```

**Lit** — implement a custom element that reads A2UI properties and applies KPMG CSS variables:

```typescript
// kpmg-theme.ts
export const KPMG_CSS_VARS = {
  '--kpmg-primary': '#00338D',
  '--kpmg-secondary': '#0091DA',
  '--kpmg-font': 'Roboto, sans-serif',
};
```

### 3. Register with the renderer

**Angular catalog** (`catalog.ts` pattern from A2UI docs):

```typescript
import { Catalog, DEFAULT_CATALOG } from '@a2ui/angular';
import { inputBinding } from '@angular/core';

export const KPMG_CATALOG = {
  ...DEFAULT_CATALOG,
  KpmgMetricCard: {
    type: () => import('./kpmg-metric-card').then(r => r.KpmgMetricCard),
    bindings: ({ properties }) => [
      inputBinding('label', () => properties['label']),
      inputBinding('value', () => properties['value']),
    ],
  },
} as Catalog;
```

### 4. Advertise catalog during A2A negotiation

The client sends supported catalog IDs via the `X-A2A-Extensions` header. The agent's `A2uiSchemaManager` selects a compatible catalog.

For KPMG custom types, advertise:

```
https://kpmg.internal/a2ui/v0_8/kpmg_widgets_catalog_definition.json
```

## Mapping semantic widgets to standard components

Until a custom client is deployed, agents should emit **standard component trees** (see `examples/0.8/`). The semantic names in `kpmg_catalog_definition.json` serve as documentation for agents and as schema for future native implementations.

## Related resources

- [Gemini Enterprise + A2UI integration guide](https://cloud.google.com/blog/topics/developers-practitioners/guide-to-gemini-enterprise-and-a2ui-integration)
- [A2UI Composer](https://a2ui-composer.ag-ui.com/) — visual prototyping
- [A2UI catalog negotiation](https://a2ui.org/guides/catalog-negotiation/)
