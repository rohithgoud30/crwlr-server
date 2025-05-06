#!/bin/bash

# CRWLR Server - Cloud Run Deployment Script
echo "======================================================"
echo "  CRWLR Server - Cloud Run Deployment"
echo "======================================================"

# Load environment variables
source .env

# Ensure required variables are set
if [ -z "$PROJECT_ID" ]; then
  echo "Error: PROJECT_ID is not set in .env file"
  exit 1
fi

if [ -z "$FIREBASE_PROJECT_ID" ]; then
  echo "Error: FIREBASE_PROJECT_ID is not set in .env file"
  exit 1
fi

echo "Deploying to Cloud Run in project: $PROJECT_ID"
echo "Using Firebase project: $FIREBASE_PROJECT_ID"

# Deploy to Cloud Run
gcloud run deploy crwlr-server \
  --source . \
  --platform managed \
  --region us-east4 \
  --memory 2Gi \
  --cpu 1 \
  --timeout 3600s \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 100 \
  --allow-unauthenticated \
  --set-env-vars="PROJECT_ID=$PROJECT_ID" \
  --set-env-vars="GEMINI_API_KEY=$GEMINI_API_KEY" \
  --set-env-vars="API_KEY=$API_KEY" \
  --set-env-vars="ENVIRONMENT=production" \
  --set-env-vars="FIREBASE_PROJECT_ID=$FIREBASE_PROJECT_ID" \
  --set-env-vars="FIREBASE_CLIENT_EMAIL=$FIREBASE_CLIENT_EMAIL" \
  --set-env-vars="FIREBASE_PRIVATE_KEY=$FIREBASE_PRIVATE_KEY"

echo "======================================================"
echo "  Deployment completed"
echo "======================================================" 