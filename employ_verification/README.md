# Agent Framework

A config-driven multi-agent platform for deploying **A2UI**, **A2A**, and **Google ADK** agents to **Gemini Enterprise** via **Agent Engine**.

## Flagship Deployment: emp_verify_google_oauth_v3

The **Employee Verification Google OAuth v3** agent (`emp_verify_google_oauth_v3`) is the current, actively
deployed agent in this repo. It demonstrates the production pattern for GE-hosted agents with **direct Google
OAuth On-Behalf-Of** — no Entra, no STS, no Workforce Identity Federation.

- **Hosting**: Vertex AI Agent Engine (Reasoning Engine) in `us-central1`.
- **GE registration**: Pure **A2A** (`a2aAgentDefinition`), not `adkAgentDefinition`. GE calls the Reasoning
  Engine's A2A URL directly from the agent card. This is required for A2UI DataParts (interactive forms) to
  render correctly in the GE chat UI.
- **Auth**: GE authorization resource uses **Google OAuth** (`accounts.google.com`). GE forwards the user's
  **Google access token** to the agent; BigQuery runs as that user (`OBO_CREDENTIAL_MODE=google_direct`).
- **A2UI catalog**: Uses `BasicCatalog` (not the repo-root `widgets/` package) because the KPMG widgets
  package is not available on Agent Engine's runtime.
- **Gallery name**: Search for **Employee Verification Google OAuth v3** in the GE Agent Gallery.

### Prerequisites (.env)

Use a **GCP OAuth 2.0 Web client** (not Entra) for the GE authorization resource:

```env
OAUTH_AUTHORIZATION_URI=https://accounts.google.com/o/oauth2/v2/auth
OAUTH_TOKEN_URI=https://oauth2.googleapis.com/token
OAUTH_SCOPES=https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/cloud-platform openid email profile
OAUTH_CLIENT_ID=<GCP OAuth 2.0 Web client ID>
OAUTH_CLIENT_SECRET=<client secret>
OBO_CREDENTIAL_MODE=google_direct
```

Add these redirect URIs to the OAuth client in GCP Console → APIs & Services → Credentials:

- `https://vertexaisearch.cloud.google.com/oauth-redirect`
- `https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`

Users who sign in need **BigQuery IAM** on the project/dataset (their Cloud Identity / Google account
principal), not Workforce pool principals.

### Deploying emp_verify_google_oauth_v3

```bash
cd employ_verification
python scripts/deploy.py emp_verify_google_oauth_v3
```

This single command reads `config/emp_verify_google_oauth_v3.yaml`'s `deploy:` block and:

1. Deploys the ADK agent (wrapped as an `A2aAgent`) to Vertex AI Agent Engine.
2. Creates or reuses a GE OAuth authorization resource via **blue/green rotation** — each deploy registers
   against the standby slot (`auth-emp-verify-google-oauth-v3-blue` / `auth-emp-verify-google-oauth-v3-green`)
   so the resource is never locked by a just-deleted prior registration. `deploy.py` flips
   `active_auth_slot` in the YAML after a successful deploy.
3. Unregisters any stale prior GE registration and re-registers as `a2aAgentDefinition`.
4. Sets gallery visibility to `ALL_USERS` so the agent appears in the GE Agent Gallery for all users.

No manual `setup_agent_auth.py` step is required for normal v3 deploys — auth resources are managed by
`deploy.py`. Use `setup_agent_auth.py --force` only if you need to recreate a locked auth resource outside
the blue/green flow.

### Re-registering an already-deployed engine (no redeploy)

```bash
python scripts/deploy.py emp_verify_google_oauth_v3 --register-only --reasoning-engine <RESOURCE_ID_OR_FULL_NAME>
```

The latest deployed engine ID is recorded in `config/emp_verify_google_oauth_v3.yaml` under
`deploy.reasoning_engine`.

### Validate Google OAuth OBO (outside GE)

```bash
gcloud auth print-access-token | python scripts/verify_google_oauth.py -
```

This exercises the same `google_direct` path the agent uses: Google access token → BigQuery as the user.

