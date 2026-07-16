# bqmcp-adk-entra-wif

ADK agent on Gemini Enterprise that calls the managed BigQuery MCP server as the
end user. End-user identity reaches BigQuery via Entra → WIF token exchange —
no synced Google identity; BigQuery sees the workforce subject directly.

Ships under **two deployment targets** wrapping the same `app.agent`:
**Vertex AI Agent Runtime** (ADK protocol; `app/agent_runtime_app.py`) and
**Cloud Run** (A2A protocol; `app/fast_api_app.py`). Only how GE delivers the
Entra JWT to the agent process differs — the token-exchange path is identical.

---

## Token transformation

```
Entra JWT
  iss = login.microsoftonline.com/<TID>/v2.0
  aud = api://<APP_ID>
  oid = <entra-user-oid>
     │
     │  POST sts.googleapis.com/v1/token
     │  grant_type = token-exchange
     │  audience   = //iam.googleapis.com/.../workforcePools/<POOL>/providers/<PROV>
     │  options    = {"userProject": <user_project>}
     ▼
Google access token
  principal = principal://iam.googleapis.com/.../workforcePools/<POOL>/subject/<email>
     │
     │  POST bigquery.googleapis.com/mcp
     │  Authorization: Bearer <google_token>
     │  X-Goog-User-Project: <user_project>
     ▼
BQ enforces IAM on the workforce subject
```

### How GE delivers the Entra JWT

GE does not propagate the workforce identity to the agent — the endpoint is
always invoked by GE's own service agent. The end-user identity arrives only
as the Entra access token GE obtains via the linked `serverSideOauth2`
authorization resource.

- **Agent Runtime (ADK):** GE writes the token directly into ADK session state.
- **Cloud Run (A2A):** GE puts it in `Authorization: Bearer <jwt>` on the
  A2A POST. `EntraAuthMiddleware` + `_AuthInjectingSessionService` in
  `app/fast_api_app.py` move it into session state. Cloud Run's own
  IAM-invoker token rides on `X-Serverless-Authorization` and is ignored.

`entra_wif_header_provider` then locates the JWT by issuer match
(`login.microsoftonline.com` / `sts.windows.net`) — the slot-key name GE picks
is undocumented and irrelevant.

### Caching

Google access tokens cached per Entra `oid` (falls back to `sub`, then raw
token) for the pool session TTL (~1 h), refreshed 60 s before expiry.
Concurrent sessions for the same user share one STS exchange.

---

## Configuration

### Entra app registration

