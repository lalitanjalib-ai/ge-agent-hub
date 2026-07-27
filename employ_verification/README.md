# Employee Verification — Google OAuth v3 (emp_verify_google_oauth_v3)

An A2UI/A2A agent, hosted on Vertex AI Agent Engine and registered in Gemini
Enterprise, that lets employees look up, update, and verify their employment
records stored in BigQuery. The agent runs queries **on behalf of the
logged-in user** using direct Google OAuth — no Entra ID, no Security Token
Service (STS), no Workforce Identity Federation.

## Auth model — Google OAuth direct On-Behalf-Of (OBO)

- **Hosting**: Vertex AI Agent Engine (Reasoning Engine) in `us-central1`.
- **GE registration**: Pure **A2A** (`a2aAgentDefinition`), not
  `adkAgentDefinition`. GE calls the Reasoning Engine's A2A URL directly from
  the agent card. This is required for A2UI DataParts (interactive forms) to
  render correctly in the GE chat UI.
- **GE authorization resource**: Uses a **GCP OAuth 2.0 Web client**
  (`accounts.google.com`), not Entra. Users sign in with their own Google /
  Cloud Identity account (which, for an org federated with Entra ID, is
  transparently SSO'd through Entra — the agent never sees Entra tokens).
- **Token flow**: GE forwards the user's **Google OAuth access token** to the
  agent. The agent wraps it directly in `google.oauth2.credentials.Credentials`
  and uses it for BigQuery — `OBO_CREDENTIAL_MODE=google_direct`. No token
  exchange of any kind.

```
User → (Entra SSO, transparent) → Google OAuth consent (GE authorization resource)
     → GE forwards access_token → Agent (extract_user_token)
     → bigquery.Client(credentials=Credentials(token=access_token))
     → BigQuery runs AS the user (IAM + audit logs attributed correctly)
```

### Why this matters: access_token vs id_token

Google's OAuth2 token endpoint issues both an `access_token` and (if
`openid` scope was requested) an `id_token` on every code exchange. **Only
the `access_token` is valid for calling Google APIs like BigQuery.** If the
GE authorization resource's OAuth consent/scopes are misconfigured such that
GE ends up forwarding the `id_token` instead, the agent will fail with
`OBOAuthError` and a clear log message — Google does not support exchanging
an id_token for an access_token client-side (unlike Entra's STS-based token
exchange). The fix is always on the GE authorization resource / OAuth
consent side (see Troubleshooting below), not in application code.

## Repository layout

```
employ_verification/
├── pyproject.toml              # Dependencies
├── .env.example                # Environment variable template
├── README.md
│
├── adhoc/
│   └── setup_employee_bq.py    # One-time: create BQ dataset/table + mock data
│
├── config/
│   ├── _defaults.yaml          # Shared defaults (model, region, base requirements)
│   └── emp_verify_google_oauth_v3.yaml   # This agent's full config
│
├── agents/
│   ├── _base/
│   │   ├── config_loader.py    # YAML config loader + merger
│   │   ├── base_executor.py    # A2A/A2UI executor — captures OBO token per request
│   │   ├── agent_card.py       # A2A agent card builder
│   │   ├── user_context.py     # Token extraction + Google OAuth credential building
│   │   └── exceptions.py       # OBOAuthError — raised on unusable forwarded tokens
│   └── emp_verify_google_oauth_v3/
│       ├── agent.py            # ADK Agent definition
│       └── executor.py         # 2-line executor subclass
│
├── tools/
│   ├── registry.py             # Tool metadata catalog
│   └── employee/
│       ├── bq_client.py        # BigQuery client factory — OBO creds, else ADC fallback
│       ├── lookup_employee.py
│       ├── update_employee_field.py
│       └── verify_employee.py
│
└── scripts/
    ├── deploy.py                # Deploy CLI (Agent Engine create/update-in-place)
    ├── setup_agent_auth.py      # (Rarely needed manually) GE authorization resource setup
    ├── grant_permissions.py     # IAM grants for the service account + named users
    └── verify_google_oauth.py  # Manual test: Google access token → BigQuery, no GE
```

## Getting Started

### Prerequisites
- Google Cloud Project with Vertex AI, BigQuery, and Discovery Engine
  (Gemini Enterprise) APIs enabled.
- `gcloud` CLI authenticated: `gcloud auth application-default login`
- Python 3.11+ with `uv` (recommended)
- **Windows corporate machine?** Add `SSL_VERIFY=false` to your `.env`.

### Step 1 — Install dependencies
```bash
cd employ_verification
uv sync
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### Step 2 — Configure environment
```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Description |
|---|---|
| `PROJECT_ID` | Your GCP project ID |
| `LOCATION` | Agent Engine region (e.g. `us-central1`) |
| `STORAGE_BUCKET` | GCS bucket for staging (e.g. `gs://my-bucket`) |
| `GEMINI_ENTERPRISE_APP_ID` | Your GE app ID |
| `GE_LOCATION` | GE app region: `global`, `us`, or `eu` |
| `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` | GCP OAuth 2.0 Web client (APIs & Services → Credentials) |

Required redirect URIs on that OAuth client:
- `https://vertexaisearch.cloud.google.com/oauth-redirect`
- `https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`

### Step 3 — Enable required GCP APIs
```bash
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  discoveryengine.googleapis.com \
  --project=$PROJECT_ID
```

### Step 4 — Set up BigQuery (mock employee data)
```bash
python adhoc/setup_employee_bq.py
```

