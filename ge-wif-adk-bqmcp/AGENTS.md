# Coding Agent Guide

## Prerequisites

Install the CLI (one-time):
```bash
uv tool install google-agents-cli
```

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `agents-cli playground` for interactive testing. Iterate based on user feedback.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Start with 1-2 eval cases, run `agents-cli eval run`, iterate. Expect 5-10+ iterations. See the **Evaluation Guide** for metrics, evalset schema, LLM-as-judge config, and common gotchas.

### Phase 4: Pre-Deployment Tests
Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

### Phase 5: Deploy to Dev
**Requires explicit human approval.** Run `agents-cli deploy` only after user confirms. See the **Deployment Guide** for details.

### Phase 6: Production Deployment
Ask the user: Option A (simple single-project) or Option B (full CI/CD pipeline with `agents-cli infra cicd`).

## Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval run` | Run evaluation against evalsets |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |

---

## Deployment Targets

This repo ships the same `app.agent` under **two** deployment targets. Pick based on how Gemini Enterprise should reach it.

### Build/deploy vs. GE registration

These are two independent steps. The agent container only needs to know how to mint Google tokens (WIF provider + user-billing project). The GE auth-resource ID is a registration-time concept — `agents-cli publish gemini-enterprise --authorization-id <id>` — and never enters the agent process. `entra_wif_header_provider` locates the GE-injected Entra JWT in session state by scanning for any value whose `iss` claim is a Microsoft endpoint, so neither the agent nor the A2A shim has to know the slot name GE picks.

### Target A — Vertex AI Agent Runtime (ADK protocol)

The original happy path. Entrypoint: `app/agent_runtime_app.py`. Governed by `[tool.agents-cli].deployment_target = "agent_runtime"` in `pyproject.toml`. GE registers it as an ADK agent and injects the end-user Entra JWT into ADK session state; `app/entra_wif.py` reads it from state and exchanges via STS workforce-identity-federation.

**Deploy:**
```bash
set -a; source ./.env; set +a
agents-cli deploy --project "$GOOGLE_CLOUD_PROJECT" --region europe-west1 \
  --no-confirm-project \
  --update-env-vars "WIF_PROVIDER_RESOURCE=${WIF_PROVIDER_RESOURCE}"
```

Two non-obvious bits:
- `app/agent.py` reads `GOOGLE_CLOUD_PROJECT` and `WIF_PROVIDER_RESOURCE` at module import, so both must be in the shell environment for the `agents-cli` introspection subprocess — hence sourcing `.env`.
- `agents-cli deploy` rewrites the engine's `deploymentSpec.env` on every deploy, so `WIF_PROVIDER_RESOURCE` must be re-passed via `--update-env-vars` each time or the container will crash on import. `GOOGLE_CLOUD_PROJECT` is reserved on Agent Runtime (auto-injected) and will return `FAILED_PRECONDITION` if passed via `--update-env-vars`.

### Target B — Cloud Run (A2A protocol)

A FastAPI server exposing the same agent over A2A JSON-RPC. Entrypoint: `app/fast_api_app.py`. Container: `Dockerfile` at repo root (`uvicorn app.fast_api_app:app` on port 8080). Terraform: `deployment/terraform/cloud_run/single-project/` (Cloud Run v2, `session_affinity = true`, GCS logs bucket, app service account).

**Required env vars on the Cloud Run service:**
- `GOOGLE_CLOUD_PROJECT` — the user-billing project for STS exchange and BQ MCP calls. Cloud Run does **not** auto-inject this; the deploy script sets it from `PROJECT_ID`.
- `WIF_PROVIDER_RESOURCE` — full workforce-pool provider resource name (`//iam.googleapis.com/locations/global/workforcePools/<pool>/providers/<provider>`); consumed by `entra_wif_header_provider` as the STS audience.
- `LOGS_BUCKET_NAME` — telemetry / artifact upload bucket (created by Terraform).
- `APP_URL` — public Cloud Run URL; embedded in the agent card so GE can find the RPC endpoint.
- `AGENT_VERSION` — surfaced in the agent card.

**Deploy:** `scripts/deploy_cloud_run.sh` auto-sources `./.env` at the repo root, so the usual flow is just
```bash
bash scripts/deploy_cloud_run.sh
```
with `PROJECT_ID` and `WIF_PROVIDER_RESOURCE` set in `.env` (or exported in the shell). Build & deploy is outside `agents-cli`'s single-target model — the Cloud Run path is deliberately not registered as the `[tool.agents-cli].deployment_target`.

**Register with GE (A2A mode):**
```bash
agents-cli publish gemini-enterprise \
  --registration-type a2a \
  --agent-card-url <CLOUD-RUN-URL>/a2a/app/.well-known/agent-card.json \
  --authorization-id <auth-id> \
  --gemini-enterprise-app-id <ge-app-id>
```

**Auth shim.** GE sends the end-user Entra JWT in `Authorization: Bearer <jwt>` on the A2A POST (Cloud Run's own IAM-invoker token rides on `X-Serverless-Authorization` and is ignored). `EntraAuthMiddleware` in `app/fast_api_app.py` is a pure-ASGI middleware that captures the bearer into a per-request `ContextVar`; `_AuthInjectingSessionService` (a thin `InMemorySessionService` subclass that overrides only `create_session`) reads the ContextVar when `A2aAgentExecutor._prepare_session` calls `create_session(state={}, ...)` and writes the captured token into session state under a private internal key (`_ENTRA_TOKEN_STATE_KEY`). `entra_wif_header_provider` then finds it by issuer match — the key name is purely an implementation detail of the shim.

**Sessions:** in-memory (`InMemorySessionService`) plus Cloud Run `session_affinity = true`. The A2A scaffold does *not* honor `--session-type agent_platform_sessions`, so this is by design — wiring Agent Platform Sessions into Target B is a tracked follow-up, not an oversight.

---

## Operational Guidelines for Coding Agents

- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked.
- **Model 404 errors**: Set `GEMINI_LOCATION` (e.g., `global`, `eu`) — `agent.py` reads it and overrides `GOOGLE_CLOUD_LOCATION`, which the Agent Runtime force-sets to the engine deploy region. Don't change the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `agents-cli install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.
