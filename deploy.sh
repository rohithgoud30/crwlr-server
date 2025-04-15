#!/bin/bash
set -e

# Simple deployment script for crwlr-server
# This script builds and deploys the application directly to Cloud Run

# Configuration
PROJECT_ID="crwlr-server"
SERVICE_NAME="crwlr-server"
REGION="us-east4"
SERVICE_ACCOUNT="662250507742-compute@developer.gserviceaccount.com"
GIT_SHA=$(git rev-parse --short HEAD)
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:${GIT_SHA}"

echo "🔧 Building and deploying ${SERVICE_NAME}..."
echo "Using Git SHA: ${GIT_SHA}"

# Build the Docker image
echo "🏗️ Building Docker image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

# Push the image to Google Container Registry
echo "⬆️ Pushing Docker image to GCR"
gcloud auth configure-docker --quiet
docker push "${IMAGE_NAME}"

# Deploy to Cloud Run
echo "🚀 Deploying to Cloud Run"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_NAME}" \
  --region "${REGION}" \
  --platform managed \
  --memory 2Gi \
  --cpu 1 \
  --timeout 3600s \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 100 \
  --allow-unauthenticated \
  --service-account "${SERVICE_ACCOUNT}" \
  --set-env-vars "PROJECT_ID=${PROJECT_ID}"

echo "✅ Deployment completed successfully!"
echo "🌐 Service URL: $(gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)')" 