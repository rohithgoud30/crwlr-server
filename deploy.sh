#!/bin/bash

# Exit on any error
set -e

# Deployment configuration
PROJECT_ID=$(gcloud config get-value project)
SERVICE_NAME="crwlr-server"
REGION="us-east4"
API_KEY="6e878bf1-c92d-4ba1-99c9-50e3343efd5d"
GEMINI_API_KEY="AIzaSyCcf-yVRysl-JnwdaEwb3I-JaYzNF4M36g"
IMAGE_NAME="gcr.io/$PROJECT_ID/$SERVICE_NAME:latest"

# Build the Docker image
echo "Building Docker image..."
docker build -t $IMAGE_NAME .

# Push the image to Container Registry
echo "Pushing image to Container Registry..."
docker push $IMAGE_NAME

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE_NAME \
  --platform managed \
  --region $REGION \
  --memory 2Gi \
  --cpu 1 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 100 \
  --timeout 3600s \
  --allow-unauthenticated \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,GEMINI_API_KEY=$GEMINI_API_KEY"

# Set the API key separately for better security
echo "Setting API key..."
gcloud run services update $SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --set-env-vars "API_KEY=$API_KEY"

echo "Deployment complete!"
echo "Service URL: $(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')" 