- Redirect URI (Web): `https://vertexaisearch.cloud.google.com/oauth-redirect`
- `accessTokenAcceptedVersion: 2` in the manifest (v1 also works if the WIF
  provider's `issuer_uri` points at `sts.windows.net/<TID>/` instead).
- Expose an API with delegated scope `access_as_user` on Application ID URI
  `api://<APP_ID>`. GE requests `api://<APP_ID>/access_as_user`, so the
  resulting `aud` is `api://<APP_ID>`.

### WIF provider (OIDC, org-level workforce pool)

- `issuer_uri = https://login.microsoftonline.com/<TID>/v2.0`
- `allowed_audiences` includes `api://<APP_ID>` — must match the `aud` above.
- Attribute mapping: `google.subject = assertion.email.lowerAscii()`
  (and `attribute.oid = assertion.oid` for oid-scoped grants).
- STS audience parameter:
  `//iam.googleapis.com/locations/global/workforcePools/<POOL>/providers/<PROVIDER>`.

### IAM on the user project (granted to the workforce principal / principalSet)

- **`roles/serviceusage.serviceUsageConsumer`** — required by
  `X-Goog-User-Project`. Without it `initialize`/`list_tools` succeed but the
  first tool call returns 403. `serviceUsageViewer` is not sufficient.
- `roles/mcp.toolUser`, `roles/bigquery.jobUser`, `roles/bigquery.dataViewer`
  (tighten the BQ grants for production).

APIs on the user project: `bigquery`, `aiplatform`, `iam`, `sts`,
`serviceusage`. The runtime service account needs no BQ access — calls are
made with the user's federated token.

### Gemini Enterprise

`serverSideOauth2` authorization resource pointed at
`login.microsoftonline.com/<TID>/oauth2/v2.0/{authorize,token}`, scope
`openid offline_access api://<APP_ID>/access_as_user`. Link it to the agent.
The resource ID is used only at GE registration (`--authorization-id`); it
never enters the agent process.

### Region pinning

GE app, authorization resource, and Agent Runtime engine must be in compatible
regions (GE: `global` / `us` / `eu`). The runtime force-sets
`GOOGLE_CLOUD_LOCATION` to the engine's deploy region, which may not be where
the chosen Gemini model is published — set `GEMINI_LOCATION` (e.g. `global`,
`eu`) to redirect model calls without moving the engine.

---

## Using the factory

All the auth machinery lives in `app/entra_wif.py`:

```python
from app.entra_wif import entra_wif_header_provider

bq_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(url="https://bigquery.googleapis.com/mcp"),
    header_provider=entra_wif_header_provider(
        wif_provider="//iam.googleapis.com/locations/global/workforcePools/<POOL>/providers/<PROVIDER>",
        user_project="<USER_PROJECT>",
    ),
)
```

Raises `MissingEntraTokenError` (no JWT in state) and `TokenExchangeError`
(STS non-200, body included).

---

## Deploy

### Target A — Agent Runtime (ADK)

```bash
agents-cli deploy --project <GCP_PROJECT> --region <ENGINE_REGION>
```

Then in GE: register as ADK agent, link the authorization resource.

### Target B — Cloud Run (A2A)

Terraform under `deployment/terraform/cloud_run/single-project/` provisions
the app service account, GCS logs bucket, Artifact Registry repo, and a
placeholder Cloud Run v2 service with `session_affinity = true` (needed
because sessions are in-memory).

```bash
bash scripts/deploy_cloud_run.sh
```

The script auto-sources `./.env`. Required: `PROJECT_ID`,
`WIF_PROVIDER_RESOURCE` (full `//iam.googleapis.com/...` resource name).

Env vars set on the service:

| Var | Purpose |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | User-billing project for STS and BQ calls |
| `WIF_PROVIDER_RESOURCE` | STS audience for the token exchange |
| `LOGS_BUCKET_NAME` | GCS bucket for artifacts / telemetry |
| `APP_URL` | Public Cloud Run URL — baked into the agent card |
| `AGENT_VERSION` | Surfaced in the agent card |

Build & deploy are decoupled from GE registration — the agent process never
reads the GE auth-resource ID. Register afterwards:

```bash
agents-cli publish gemini-enterprise \
  --registration-type a2a \
  --agent-card-url <CLOUD-RUN-URL>/a2a/app/.well-known/agent-card.json \
  --authorization-id <auth-id> \
  --gemini-enterprise-app-id <ge-app-id>
```

Wiring Agent Platform Sessions into Target B is a tracked follow-up; until
then sessions are in-memory + affinity-pinned.

---

## Gotchas

- **`TokenExchangeError` from STS** — WIF provider mismatch.
  `allowed_audiences` ≠ Entra `aud`, or `issuer_uri` ≠ `iss` (v1 vs v2
  endpoint confusion). Decode the JWT and compare.
- **`MissingEntraTokenError`** — authorization resource isn't linked to the
  agent, or GE app and auth resource are in different regions.
- **403 only on the first BQ tool call** —
  `roles/serviceusage.serviceUsageConsumer` missing on `user_project`.
  `initialize`/`list_tools` don't trigger `X-Goog-User-Project` checks.
- **GE registration fails to invoke the Cloud Run service** — the Terraform
  does *not* grant `roles/run.invoker` to GE's service agent
  (`service-<PROJECT-NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com`).
  Grant it on the service after deploy, or the A2A registration probe 403s.
- **Cloud Run instance churn losing state** — sessions are in-memory; if
  `session_affinity = true` gets toggled off, multi-turn conversations
  fragment across instances.

---

## Local development

`agents-cli playground` iterates on agent logic against the Agent Runtime
entrypoint. For A2A, run uvicorn directly:

```bash
uv run uvicorn app.fast_api_app:app --port 8000
curl http://localhost:8000/a2a/app/.well-known/agent-card.json
```

Either way the BQ tool raises `MissingEntraTokenError` — no GE-injected JWT
in state. End-to-end validation requires deploying and invoking through GE.

`uv run pytest tests/unit` runs the offline suite (entra_wif logic, A2A auth
shim, agent wiring). `tests/integration` needs ADC and hits live services.
