#!/bin/bash
# This script helps set up IAM permissions for Cloud Build to deploy to Cloud Run and creates triggers for different environments

# Set your project ID
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

# Grant the Cloud Run Admin role to the Cloud Build service account
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
  --role="roles/run.admin"

# Grant the IAM Service Account User role to the Cloud Build service account
# This is needed to act as the Cloud Run runtime service account
gcloud iam service-accounts add-iam-policy-binding \
  $PROJECT_NUMBER-compute@developer.gserviceaccount.com \
  --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"

# Grant the Storage Admin role to the Cloud Build service account
# This is needed to push images to Container Registry
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
  --role="roles/storage.admin"

echo "IAM permissions have been set up for Cloud Build to deploy to Cloud Run."

# Create Cloud Build triggers for different environments
echo "Creating Cloud Build triggers for different environments..."

# Read the GitHub repository URL - this assumes you have a GitHub remote set up
GITHUB_REPO_URL=$(git config --get remote.origin.url)
GITHUB_REPO_NAME=${GITHUB_REPO_URL#*github.com/}
GITHUB_REPO_NAME=${GITHUB_REPO_NAME%.git}
GITHUB_OWNER=$(echo $GITHUB_REPO_NAME | cut -d'/' -f1)
GITHUB_REPO=$(echo $GITHUB_REPO_NAME | cut -d'/' -f2)

echo "GitHub repository: $GITHUB_OWNER/$GITHUB_REPO"

# Store Gemini API key as a secret (you'll need to have this available)
echo "Please enter your Gemini API key:"
read -s GEMINI_API_KEY
echo

# Create the secret for Gemini API key
echo "Creating secret for Gemini API key..."
echo -n "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=- --project=$PROJECT_ID

# Grant access to the secret for Cloud Build
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=$PROJECT_ID

# Function to create a build trigger
create_trigger() {
  local branch=$1
  local service_name=$2
  local is_manual=$3
  local trigger_name="github-trigger-$branch"
  
  echo "Creating trigger for $branch branch deploying to $service_name..."
  
  if [ "$is_manual" = true ]; then
    # Create manual trigger with approval required
    gcloud builds triggers create github \
      --name="$trigger_name" \
      --region="us-east4" \
      --repo-owner="$GITHUB_OWNER" \
      --repo-name="$GITHUB_REPO" \
      --branch-pattern="^$branch$" \
      --build-config="cloudbuild.yaml" \
      --require-approval \
      --substitutions="_SERVICE_NAME=$service_name,_GEMINI_API_KEY=projects/$PROJECT_ID/secrets/gemini-api-key/versions/latest"
  else
    # Create automatic trigger that runs on push
    gcloud builds triggers create github \
      --name="$trigger_name" \
      --region="us-east4" \
      --repo-owner="$GITHUB_OWNER" \
      --repo-name="$GITHUB_REPO" \
      --branch-pattern="^$branch$" \
      --build-config="cloudbuild.yaml" \
      --substitutions="_SERVICE_NAME=$service_name,_GEMINI_API_KEY=projects/$PROJECT_ID/secrets/gemini-api-key/versions/latest"
  fi
}

# Create triggers for each environment
# Only main is automatic, test and dev are manual
create_trigger "main" "crwlr-server" false
create_trigger "test" "crwlr-server-test" true
create_trigger "dev" "crwlr-server-dev" true

echo "Setup complete!"
echo "Cloud Build triggers have been created:"
echo "1. github-trigger-main - Automatically deploys to crwlr-server from the main branch"
echo "2. github-trigger-test - Manual trigger to deploy to crwlr-server-test from the test branch"
echo "3. github-trigger-dev - Manual trigger to deploy to crwlr-server-dev from the dev branch"
echo ""
echo "To manually deploy from test or dev branches, use:"
echo "gcloud builds triggers run github-trigger-test --region=us-east4"
echo "gcloud builds triggers run github-trigger-dev --region=us-east4"
echo ""
echo "The Gemini API key has been stored as a secret and linked to the triggers." 