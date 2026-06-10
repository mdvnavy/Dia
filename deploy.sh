#!/bin/bash
set -e

# Configuration variables
REGION=${REGION:-"us-central1"}
REPO_NAME=${REPO_NAME:-"client-discovery-repo"}
SERVICE_NAME=${SERVICE_NAME:-"client-discovery-agent-adk"}

# Get current GCP Project ID if not set
if [ -z "$PROJECT_ID" ]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi

# Trim whitespace
PROJECT_ID=$(echo "$PROJECT_ID" | xargs)

if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  echo "Error: PROJECT_ID is not set and could not be retrieved from gcloud config (it is empty or '(unset)')."
  echo "Please set your project ID in your environment:"
  echo "  export PROJECT_ID=your-project-id"
  echo "Or configure gcloud:"
  echo "  gcloud config set project your-project-id"
  exit 1
fi

IMAGE_TAG="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:latest"

echo "========================================================"
echo "Deploying ${SERVICE_NAME} to Google Cloud Run"
echo "Project ID:  ${PROJECT_ID}"
echo "Region:      ${REGION}"
echo "Repository:  ${REPO_NAME}"
echo "Image Tag:   ${IMAGE_TAG}"
echo "========================================================"

echo "Step 1: Enabling required GCP services..."
gcloud services enable --project "${PROJECT_ID}" \
  artifactregistry.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com

echo "Step 2: Checking if Artifact Registry repository exists..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" --location="${REGION}" --project "${PROJECT_ID}" &>/dev/null; then
  echo "Creating Artifact Registry repository '${REPO_NAME}'..."
  gcloud artifacts repositories create "${REPO_NAME}" \
    --project "${PROJECT_ID}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker repository for DIA web application"
else
  echo "Repository '${REPO_NAME}' already exists."
fi

echo "Step 3: Building and pushing Docker image using Cloud Build..."
gcloud builds submit --tag "${IMAGE_TAG}" --project "${PROJECT_ID}" .

echo "Step 4: Deploying to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
  --project "${PROJECT_ID}" \
  --image "${IMAGE_TAG}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated

echo "========================================================"
echo "Deployment completed successfully!"
echo "Service URL: $(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --project "${PROJECT_ID}" --format='value(status.url)')"
echo "========================================================"
