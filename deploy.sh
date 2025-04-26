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

# Check for required environment variables
required_vars=("GEMINI_API_KEY" "DB_USER" "DB_PASS" "DB_NAME" "INSTANCE_CONNECTION_NAME" "DB_IP_ADDRESS" "API_KEY" "PROJECT_ID" "ENV" "USE_CLOUD_SQL_PROXY")
missing_vars=()

for var_name in "${required_vars[@]}"; do
  # Use indirect expansion to get the value of the variable named by var_name
  if [ -z "${!var_name}" ]; then
    missing_vars+=("$var_name")
  fi
done

if [ ${#missing_vars[@]} -ne 0 ]; then
  echo "Error: Missing required environment variables:" >&2
  for var_name in "${missing_vars[@]}"; do
    echo "  - $var_name" >&2
  done
  exit 1
fi

echo "All required environment variables are set."

# Define image tag - using 'latest' for simplicity in manual script
IMAGE_TAG="latest"
IMAGE_URI="us-east4-docker.pkg.dev/$PROJECT_ID/crwlr-repo/$SERVICE_NAME:$IMAGE_TAG"

# Build and push the full container using the default Dockerfile
echo "Building and pushing container image: $IMAGE_URI"
# Use gcloud builds submit with the default Dockerfile in the current directory
gcloud builds submit --tag "$IMAGE_URI" .

echo "Deploying container..."

# Construct the --set-env-vars string dynamically
env_vars_string="PROJECT_ID=$PROJECT_ID"
env_vars_string+=",ENVIRONMENT=$ENV"
env_vars_string+=",API_KEY=$API_KEY"
env_vars_string+=",GEMINI_API_KEY=$GEMINI_API_KEY"
env_vars_string+=",DB_USER=$DB_USER"
env_vars_string+=",DB_PASS=$DB_PASS"
env_vars_string+=",DB_NAME=$DB_NAME"
env_vars_string+=",INSTANCE_CONNECTION_NAME=$INSTANCE_CONNECTION_NAME"
env_vars_string+=",DB_IP_ADDRESS=$DB_IP_ADDRESS"
env_vars_string+=",USE_CLOUD_SQL_PROXY=$USE_CLOUD_SQL_PROXY"

# Deploy using the newly built image tag
# Adjust memory/cpu/etc. as needed for full deployment vs emergency
gcloud run deploy $SERVICE_NAME \
  --image "$IMAGE_URI" \
  --region $REGION \
  --platform managed \
  --memory 2Gi \
  --cpu 1 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 100 \
  --allow-unauthenticated \
  --service-account github-actions-deployer@crwlr-server.iam.gserviceaccount.com \
  --set-env-vars "$env_vars_string" \
  --port 8080

echo "Deployment completed!"
# Construct the service URL dynamically
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --platform managed --region $REGION --format='value(status.url)')
echo "The service should be available at: $SERVICE_URL"
