#!/bin/bash
set -e

# Configuration variables - customize these
PROJECT_ID="crwlr-server"  # Updated with actual project ID
SERVICE_NAME="crwlr-api"
REGION="us-east4"  # Northern Virginia region
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Make sure gcloud is configured with the correct project
gcloud config set project ${PROJECT_ID}

# Build the Docker image with platform targeting to ensure compatibility
echo "Building Docker image..."
docker build --platform linux/amd64 -t ${IMAGE_NAME} .

# Push the image to Google Container Registry
echo "Pushing image to Google Container Registry..."
docker push ${IMAGE_NAME}

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
  --image=${IMAGE_NAME} \
  --platform=managed \
  --region=${REGION} \
  --allow-unauthenticated \
  --memory=4Gi \
  --cpu=2 \
  --max-instances=10 \
  --concurrency=50 \
  --timeout=10m \
  --set-env-vars="ENV=production" \
  --port=8000 \
  --cpu-throttling

echo "Deployment completed!"
echo "Your service URL:"
gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format='value(status.url)' 