After deploying, watch engine logs for `OBO credential mode: google_direct` and
`BigQuery: using On-Behalf-Of user (google_direct) credentials` vs the ADC fallback line.

### Dry run

```bash
python scripts/deploy.py emp_verify_google_oauth_v3 --dry-run
```

---

## Alternative: emp_verify_v2 (Entra → WIF OBO)

`emp_verify_v2` uses **Microsoft Entra ID → Google STS → Workforce Identity Federation** for OBO. It
remains in the repo for environments that require Entra sign-in. See [Entra/WIF OBO](#on-behalf-of-obo-user-authentication--entra-id--workforce-identity-emp_verify_v2) below.

```bash
python scripts/deploy.py emp_verify_v2
```

If GE reports the authorization resource as `"used by another agent"` after redeploy, `deploy.py` retries
automatically. If still locked:

```bash
python scripts/setup_agent_auth.py --id auth-emp-verify-v2 --force
python scripts/deploy.py emp_verify_v2 --register-only --reasoning-engine <RESOURCE_ID>
```

---

## Architecture

```
dn-innov-a2ui/
├── widgets/                              # KPMG A2UI widgets library (shared)
│   ├── catalog/                          # Widget schema definitions
│   ├── examples/0.8/                     # Branded example payloads
│   └── python/                           # Builders + schema provider
│
└── employ_verification/                  # Agent platform
    ├── pyproject.toml                    # Shared dependencies
    ├── .env / .env.example               # Environment configuration
    ├── .gitignore
    │
    ├── adhoc/                            # One-time setup scripts (run before first deploy)
    │   ├── README.md                     # Instructions for each adhoc script
    │   └── setup_employee_bq.py          # Create BQ dataset, table, and mock data
    │
    ├── config/                           # Agent configurations (YAML)
    │   ├── _defaults.yaml                # Shared defaults (model, region, etc.)
    │   ├── emp_verify_google_oauth_v3.yaml  # Flagship — Agent Engine + A2A + Google OAuth OBO
    │   ├── emp_verify_v2.yaml            # Entra → WIF OBO variant
    │   └── employee_verification.yaml    # Original v1 agent config
    │
    ├── agents/                           # Agent definitions (one folder per agent)
    │   ├── _base/                        # Shared base classes
    │   │   ├── config_loader.py          # YAML config loader + merger
    │   │   ├── base_executor.py          # Generic A2A/A2UI executor (captures OBO token per request)
    │   │   ├── agent_card.py             # A2A agent card builder with A2UI extension metadata
    │   │   ├── token_exchange.py         # RFC 8693 STS call (Entra → WIF; used by emp_verify_v2)
    │   │   └── user_context.py           # Extracts forwarded token; builds user Credentials
    │   │
    │   ├── emp_verify_google_oauth_v3/   # Employee Verification v3 (flagship, Google OAuth OBO)
    │   │   ├── agent.py                  # ADK Agent — BasicCatalog for Agent Engine
    │   │   └── executor.py               # Thin executor subclass
    │   │
    │   ├── emp_verify_v2/                # Employee Verification v2 (Entra → WIF OBO)
    │   │   ├── agent.py
    │   │   └── executor.py
    │   │
    │   └── employee_verification/        # Employee Verification v1 (reference implementation)
    │       ├── agent.py
    │       ├── executor.py
    │       └── examples/0.8/             # A2UI JSON examples (shared with v2/v3)
    │
    ├── tools/                            # Shared tool library
    │   ├── registry.py                   # Tool metadata catalog
    │   └── employee/                     # Tools grouped by domain
    │       ├── bq_client.py              # BigQuery client factory — OBO creds, else ADC fallback
    │       ├── lookup_employee.py
    │       ├── update_employee_field.py
    │       └── verify_employee.py
    │
    ├── scripts/                          # Deploy + lifecycle + diagnostics
    │   ├── deploy.py                     # Generic deploy CLI (Agent Engine; blue/green auth rotation)
    │   ├── undeploy.py                   # Tear down agents
    │   ├── setup_agent_auth.py           # Create/recreate GE OAuth authorization resource
    │   ├── grant_permissions.py          # IAM grants for OBO identities + service account
    │   ├── validate_wif_config.py        # Pre-flight .env alignment checks (Entra/WIF path)
    │   ├── verify_wif.py                 # Entra → STS → BigQuery validation (emp_verify_v2)
    │   ├── verify_google_oauth.py        # Google access token → BigQuery validation (v3)
    │   ├── get_entra_token.py            # MSAL device-code flow (Entra/WIF testing)
    │   ├── debug_agent.py                # Consolidated logs/traces/diagnostics tool
    │   └── get_agent_card.py             # Fetch A2A agent card from a deployed Reasoning Engine
    │
    └── data/                             # Mock data, schemas, etc.
```

---

## Getting Started (New Project Setup)

Follow these steps **in order** when setting up in a new GCP project.

### Prerequisites
- Google Cloud Project with Vertex AI and Gemini Enterprise enabled
- `gcloud` CLI authenticated: `gcloud auth application-default login`
- Python 3.11+ with `uv` (recommended)
- **Windows corporate machine?** Add `SSL_VERIFY=false` to your `.env` (see Step 2)

---

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

Edit `.env` and fill in the shared variables:

| Variable | Description |
|---|---|
| `PROJECT_ID` | Your GCP project ID |
| `LOCATION` | Agent Engine region (e.g. `us-central1`) |
| `STORAGE_BUCKET` | GCS bucket for staging (e.g. `gs://my-bucket`) |
| `GEMINI_ENTERPRISE_APP_ID` | Your GE app ID from the GCP Console |
| `GE_LOCATION` | GE app region: `global`, `us`, or `eu` (regional apps, e.g. `us`, cannot use `global` auth resources) |
| `SSL_VERIFY` | Set to `false` on Windows corporate machines with custom CAs |

**For `emp_verify_google_oauth_v3` (recommended)** — use a GCP OAuth 2.0 Web client:

| Variable | Value |
|---|---|
| `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` | GCP OAuth 2.0 Web client (APIs & Services → Credentials) |
| `OAUTH_AUTHORIZATION_URI` | `https://accounts.google.com/o/oauth2/v2/auth` |
| `OAUTH_TOKEN_URI` | `https://oauth2.googleapis.com/token` |
| `OAUTH_SCOPES` | `https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/cloud-platform openid email profile` |
| `OBO_CREDENTIAL_MODE` | `google_direct` |

**For `emp_verify_v2` (Entra → WIF)** — use an Entra OAuth client and WIF pool settings instead; see
[Entra/WIF OBO](#on-behalf-of-obo-user-authentication--entra-id--workforce-identity-emp_verify_v2).

> **Finding your GE App ID:** GCP Console → Vertex AI → Agent Builder → your app → copy the ID from the URL

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
This grants least-privilege BigQuery access to the Reasoning Engine service account (ADC fallback) and,
for the Entra/WIF path, to Workforce Identity pool principals. For **Google OAuth v3**, ensure end users
have BigQuery access on the dataset under their Google / Cloud Identity account.

### Step 6 — Set up OAuth authorization resource

**v3 (`emp_verify_google_oauth_v3`)**: skip this step — `deploy.py` creates auth resources automatically
via blue/green rotation (`auth-emp-verify-google-oauth-v3-blue` / `-green`).

**v2 (`emp_verify_v2`)**:
```bash
python scripts/setup_agent_auth.py --id auth-emp-verify-v2
```

> **OAuth client redirect URIs** (both v3 and v2):
> - `https://vertexaisearch.cloud.google.com/oauth-redirect`
> - `https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`

### Step 7 — Deploy
```bash
python scripts/deploy.py emp_verify_google_oauth_v3
```

That's it! The agent deploys to Agent Engine, registers in Gemini Enterprise as a pure A2A agent, and
appears in the Agent Gallery as **Employee Verification Google OAuth v3**.

---

## On-Behalf-Of (OBO) — Google OAuth direct (emp_verify_google_oauth_v3)

The v3 agent skips Entra and Workforce Identity entirely. Users sign in with **Google** in Gemini
Enterprise; GE forwards a **Google OAuth access token** to the agent, and BigQuery runs as that user.

### How it works

```mermaid
sequenceDiagram
    participant User
    participant GE as Gemini Enterprise
    participant Google as Google OAuth
    participant Agent as A2A Executor (Agent Engine)
    participant BQ as BigQuery

    User->>GE: prompt (first time)
    GE->>Google: OAuth2 consent (authorization resource)
    Google-->>GE: Google access token
    GE->>Agent: A2A message + forwarded Google token
    Agent->>Agent: extract_user_token(context)
    Agent->>BQ: query AS the user (google_direct creds)
    BQ-->>Agent: user-scoped rows
    Agent-->>GE: A2UI response
```

### Key files

| File | Responsibility |
|------|----------------|
| `agents/_base/user_context.py` | `OBO_CREDENTIAL_MODE=google_direct` — wraps forwarded Google token in `Credentials` |
| `agents/_base/base_executor.py` | Sets `obo_credential_mode` from YAML per request; captures token into `ContextVar` |
| `tools/employee/bq_client.py` | `get_bigquery_client()` — user (OBO) creds, else ADC fallback |
| `config/emp_verify_google_oauth_v3.yaml` | `obo_credential_mode: google_direct`, blue/green auth IDs, `ge_access_policy` |

If **no** user token is forwarded, tools transparently fall back to Application Default Credentials (the
Reasoning Engine service account).

### Blue/green auth resource rotation

`config/emp_verify_google_oauth_v3.yaml` defines two authorization IDs and an `active_auth_slot`:

```yaml
agent_authorization_ids:
  - "auth-emp-verify-google-oauth-v3-blue"
  - "auth-emp-verify-google-oauth-v3-green"
active_auth_slot: "green"
```

Each deploy registers against the **standby** slot (the one not currently active), avoiding GE's
`"used by another agent"` lock when swapping engines. `deploy.py` flips `active_auth_slot` after a
successful deploy — do not edit it by hand unless recovering from a failed deploy.

### Validating Google OAuth OBO (outside GE)

```bash
gcloud auth print-access-token | python scripts/verify_google_oauth.py -
```

Once deployed, use `python scripts/debug_agent.py emp_verify_google_oauth_v3 --follow` and watch for
`OBO credential mode: google_direct` and `BigQuery: using On-Behalf-Of user (google_direct) credentials`.

---

## On-Behalf-Of (OBO) User Authentication — Entra ID → Workforce Identity (emp_verify_v2)

KPMG users sign in with **Microsoft Entra ID**, but BigQuery (and other Google
APIs) only accept **Google** credentials. To run queries *as the logged-in user*
(so user-level ACLs and audit logs are honored) the agent performs an
On-Behalf-Of token exchange.

### How it works

```mermaid
sequenceDiagram
    participant User
    participant GE as Gemini Enterprise
    participant Entra as Microsoft Entra ID
    participant Agent as A2A Executor (Agent Engine)
    participant STS as Google STS
    participant BQ as BigQuery

    User->>GE: prompt (first time)
    GE->>Entra: OAuth2 consent (authorization resource)
    Entra-->>GE: user token
    GE->>Agent: A2A message + forwarded Entra token
    Agent->>Agent: extract_user_token(context)
    Agent->>STS: token-exchange (Entra token -> WIF)
    STS-->>Agent: Google federated access token
    Agent->>BQ: query AS the user (federated creds)
    BQ-->>Agent: user-scoped rows
    Agent-->>GE: A2UI response
```

### Two distinct Entra apps — do not confuse them

| App | Role |
|---|---|
| **GE OAuth Client** (`OAUTH_CLIENT_ID` in `.env`) | Drives the login prompt GE shows the user. Used only on the GE authorization resource (`serverSideOauth2`). |
| **WIF API App** (`WIF_PROVIDER_OIDC_CLIENT_ID`) | The audience the Workforce Identity Pool provider expects on the forwarded Entra access token. This is what Google STS validates against. |

These are separate Entra App Registrations. The GE OAuth client must request a scope
(`api://<WIF_APP_ID>/<scope-name>`) against the WIF API app so the token GE forwards has the right audience
for the STS exchange.

### Key files

| File | Responsibility |
|------|----------------|
| `agents/_base/token_exchange.py` | RFC 8693 STS call: Entra token → Workforce (WIF) access token |
| `agents/_base/user_context.py` | Extracts the forwarded token from the A2A request; builds user `Credentials` |
| `agents/_base/base_executor.py` | Captures the user token per request into a `ContextVar` |
| `tools/employee/bq_client.py` | `get_bigquery_client()` — user (OBO) creds, else ADC fallback |

If **no** user token is forwarded (authorization disabled, or a machine-to-machine
call) the tools transparently fall back to Application Default Credentials.

### Requirements for OBO to actually engage

1. **A Workforce Pool + OIDC provider** trusting your Entra tenant:
   ```env
   WORKFORCE_POOL_ID=azure-oidc-agentspace-dev-app
   WORKFORCE_PROVIDER_ID=azure-dev-oidc-provider
   WORKFORCE_POOL_LOCATION=global
   WIF_SUBJECT_TOKEN_TYPE=jwt
   ```
   Verify with:
   ```bash
   gcloud iam workforce-pools providers describe azure-dev-oidc-provider \
     --workforce-pool=azure-oidc-agentspace-dev-app --location=global
   ```
2. **An Entra authorization resource** so Gemini Enterprise forwards the token, requesting the WIF API
   app's custom scope (not Microsoft Graph scopes):
   ```env
   OAUTH_CLIENT_ID=<GE OAuth client ID>
   OAUTH_CLIENT_SECRET=<secret from Azure Portal for that app>
   OAUTH_AUTHORIZATION_URI=https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/authorize
   OAUTH_TOKEN_URI=https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token
   OAUTH_SCOPES="openid offline_access api://<WIF_APP_ID>/<scope-name>"
   ```
   ```bash
   python scripts/setup_agent_auth.py --id auth-emp-verify-v2
   ```
3. **IAM for the federated users** — the pool principals need BigQuery + Agent access plus
   `roles/serviceusage.serviceUsageConsumer` (required for STS `userProject` billing):
   ```bash
   python scripts/grant_permissions.py
   ```

### Validating OBO end-to-end (outside of GE)

```bash
python scripts/get_entra_token.py        # MSAL device-code login, prints an Entra token
python scripts/verify_wif.py <ENTRA_ACCESS_TOKEN>   # Entra -> STS -> BigQuery smoke test
```

`verify_wif.py` exercises the agent's own `token_exchange.py` code path, so what you validate there is
exactly what runs in production.

Once deployed, use `python scripts/debug_agent.py emp_verify_v2 --follow` and watch for
`BigQuery: using On-Behalf-Of user (federated) credentials` vs the ADC fallback line to confirm which
identity ran a query.

**Note on `.entra_token.tmp`**: `get_entra_token.py` writes the fetched token to this file for convenience
during manual testing. It is gitignored — never commit it, it is a live credential.

---

## Deploy CLI Reference

```bash
# Deploy a SINGLE agent
python scripts/deploy.py emp_verify_google_oauth_v3

# Deploy MULTIPLE specific agents
python scripts/deploy.py emp_verify_google_oauth_v3 emp_verify_v2

# Deploy ALL agents (reads every YAML in config/)
python scripts/deploy.py --all

# Dry run (shows config without deploying)
python scripts/deploy.py emp_verify_google_oauth_v3 --dry-run

# List available agents
python scripts/deploy.py --list

# Register an already-deployed Reasoning Engine (skip redeploy)
python scripts/deploy.py emp_verify_google_oauth_v3 --register-only --reasoning-engine <RESOURCE_ID>

# Undeploy an agent
python scripts/deploy.py emp_verify_google_oauth_v3 --undeploy
```

### Undeploy Script
```bash
# Undeploy a single agent
python scripts/undeploy.py emp_verify_google_oauth_v3

# Undeploy all agents
python scripts/undeploy.py --all

# List agents registered in Gemini Enterprise
python scripts/undeploy.py --list
```

## Adding a New Agent

### 1. Create the config YAML
```bash
# config/my_new_agent.yaml
```
```yaml
agent:
  name: "MyNewAgent"
  display_name: "My New Agent"
  description: "What this agent does."

  tools:
    - "tools.my_domain.my_tool.my_function"

  a2ui:
    examples_dir: "agents/my_new_agent/examples/0.8"

  prompts:
    role: "You are a helpful assistant that..."
    workflow: "Follow these steps..."
    ui: "Render A2UI components..."

  actions:
    my_button_action:
      template: "User clicked: {context_var}"

deploy:
  deployment_target: agent_engine   # or cloud_run
  ge_registration: a2a              # required for A2UI/iframe support in GE
  agent_authorization_id: "auth-my-new-agent"

  extra_requirements:
    - "some-extra-package>=1.0"
  extra_packages:
    - "agents"
    - "tools"
    - "config"
    # Do NOT add "../widgets" for Agent Engine — use BasicCatalog in agent.py instead
  env_vars:
    NUM_WORKERS: "1"
  skills:
    - id: "my-skill"
      name: "My Skill"
      description: "What this skill does."
      examples: ["Example query 1", "Example query 2"]
```

### 2. Create the agent folder
```bash
mkdir -p agents/my_new_agent/examples/0.8
```

**agents/my_new_agent/agent.py** — Copy from `agents/emp_verify_google_oauth_v3/agent.py` and change `AGENT_CONFIG_NAME`.

**agents/my_new_agent/executor.py** — Just 3 lines:
```python
from agents._base.base_executor import BaseA2UIExecutor

class MyNewAgentExecutor(BaseA2UIExecutor):
    AGENT_CONFIG_NAME = "my_new_agent"
```

### 3. Add tools (if needed)
```bash
mkdir -p tools/my_domain
```
Create tool functions, add to `tools/registry.py`, import in `tools/my_domain/__init__.py`.

### 4. Deploy
```bash
python scripts/deploy.py my_new_agent
```

## Tool Registry

The tool registry (`tools/registry.py`) provides metadata for management:
```python
from tools.registry import get_tools_by_tag, get_tool_metadata, list_all_tools

employee_tools = get_tools_by_tag("employee")
read_tools = get_tools_by_operation("READ")
meta = get_tool_metadata("lookup_employee")
all_tools = list_all_tools()
```

## Config System

- **`config/_defaults.yaml`** — Shared defaults inherited by all agents (model, region, base requirements)
- **`config/<agent_name>.yaml`** — Agent-specific config that overrides defaults
- Configs are deep-merged: agent values override defaults, nested dicts are merged recursively

## Key Design Decisions

| Aspect | How it works |
|--------|-------------|
| **Hosting** | Vertex AI Agent Engine (Reasoning Engine) — KPMG standard for GE agents |
| **GE registration** | Pure A2A (`a2aAgentDefinition`) — required for A2UI DataParts in GE |
| **OBO (v3)** | Google OAuth direct — GE forwards Google access token; `OBO_CREDENTIAL_MODE=google_direct` |
| **OBO (v2)** | Entra ID → Google STS → Workforce Identity Federation, per-request via `ContextVar` |
| **Auth resources (v3)** | Blue/green rotation in YAML — `deploy.py` alternates two authorization IDs per deploy |
| **Config** | YAML files in `config/`, merged with `_defaults.yaml` |
| **Tools vs Skills** | Tools = Python functions the LLM calls. Skills = metadata for A2A routing |
| **Executor** | Base class in `agents/_base/base_executor.py`, agents subclass with 3 lines |
| **Deploy** | Generic `scripts/deploy.py` reads config, imports executor dynamically |
| **A2UI** | Examples in `agents/employee_verification/examples/0.8/`; v3 uses `BasicCatalog` on Agent Engine |
| **KPMG Widgets** | Repo-root `widgets/` — local/dev only; not bundled for Agent Engine deploys |
