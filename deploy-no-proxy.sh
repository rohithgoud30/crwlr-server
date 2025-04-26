#!/bin/bash
set -e

# Deployment script for CRWLR API server to Cloud Run without using SQL proxy
# This script explicitly sets NO_PROXY=true to enable direct connections
# Note: This project requires Python 3.11 for better compatibility with cloud-sql-python-connector

echo "→ Deploying to Cloud Run without using Cloud SQL Proxy"
echo "  This uses direct connections to the database with NO_PROXY=true"

# Load environment variables from .env file
if [ -f .env ]; then
  echo "→ Loading API keys from .env file..."
  # Use grep to extract the API keys from .env file
  API_KEY=$(grep -o 'API_KEY=.*' .env | cut -d '=' -f2)
  GEMINI_API_KEY=$(grep -o 'GEMINI_API_KEY=.*' .env | cut -d '=' -f2)
  
  # Get database settings
  DB_USER=$(grep -o 'DB_USER=.*' .env | cut -d '=' -f2)
  DB_PASS=$(grep -o 'DB_PASS=.*' .env | cut -d '=' -f2)
  DB_NAME=$(grep -o 'DB_NAME=.*' .env | cut -d '=' -f2)
  DB_HOST=$(grep -o 'DB_HOST=.*' .env | cut -d '=' -f2)
  DB_PORT=$(grep -o 'DB_PORT=.*' .env | cut -d '=' -f2 || echo "5432")
else
  echo "Error: .env file not found."
  echo "Please create a .env file with API_KEY, GEMINI_API_KEY, and database settings."
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

# Check if database settings are loaded
if [ -z "$DB_USER" ] || [ -z "$DB_PASS" ] || [ -z "$DB_NAME" ] || [ -z "$DB_HOST" ]; then
  echo "Error: Database settings missing in .env file."
  echo "Please make sure your .env file contains:"
  echo "DB_USER=your_db_user"
  echo "DB_PASS=your_db_password"
  echo "DB_NAME=your_db_name"
  echo "DB_HOST=your_db_host"
  echo "DB_PORT=5432 (optional, defaults to 5432)"
  exit 1
fi

# Get current branch name
BRANCH_NAME=$(git branch --show-current)
if [ -z "$BRANCH_NAME" ]; then
  BRANCH_NAME="main"  # Default to main if git command fails
fi

# Use a different service name for the no-proxy test deployment
# This prevents conflicts with the main deployment
SERVICE_NAME="crwlr-api-no-proxy"
PROJECT_ID="crwlr-server"  # Replace with your actual project ID
REGION="us-east4"  # Northern Virginia region
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

# Make sure gcloud is configured with the correct project
echo "→ Setting Google Cloud project to ${PROJECT_ID}..."
gcloud config set project ${PROJECT_ID}

# Build the Docker image with platform targeting to ensure compatibility
echo "→ Building Docker image..."
docker build --platform linux/amd64 -t ${IMAGE_NAME} .

# Push the image to Google Container Registry
echo "→ Pushing image to Google Container Registry..."
docker push ${IMAGE_NAME}

# Deploy to Cloud Run with NO_PROXY=true
echo "→ Deploying to Cloud Run with NO_PROXY=true..."
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
  --set-env-vars="ENV=production,PROJECT_ID=${PROJECT_ID},BRANCH_NAME=${BRANCH_NAME},GEMINI_API_KEY=${GEMINI_API_KEY},NO_PROXY=true,DB_USER=${DB_USER},DB_PASS=${DB_PASS},DB_NAME=${DB_NAME},DB_HOST=${DB_HOST},DB_PORT=${DB_PORT}" \
  --port=8000 \
  --cpu-throttling

# Set the API key separately for better security
echo "→ Setting API key..."
gcloud run services update ${SERVICE_NAME} \
  --platform=managed \
  --region=${REGION} \
  --set-env-vars="API_KEY=${API_KEY}"

echo "→ Deployment completed!"
echo "→ Your service URL:"
gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format='value(status.url)'

echo ""
echo "Test URL: https://$(gcloud run services describe ${SERVICE_NAME} --platform managed --region ${REGION} --format='value(status.url)')/docs" 