### Step 5 — Grant IAM permissions
```bash
python scripts/grant_permissions.py
```
Grants least-privilege BigQuery access to the Reasoning Engine service
account (ADC fallback path). Users authenticate as themselves for the OBO
path, so they need their own BigQuery IAM on the dataset (either granted
individually, via `GE_USER_PERMISSION` in `.env`, or via your org's normal
IAM/Group management).

### Step 6 — Deploy
```bash
python scripts/deploy.py
```

This single command reads `config/emp_verify_google_oauth_v3.yaml`'s
`deploy:` block and:

1. **First-time deploy** (no `deploy.reasoning_engine` recorded, or that
   engine no longer exists): creates a new Agent Engine (Reasoning Engine)
   resource, then writes the new resource name back into the YAML.
2. **Redeploy** (an existing `deploy.reasoning_engine` is still valid):
   updates that **same** Reasoning Engine resource in place via
   `agent_engines.update()` — the resource name and A2A URL never change.
3. Creates the GE OAuth authorization resource
   (`deploy.agent_authorization_id`) if it doesn't already exist — this only
   happens once, on first deploy.
4. Registers (or PATCHes in place) the GE agent as `a2aAgentDefinition`.
   Because the underlying engine resource name doesn't change between
   deploys, the authorization is **never detached or reattached** on a
   normal redeploy.
5. Sets gallery visibility to `ALL_USERS`.

No manual `setup_agent_auth.py` step is required for normal deploys.

That's it — the agent appears in the Gemini Enterprise Agent Gallery as
**Employee Verification Google OAuth v3**.

## Deploy CLI Reference

```bash
# Deploy (first-time or in-place redeploy, auto-detected)
python scripts/deploy.py

# Preview config without deploying
python scripts/deploy.py --dry-run

# Delete + recreate the Reasoning Engine (e.g. for agent_framework changes)
python scripts/deploy.py --force-recreate-engine

# Unregister from Gemini Enterprise
python scripts/deploy.py --undeploy

# Unregister AND delete the Agent Engine resource
python scripts/deploy.py --undeploy --delete-engine

# Re-register an already-deployed engine without redeploying
python scripts/deploy.py --register-only --reasoning-engine <RESOURCE_ID_OR_FULL_NAME>
```

## Validating OBO (outside Gemini Enterprise)

```bash
gcloud auth print-access-token | python scripts/verify_google_oauth.py -
```

This exercises the exact same code path the deployed agent uses: a Google
OAuth access token → `bigquery.Client(credentials=...)` → a test query. If
this succeeds locally but the deployed agent still fails, the issue is in
how GE is forwarding (or not forwarding) the token — not in the agent code.

After deploying, watch Agent Engine logs for:
- `OBO credential mode: google_direct`
- `OBO: using forwarded Google OAuth access token directly (no STS)`
- `BigQuery: using forwarded Google OAuth token (direct OBO, no STS)`

## Troubleshooting: `OBOAuthError` / "authentication issue" replies

If users see the generic message *"Sorry, I'm unable to access your
employee records right now due to an authentication issue..."*, check the
Agent Engine logs for an `OBOAuthError` / `OBO_AUTH_ERROR` entry. The two
known causes are both logged explicitly:

1. **Google id_token forwarded instead of access_token** — the GE
   authorization resource's OAuth consent isn't issuing/forwarding an
   access_token with the right scopes. Fix: confirm `OAUTH_SCOPES` includes
   `https://www.googleapis.com/auth/bigquery` and
   `https://www.googleapis.com/auth/cloud-platform`, then force-recreate the
   authorization and have the affected user re-consent:
   ```bash
   python scripts/setup_agent_auth.py --id auth-emp-verify-google-oauth-v3 --force
   ```
   Have the user revisit myaccount.google.com/permissions to revoke stale
   consent first, if a fresh consent screen doesn't appear.

2. **Entra JWT forwarded instead of a Google token** — the GE authorization
   resource is misconfigured to use an Entra/Microsoft identity provider
   instead of Google OAuth (`accounts.google.com`). Recreate the
   authorization resource with the correct `OAUTH_AUTHORIZATION_URI` /
   `OAUTH_TOKEN_URI` pointed at Google, not Microsoft.

Set `STRICT_OBO=true` in `.env` while validating to make any other type of
OBO failure raise loudly instead of silently falling back to the service
account (ADC) — without this, a broken OBO path is invisible in normal
usage (the agent still returns data, just under the wrong identity).

## Config System

- `config/_defaults.yaml` — shared defaults inherited by this agent (model,
  region, base pip requirements).
- `config/emp_verify_google_oauth_v3.yaml` — this agent's full config
  (prompts, tools, deploy settings). Deep-merged on top of the defaults.

## Key Design Decisions

| Aspect | How it works |
|--------|-------------|
| **Hosting** | Vertex AI Agent Engine (Reasoning Engine) |
| **GE registration** | Pure A2A (`a2aAgentDefinition`) — required for A2UI DataParts |
| **OBO** | Direct Google OAuth — `OBO_CREDENTIAL_MODE=google_direct`, no STS/WIF |
| **Auth resources** | Single stable ID in YAML — created once on first deploy, never rotated |
| **Failure mode** | `OBOAuthError` raised (not silently swallowed) on a definitively unusable forwarded token; generic message shown to users, full detail in logs |
| **Config** | YAML in `config/`, merged with `_defaults.yaml` |
