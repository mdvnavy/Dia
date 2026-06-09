#!/usr/bin/env bash
# Deploy DIA to Google Cloud Run from source.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - A Google Cloud project with billing enabled
#   - A Gemini API key (https://aistudio.google.com/app/apikey)
#
# Usage:
#   PROJECT_ID=my-project GEMINI_API_KEY=xxxx ./deploy-cloudrun.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}"
SERVICE="${SERVICE:-dia-discovery-intake}"
REGION="${REGION:-us-central1}"
GEMINI_API_KEY="${GEMINI_API_KEY:?Set GEMINI_API_KEY for live Gemini agent runs}"

echo "Enabling required Google Cloud services..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  --project "${PROJECT_ID}"

echo "Deploying ${SERVICE} to Cloud Run in ${REGION}..."
gcloud run deploy "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --source . \
  --allow-unauthenticated \
  --set-env-vars "GEMINI_API_KEY=${GEMINI_API_KEY}"

echo "Done. Service URL:"
gcloud run services describe "${SERVICE}" \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --format 'value(status.url)'
