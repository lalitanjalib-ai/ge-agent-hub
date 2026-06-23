# A2UI Local Sample (POC)

Local development sample following the [ADK + A2UI Codelab](https://codelabs.developers.google.com/next26/adk-a2ui).

The agent lives **directly in this folder** (`agent.py`, `resources.py`, `a2ui_utils.py`) and uses the **A2UI Python SDK** (`A2uiSchemaManager`, `BasicCatalog` via `KpmgWidgetsCatalog`) plus KPMG widget examples from [`../widgets/`](../widgets/).

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended)
- GCP project with Vertex AI enabled
- `gcloud auth application-default login`

## Quick start

```bash
cd poc
uv sync
cp .env.example .env
# Edit .env — set GOOGLE_CLOUD_PROJECT

python scripts/run_local.py
```

Open **http://127.0.0.1:8080**, select the **poc** agent, start a new session.

## Sample prompts

```
What's running in my project?
```

```
Does anything need my attention?
```

```
I need to deploy a new service
```

## Layout

```
poc/
├── agent.py          # ADK agent + A2uiSchemaManager system prompt
├── resources.py      # Mock cloud resources (codelab data)
├── a2ui_utils.py     # after_model_callback for adk web rendering
├── scripts/
│   └── run_local.py  # Starts: adk web <poc> --port 8080
├── pyproject.toml
└── .env.example
```

## How it works (codelab steps)

1. **A2uiSchemaManager** teaches the LLM the A2UI v0.8 catalog and few-shot examples.
2. The LLM returns JSON messages: `beginRendering`, `surfaceUpdate`, `dataModelUpdate`.
3. **`a2ui_callback`** converts JSON text into parts that `adk web` renders as interactive UI.

## Environment variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | `global` (default) |
| `GOOGLE_GENAI_USE_VERTEXAI` | `True` |
| `GOOGLE_GENAI_MODEL` | Optional (default: `gemini-2.5-flash`) |

## References

- [ADK + A2UI Codelab](https://codelabs.developers.google.com/next26/adk-a2ui)
- [KPMG widgets library](../widgets/README.md)
- [A2UI Composer](https://a2ui-composer.ag-ui.com/)
