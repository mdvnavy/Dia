#!/usr/bin/env bash
# Deploy DIA to Google Cloud Run with the Gemini key stored in Secret Manager.
#
# The API key is NEVER passed as a plaintext env var (which would be visible in
# the Cloud Run config and `gcloud describe` output). It is stored once in
# Secret Manager and mounted at runtime with --set-secrets.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A Google Cloud project with billing enabled
#   - A Gemini API key (https://aistudio.google.com/app/apikey)
#
# Usage:
#   PROJECT_ID=my-project GEMINI_API_KEY=xxxx ./deploy-cloudrun.sh
#   PROJECT_ID=my-project GEMINI_API_KEY=xxxx \
#     GCP_MCP_URL=https://monitoring.googleapis.com/mcp ./deploy-cloudrun.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}"
SERVICE="${SERVICE:-dia-discovery-intake}"
REGION="${REGION:-us-central1}"
SECRET_NAME="${SECRET_NAME:-gemini-api-key}"
GEMINI_API_KEY="${GEMINI_API_KEY:?Set GEMINI_API_KEY for live Gemini agent runs}"

DEPLOY_ENV_ARGS=(--set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION}")
if [[ -n "${GCP_MCP_URL:-}" ]]; then
  DEPLOY_ENV_ARGS=(--set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},GCP_MCP_URL=${GCP_MCP_URL}")
fi

echo "Enabling required Google Cloud services..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com --project "${PROJECT_ID}"

echo "Storing the API key in Secret Manager (${SECRET_NAME})..."
if gcloud secrets describe "${SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  printf '%s' "${GEMINI_API_KEY}" | gcloud secrets versions add "${SECRET_NAME}" \
    --project "${PROJECT_ID}" --data-file=-
else
  printf '%s' "${GEMINI_API_KEY}" | gcloud secrets create "${SECRET_NAME}" \
    --project "${PROJECT_ID}" --replication-policy=automatic --data-file=-
fi

# Grant the Cloud Run runtime service account read access to the secret.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "Granting ${RUNTIME_SA} access to the secret..."
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
  --project "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/secretmanager.secretAccessor >/dev/null

echo "Deploying ${SERVICE} to Cloud Run in ${REGION}..."
gcloud run deploy "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source . \
  --allow-unauthenticated \
  --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:latest" \
  "${DEPLOY_ENV_ARGS[@]}"

echo "Done. Service URL:"
gcloud run services describe "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format 'value(status.url)'
