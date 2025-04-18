#!/bin/bash

# Exit on any error
set -e

# Load environment variables from .env file
if [ -f .env ]; then
  echo "Loading API keys from .env file..."
  # Use grep to extract the API keys from .env file
  API_KEY=$(grep -o 'API_KEY=.*' .env | cut -d '=' -f2)
  GEMINI_API_KEY=$(grep -o 'GEMINI_API_KEY=.*' .env | cut -d '=' -f2)
else
  echo "Error: .env file not found."
  echo "Please create a .env file with API_KEY and GEMINI_API_KEY."
  exit 1
fi

# Check if the variables were loaded properly
if [ -z "$API_KEY" ] || [ -z "$GEMINI_API_KEY" ]; then
  echo "Error: API_KEY and/or GEMINI_API_KEY not found in .env file."
  echo "Please make sure your .env file contains:"
  echo "API_KEY=your_api_key_here"
  echo "GEMINI_API_KEY=your_gemini_api_key_here"
  exit 1
fi

# Get current branch name
BRANCH_NAME=$(git branch --show-current)
if [ -z "$BRANCH_NAME" ]; then
  BRANCH_NAME="main"  # Default to main if git command fails
fi

# Deployment configuration
PROJECT_ID=$(gcloud config get-value project)
SERVICE_NAME="crwlr-server"
REGION="us-east4"
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
  --set-env-vars "PROJECT_ID=$PROJECT_ID,GEMINI_API_KEY=$GEMINI_API_KEY,BRANCH_NAME=$BRANCH_NAME,ENVIRONMENT=production"

# Set the API key separately for better security
echo "Setting API key..."
gcloud run services update $SERVICE_NAME \
  --platform managed \
  --region $REGION \
  --set-env-vars "API_KEY=$API_KEY"

echo "Deployment complete!"
echo "Service URL: $(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format 'value(status.url)')" 