#!/bin/bash
set -e

# Load environment variables from .env file if it exists
if [ -f .env ]; then
  echo "Loading environment variables from .env file..."
  source .env
fi

# Set default values if not set in environment
PROJECT_ID=${PROJECT_ID:-crwlr-server}
REGION=${REGION:-us-east4}
SERVICE_NAME=${SERVICE_NAME:-crwlr-server}
API_KEY=${API_KEY:-"default-api-key"}
ENV=${ENV:-production}

echo "Building emergency minimal container..."

# Create a directory just for emergency deployment
mkdir -p emergency
cp run.py emergency/
mkdir -p emergency/app
cp app/main.py emergency/app/
cp Dockerfile.emergency emergency/Dockerfile

# Build and push a minimal container
echo "Building and pushing emergency container..."
cd emergency
gcloud builds submit --tag us-east4-docker.pkg.dev/$PROJECT_ID/crwlr-repo/crwlr-server:emergency

echo "Deploying emergency container..."
gcloud run deploy $SERVICE_NAME \
  --image us-east4-docker.pkg.dev/$PROJECT_ID/crwlr-repo/crwlr-server:emergency \
  --region $REGION \
  --platform managed \
  --memory 512Mi \
  --cpu 1 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 10 \
  --allow-unauthenticated \
  --service-account github-actions-deployer@crwlr-server.iam.gserviceaccount.com \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,ENVIRONMENT=$ENV,API_KEY=$API_KEY" \
  --port 8080

cd ..
echo "Emergency deployment completed!"
echo "The API should be available at: https://crwlr-server-$REGION.a.run.app/api/v1/status"
