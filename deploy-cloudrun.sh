#!/usr/bin/env bash
# Deploy DIA to Google Cloud Run. Gemini runs on Vertex AI only.
#
# House rule: Google model calls go through Vertex AI with the service's own
# identity (Application Default Credentials), never the API-key developer
# endpoint. No Gemini API key is stored, mounted or read: the app refuses an
# API-key client even if one were present (see vertex_config.py).
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A Google Cloud project with billing enabled and the Vertex AI API on
#   - The Cloud Run runtime service account can call Vertex AI
#     (roles/aiplatform.user). This script does NOT grant IAM.
#
# Usage:
#   PROJECT_ID=my-project ./deploy-cloudrun.sh
#   PROJECT_ID=my-project VERTEX_LOCATION=us-central1 \
#     GCP_MCP_URL=https://monitoring.googleapis.com/mcp ./deploy-cloudrun.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}"
SERVICE="${SERVICE:-dia-discovery-intake}"
REGION="${REGION:-us-central1}"
# Vertex AI location for model calls (may differ from the Cloud Run region).
VERTEX_LOCATION="${VERTEX_LOCATION:-${REGION}}"

# --update-env-vars merges rather than replaces, so env already on the service, e.g.
# OWNER_EMAILS or ASK_DIA_ENABLED, is kept rather than wiped.
DEPLOY_ENV_ARGS=(
  --update-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION}"
)
if [[ -n "${GCP_MCP_URL:-}" ]]; then
  DEPLOY_ENV_ARGS+=(--update-env-vars "GCP_MCP_URL=${GCP_MCP_URL}")
fi

echo "Enabling required Google Cloud services..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  aiplatform.googleapis.com --project "${PROJECT_ID}"

echo "Deploying ${SERVICE} to Cloud Run in ${REGION} (Vertex AI in ${VERTEX_LOCATION})..."
gcloud run deploy "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source . \
  --allow-unauthenticated \
  # Unbind the old API-key secret if a previous deploy mounted it. The secret
  # itself is left in Secret Manager; deleting it is a separate, manual step.
  --remove-secrets GEMINI_API_KEY \
  "${DEPLOY_ENV_ARGS[@]}"

echo "Done. Service URL:"
gcloud run services describe "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format 'value(status.url)'
