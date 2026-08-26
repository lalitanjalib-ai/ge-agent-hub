# Employee Verification Agent — Microsoft Entra ID 3P OAuth + WIF

An A2A agent that helps employees look up, update, and verify their
employment records in BigQuery. This agent runs on Vertex AI Agent Engine's
**Bring-Your-Own-Dockerfile (BYOC)** deployment mode behind the **Agent
Engine V2 ingress**, and is registered with Gemini Enterprise using a
**Microsoft Entra ID** authorization resource — end users authenticate with
their corporate Entra ID account, and their Entra JWT is exchanged for a
Google Cloud access token via **Workforce Identity Federation (RFC 8693
STS)** before any BigQuery call.

## How the token flows

1. Gemini Enterprise runs the Entra ID OAuth consent flow using the
   `Authorization` resource created by `scripts/setup_agent_auth.py` /
   `scripts/deploy.py`.
2. When a user invokes the agent, GE calls the Agent Engine **V2 ingress
   URL** (`.../reasoningEngines/{id}/api/a2a/...`). GE mints a Discovery
   Engine P4SA token on `Authorization` (to satisfy Google edge auth) and
   places the end user's Entra JWT on `X-Goog-Agent-User-Authorization`.
3. Agent Engine's V2 ingress gateway rewrites
   `X-Goog-Agent-User-Authorization` onto the standard
   `Authorization: Bearer <Entra_JWT>` header before the request reaches
   this container.
4. `main.py`'s `TokenExtractorMiddleware` captures that header into a
   per-request `ContextVar` (`employee_agent.token_context`).
5. `employee_agent/tools/bq_client.py` exchanges the Entra JWT for a
   short-lived Google Cloud access token via
   `employee_agent.entra_wif.exchange_entra_token_for_google_token`
   (Workforce Identity Federation / RFC 8693 STS), then uses that token to
   build the BigQuery client — so queries run, and are audit-logged, as the
   actual end user.

## Prerequisites

1. A **Gemini Enterprise app** must already exist — note its app ID.
2. A **Microsoft Entra ID app registration** with:
   - Redirect URI: `https://vertexaisearch.cloud.google.com/static/oauth/oauth.html`
   - An exposed API scope, e.g. `api://{ENTRA_APP_ID}/access_as_user`
3. A **Workforce Identity Federation pool + provider** configured to trust
   your Entra tenant as an OIDC issuer, with IAM bindings granting the
   workforce pool's `principalSet` `roles/bigquery.dataViewer`,
   `roles/bigquery.jobUser`, and `roles/serviceusage.serviceUsageConsumer`.

## Setup

```bash
cp .env.example .env
# fill in PROJECT_ID, LOCATION, STORAGE_BUCKET, GEMINI_ENTERPRISE_APP_ID,
# ENTRA_TENANT_ID, OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, ENTRA_APP_ID,
# WIF_PROVIDER_RESOURCE

pip install -r requirements.txt

python adhoc/setup_employee_bq.py     # creates + seeds the BigQuery table
python scripts/grant_permissions.py   # grants IAM to service accounts
```

## Deploy

```bash
python scripts/setup_agent_auth.py                  # create the Entra authorization resource
python scripts/deploy.py                             # first deploy or in-place redeploy
python scripts/deploy.py --force-recreate-engine     # recreate the Agent Engine resource
python scripts/deploy.py --dry-run                   # preview only
python scripts/deploy.py --undeploy                  # unregister from GE
python scripts/deploy.py --undeploy --delete-engine  # also delete the Reasoning Engine
```

First-time deploy builds a container from the local `Dockerfile` +
`requirements.txt` (BYOC mode, ~5-10 minutes) and registers it with Gemini
Enterprise at the V2 ingress URL with the Entra ID authorization resource
attached, and sets gallery visibility to `ALL_USERS`.

## Local development

```bash
uvicorn main:app --port 8080
curl http://localhost:8080/healthz
curl http://localhost:8080/api/a2a/.well-known/agent-card.json
```

Locally (without going through Gemini Enterprise), no `Authorization` header
will be present, so the BigQuery tools fall back to Application Default
Credentials — this is expected and logged.

## Testing end-to-end

1. Open the Gemini Enterprise app, select "Employee Verification Agent",
   complete the Entra ID consent prompt when asked, and ask something like
   "Lookup employee John Smith".
2. Check Cloud Logging for the Reasoning Engine — look for:
   - `OBO: propagated Entra Authorization header captured on this request.`
   - `OBO: Entra token exchanged via WIF/STS — BigQuery calls will run as
     the end user.`
   If instead you see `OBO: no Authorization header on this request.`, the
   token was not propagated — check that the agent is registered at the V2
   `/api/...` URL and that the GE authorization resource is the Entra ID
   resource created by `scripts/setup_agent_auth.py`.

## IAM checklist

```bash
PROJECT_NUM=$(gcloud projects describe prj-us-bpg-agentspace-dev-b --format='value(projectNumber)')
gcloud projects add-iam-policy-binding prj-us-bpg-agentspace-dev-b \
  --member="serviceAccount:service-${PROJECT_NUM}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

Also ensure the workforce pool `principalSet` has `roles/bigquery.dataViewer`,
`roles/bigquery.jobUser`, and `roles/serviceusage.serviceUsageConsumer`.

## Project layout

```
employ_verification/
├── main.py                      # BYOC A2A server (TokenExtractorMiddleware, mounted under /api)
├── Dockerfile
├── requirements.txt
├── employee_agent/
│   ├── agent.py                 # ADK Agent + instruction + tools
│   ├── token_context.py         # ContextVar holding the propagated Entra JWT
│   ├── entra_wif.py             # Entra JWT -> Google token exchange (RFC 8693 STS / WIF)
│   └── tools/
│       ├── bq_client.py         # BigQuery client built from the exchanged Google token
│       ├── lookup_employee.py
│       ├── update_employee_field.py
│       └── verify_employee.py
├── scripts/
│   ├── deploy.py                # BYOC create/update + GE registration at V2 ingress URL
│   ├── setup_agent_auth.py      # creates the Entra ID GE authorization resource
│   └── grant_permissions.py
└── adhoc/
    └── setup_employee_bq.py     # seeds the BigQuery table
```
