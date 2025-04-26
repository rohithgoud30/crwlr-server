#!/bin/bash
set -e

# Use environment variables with defaults
PROJECT_ID=${PROJECT_ID:-crwlr-server}
REGION=${REGION:-us-east4}
SERVICE_NAME=${SERVICE_NAME:-crwlr-server}
MEMORY=${MEMORY:-512Mi}
CPU=${CPU:-1}
CONCURRENCY=${CONCURRENCY:-80}
MIN_INSTANCES=${MIN_INSTANCES:-0}
MAX_INSTANCES=${MAX_INSTANCES:-10}
API_KEY=${API_KEY:-6e878bf1-c92d-4ba1-99c9-50e3343efd5d}
ENVIRONMENT=${ENVIRONMENT:-emergency}
SERVICE_ACCOUNT=${SERVICE_ACCOUNT:-github-actions-deployer@crwlr-server.iam.gserviceaccount.com}
REPO=${REPO:-crwlr-repo}
IMAGE_TAG=${IMAGE_TAG:-emergency}
PORT=${PORT:-8080}

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
gcloud builds submit --tag us-east4-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE_NAME:$IMAGE_TAG

echo "Deploying emergency container..."
gcloud run deploy $SERVICE_NAME \
  --image us-east4-docker.pkg.dev/$PROJECT_ID/$REPO/$SERVICE_NAME:$IMAGE_TAG \
  --region $REGION \
  --platform managed \
  --memory $MEMORY \
  --cpu $CPU \
  --concurrency $CONCURRENCY \
  --min-instances $MIN_INSTANCES \
  --max-instances $MAX_INSTANCES \
  --allow-unauthenticated \
  --service-account $SERVICE_ACCOUNT \
  --set-env-vars "PROJECT_ID=$PROJECT_ID,ENVIRONMENT=$ENVIRONMENT,API_KEY=$API_KEY" \
  --port $PORT

cd ..
echo "Emergency deployment completed!"
echo "The API should be available at: https://$SERVICE_NAME-$REGION.a.run.app/api/v1/status"
