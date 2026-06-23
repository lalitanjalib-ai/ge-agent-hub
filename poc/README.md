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
│   └── run_local.py  # Starts: python -m google.adk.cli web . --port 8080
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
| `SSL_VERIFY` | Set to `false` on Windows corporate machines with custom CAs |
| `REQUESTS_CA_BUNDLE` | Path to corporate CA bundle (mapped to `SSL_CERT_FILE` for Vertex AI) |

## Troubleshooting (Windows)

| Error | Cause | Fix |
|-------|-------|-----|
| `CERTIFICATE_VERIFY_FAILED` | Corporate proxy / custom CA | Add `SSL_VERIFY=false` to `.env`, or set `REQUESTS_CA_BUNDLE` to your CA `.pem` |
| Empty bubble after tool call | A2UI SDK uses `<a2ui-json>` tags; old callback only parsed the first block | Fixed in `a2ui_utils.py` — restart server, **+New Session**, hard-refresh browser |
| Cards show `(empty)` for list items | List `dataBinding` needs `valueMap` (keyed map), not `valueList` | Fixed in KPMG example + callback auto-converts `valueList` |
| `unexpected extra arguments (agent.py ...)` | `--allow_origins *` glob-expands to all files in `poc/` | Use `python scripts/run_local.py` (fixed — uses explicit localhost origins) |
| `unexpected extra arguments (poc widgets)` | Full Windows path split by `adk.EXE` | Same — script runs `adk web .` from inside `poc/` |
| Raw JSON in chat | Missing render callback | `a2ui_callback` is already wired in `agent.py` |

## References

- [ADK + A2UI Codelab](https://codelabs.developers.google.com/next26/adk-a2ui)
- [KPMG widgets library](../widgets/README.md)
- [A2UI Composer](https://a2ui-composer.ag-ui.com/)
