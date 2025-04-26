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

if [ -z "$INSTANCE_CONNECTION_NAME" ]; then
  echo "Error: INSTANCE_CONNECTION_NAME is not set in .env file"
  exit 1
fi

echo "Deploying to Cloud Run in project: $PROJECT_ID"
echo "Using Cloud SQL instance: $INSTANCE_CONNECTION_NAME"

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
  --set-env-vars="DB_USER=$DB_USER" \
  --set-env-vars="DB_PASS=$DB_PASS" \
  --set-env-vars="DB_NAME=$DB_NAME" \
  --set-env-vars="INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME" \
  --set-env-vars="ENVIRONMENT=production" \
  --set-env-vars="USE_CLOUD_SQL_PROXY=false" \
  --set-env-vars="NO_PROXY=false" \
  --set-secrets="DB_PASS=DB_PASSWORD:latest" \
  --add-cloudsql-instances="$INSTANCE_CONNECTION_NAME"

echo "======================================================"
echo "  Deployment completed"
echo "======================================================" 