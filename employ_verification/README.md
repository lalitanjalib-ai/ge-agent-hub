# Employee Verification Agent — v5 (Built-in Gemini Enterprise OAuth Propagation)

An A2A/A2UI agent that helps employees look up, update, and verify their
employment records in BigQuery. This version uses Vertex AI Agent Engine's
**Bring-Your-Own-Dockerfile (BYOC)** deployment mode together with Gemini
Enterprise's **Agent Engine V2 ingress** feature to propagate the logged-in
user's Google OAuth access token straight onto the standard `Authorization`
header of the incoming A2A request — no STS/WIF token exchange, no
multi-location scanning of ADK session state.

## Why this exists (history)

Earlier iterations of this agent (v3/v4) used the managed
`vertexai.preview.reasoning_engines.templates.a2a.A2aAgent` template, which:
- builds its own Starlette app internally (no way to add middleware), and
- constructs the registered A2A card `url` in a way that, once Gemini
  Enterprise appended its own `/v1/message:send` suffix, produced a doubled
  `/a2a/v1/v1/message:send` path that 404'd — the request never reached the
  agent at all.

This meant the only way to receive the forwarded OAuth token was to guess at
several possible locations (`message.metadata`, `call_context.state`, ADK
session state, various key names) and disambiguate id_token vs. access_token
heuristically. Google has since documented a more direct mechanism (BYOC +
Agent Engine V2 ingress) that delivers the token on the plain `Authorization`
header, provided the hosting project is on the relevant allowlist.

## How the token flows

1. Gemini Enterprise runs the OAuth consent flow using the `Authorization`
   resource created by `scripts/setup_agent_auth.py` / `scripts/deploy.py`.
2. When a user invokes the agent, GE calls the Agent Engine **V2 ingress
   URL** (`.../reasoningEngines/{id}/api/a2a/...`). If the hosting project is
   on the allowlist, GE attaches the user's OAuth token on
   `X-Goog-Agent-User-Authorization`.
3. Agent Engine's own gateway rewrites that onto the standard
   `Authorization: Bearer <token>` header before the request reaches this
   container.
4. `main.py`'s `TokenExtractorMiddleware` captures that header into a
   per-request `ContextVar` (`employee_agent.token_context`). The BigQuery
   tools read it directly — no further processing needed.

> The `/api/` segment in the registered URL is required — GE only propagates
> the token to Agent Engine **V2** `/api/` URLs, not the legacy `/a2a/v1`
> URLs used by the managed `A2aAgent` template.

## Prerequisites

1. **Allowlist**: your hosting GCP project must be on Google's allowlist for
   Agent Engine V2 ingress OAuth propagation. Without it, deployment and A2A
   wiring still work, but no token is propagated — the agent falls back to
   its own service account for BigQuery (logged clearly).
2. A **Gemini Enterprise app** must already exist — note its app ID.
3. An **OAuth client ID** (type: Web application) with these redirect URIs:
   - `https://vertexaisearch.cloud.google.com/oauth-redirect`
   - `https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`

## Setup

```bash
cp .env.example .env
# fill in PROJECT_ID, LOCATION, STORAGE_BUCKET, GEMINI_ENTERPRISE_APP_ID,
# OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET

uv sync   # or: pip install -r requirements.txt

python adhoc/setup_employee_bq.py     # creates + seeds the BigQuery table
python scripts/grant_permissions.py   # grants IAM to service accounts
```

## Deploy

```bash
python scripts/deploy.py              # first deploy or in-place redeploy
python scripts/deploy.py --dry-run    # preview only
python scripts/deploy.py --undeploy   # unregister from GE
python scripts/deploy.py --undeploy --delete-engine  # also delete the Reasoning Engine
```

First-time deploy builds a container from the local `Dockerfile` +
`requirements.txt` (BYOC mode, ~5-10 minutes) and registers it with Gemini
Enterprise at the V2 ingress URL. Redeploys update the same Reasoning Engine
resource in place, so the GE registration and OAuth authorization resource
never need to be detached/reattached.

## Local development

```bash
uv run uvicorn main:app --port 8080
curl http://localhost:8080/healthz
curl http://localhost:8080/api/a2a/.well-known/agent-card.json
```

Locally (without going through Gemini Enterprise), no `Authorization` header
will be present, so the BigQuery tools fall back to Application Default
Credentials — this is expected and logged.

## Testing end-to-end

1. Open the Gemini Enterprise app, select "Employee Verification Agent
   (v5)", complete the OAuth consent prompt when asked, and ask something
   like "Look up employee E-1001".
2. Check Cloud Logging for the Reasoning Engine — look for:
   - `OBO: propagated Authorization header captured on this request.` — the
     token arrived; and
   - `OBO: propagated OAuth token present — BigQuery calls will run as the
     end user` — BigQuery calls will run as the logged-in user.
   If instead you see `OBO: no Authorization header on this request.`, the
   token was not propagated — check the allowlist status and that the agent
   is registered at the V2 `/api/...` URL (not `/a2a/v1`).

## Project layout

```
employ_verification/
├── main.py                      # BYOC A2A server (TokenExtractorMiddleware, mounted under /api)
├── Dockerfile
├── requirements.txt
├── employee_agent/
│   ├── agent.py                 # ADK Agent + instruction + tools
│   ├── token_context.py         # ContextVar holding the propagated OAuth token
│   └── tools/
│       ├── bq_client.py         # BigQuery client built from the propagated token
│       ├── lookup_employee.py
│       ├── update_employee_field.py
│       └── verify_employee.py
├── scripts/
│   ├── deploy.py                # BYOC create/update + GE registration at V2 ingress URL
│   ├── setup_agent_auth.py      # creates the GE authorization resource
│   └── grant_permissions.py
└── adhoc/
    └── setup_employee_bq.py     # seeds the BigQuery table
```
