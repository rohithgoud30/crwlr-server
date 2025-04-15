#!/bin/bash
set -e

# Configuration variables - customize these
PROJECT_ID="your-gcp-project-id"  # Change this to your GCP project ID
SERVICE_NAME="crwlr-api"
REGION="us-central1"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Make sure gcloud is configured with the correct project
gcloud config set project ${PROJECT_ID}

# Build the Docker image
echo "Building Docker image..."
docker build -t ${IMAGE_NAME} .

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
  --memory=2Gi \
  --cpu=2 \
  --max-instances=10 \
  --concurrency=80 \
  --timeout=5m \
  --set-env-vars="ENV=production"

echo "Deployment completed!"
echo "Your service URL:"
gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format='value(status.url)' 