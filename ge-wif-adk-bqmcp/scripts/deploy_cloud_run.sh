#!/usr/bin/env bash
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Deploy the A2A FastAPI app to Cloud Run.
#
# Assumes Terraform under deployment/terraform/cloud_run/single-project has
# already provisioned: APIs, the app service account, the GCS logs bucket,
# and the initial Cloud Run service (hello-world placeholder image).
#
# This script builds the container with Cloud Build, pushes to Artifact
# Registry, and updates the Cloud Run service with the new image plus the
# required env vars.
#
# Required env (or pass via flags; auto-sourced from ./.env at repo root):
#   PROJECT_ID             GCP project id
#   REGION                 Cloud Run region (default: europe-west1)
#   SERVICE_NAME           Cloud Run service name (default: bqmcp-a2a)
#   WIF_PROVIDER_RESOURCE  Full workforce-pool provider resource name, e.g.
#                          //iam.googleapis.com/locations/global/workforcePools/<pool>/providers/<provider>
#
# Optional:
#   IMAGE_TAG              Defaults to current git SHA (or "latest" if no git)
#   LOGS_BUCKET_NAME       Override; defaults to ${PROJECT_ID}-${SERVICE_NAME}-logs
#                          (matches storage.tf naming)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env"
  set +a
fi

PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:-europe-west1}"
SERVICE_NAME="${SERVICE_NAME:-bqmcp-a2a}"
WIF_PROVIDER_RESOURCE="${WIF_PROVIDER_RESOURCE:?WIF_PROVIDER_RESOURCE is required (full //iam.googleapis.com/... resource name)}"
LOGS_BUCKET_NAME="${LOGS_BUCKET_NAME:-${PROJECT_ID}-${SERVICE_NAME}-logs}"

if command -v git >/dev/null 2>&1 && git -C "${REPO_ROOT}" rev-parse --short HEAD >/dev/null 2>&1; then
  IMAGE_TAG="${IMAGE_TAG:-$(git -C "${REPO_ROOT}" rev-parse --short HEAD)}"
else
  IMAGE_TAG="${IMAGE_TAG:-latest}"
fi

AGENT_VERSION="${AGENT_VERSION:-0.1.0}"
IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE_NAME}:${IMAGE_TAG}"

# Resolve the Cloud Run service URL so APP_URL embedded in the agent card
# matches the actual public URL. If the service doesn't exist yet, fall back
# to the deterministic project-number pattern (Terraform also uses this).
if [[ -z "${APP_URL:-}" ]]; then
  APP_URL="$(gcloud run services describe "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"
  if [[ -z "${APP_URL}" ]]; then
    PROJECT_NUMBER="${PROJECT_NUMBER:-$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')}"
    APP_URL="https://${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"
  fi
fi

cd "${REPO_ROOT}"

echo "==> Building image ${IMAGE_URI}"
gcloud builds submit \
  --project="${PROJECT_ID}" \
  --tag="${IMAGE_URI}" \
  .

echo "==> Deploying Cloud Run service ${SERVICE_NAME} in ${REGION}"
gcloud run deploy "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE_URI}" \
  --port=8080 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},WIF_PROVIDER_RESOURCE=${WIF_PROVIDER_RESOURCE},LOGS_BUCKET_NAME=${LOGS_BUCKET_NAME},AGENT_VERSION=${AGENT_VERSION},APP_URL=${APP_URL},OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT"

URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

echo
echo "Service URL:       ${URL}"
echo "Agent card URL:    ${URL}/a2a/app/.well-known/agent-card.json"
echo "A2A RPC endpoint:  ${URL}/a2a/app"
echo
echo "Next step (GE registration):"
echo "  agents-cli publish gemini-enterprise \\"
echo "    --registration-type a2a \\"
echo "    --agent-card-url ${URL}/a2a/app/.well-known/agent-card.json \\"
echo "    --authorization-id <YOUR-GE-AUTH-RESOURCE-ID> \\"
echo "    --gemini-enterprise-app-id <YOUR-GE-APP-ID>"
