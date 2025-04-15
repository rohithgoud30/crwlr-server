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

# Check if GitHub repo is properly configured
if ! git remote -v | grep -q "github.com"; then
  echo "Error: No GitHub remote detected. Please configure your git repository with a GitHub remote first."
  exit 1
fi

# Extract GitHub repository information
GITHUB_REPO_URL=$(git remote get-url origin)
# Remove .git extension if present
GITHUB_REPO_URL=${GITHUB_REPO_URL%.git}
# Extract owner/repo from the URL
if [[ $GITHUB_REPO_URL == *"github.com"* ]]; then
  GITHUB_REPO_NAME=$(echo $GITHUB_REPO_URL | sed -E 's|.*github.com[/:]([^/]+/[^/]+).*|\1|')
else
  echo "Error: Could not parse GitHub repository URL from git remote."
  exit 1
fi

GITHUB_OWNER=$(echo $GITHUB_REPO_NAME | cut -d'/' -f1)
GITHUB_REPO=$(echo $GITHUB_REPO_NAME | cut -d'/' -f2)

echo "GitHub repository: $GITHUB_OWNER/$GITHUB_REPO"

# Store Gemini API key as a secret (you'll need to have this available)
echo "Please enter your Gemini API key:"
read -s GEMINI_API_KEY
echo

# Create the secret for Gemini API key (or update it if it exists)
echo "Creating/updating secret for Gemini API key..."
if gcloud secrets describe gemini-api-key --project=$PROJECT_ID > /dev/null 2>&1; then
  echo "Secret already exists, updating it..."
  echo -n "$GEMINI_API_KEY" | gcloud secrets versions add gemini-api-key --data-file=- --project=$PROJECT_ID
else
  echo "Creating new secret..."
  echo -n "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=- --project=$PROJECT_ID
fi

# Grant access to the secret for Cloud Build
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=$PROJECT_ID

# Check if Triggers API is enabled
if ! gcloud services list --enabled | grep -q "cloudbuild.googleapis.com"; then
  echo "Enabling Cloud Build API..."
  gcloud services enable cloudbuild.googleapis.com
fi

# Function to create or update a build trigger
create_trigger() {
  local branch=$1
  local service_name=$2
  local is_manual=$3
  local trigger_name="github-trigger-$branch"
  
  echo "Creating trigger for $branch branch deploying to $service_name..."
  
  # Check if trigger exists
  if gcloud builds triggers list --region=us-east4 --filter="name:$trigger_name" | grep -q "$trigger_name"; then
    echo "Trigger $trigger_name already exists, deleting it..."
    gcloud builds triggers delete "$trigger_name" --region=us-east4 --quiet
  fi
  
  # Create temporary config file
  TEMP_FILE=$(mktemp)
  cat > $TEMP_FILE << EOF
{
  "name": "$trigger_name",
  "github": {
    "owner": "$GITHUB_OWNER",
    "name": "$GITHUB_REPO",
    "push": {
      "branch": "^$branch$"
    }
  },
  "build": {
    "steps": [
      {
        "name": "gcr.io/cloud-builders/docker",
        "args": ["build", "-t", "gcr.io/\$PROJECT_ID/$service_name:\$COMMIT_SHA", "."]
      },
      {
        "name": "gcr.io/cloud-builders/docker",
        "args": ["push", "gcr.io/\$PROJECT_ID/$service_name:\$COMMIT_SHA"]
      },
      {
        "name": "gcr.io/google.com/cloudsdktool/cloud-sdk",
        "entrypoint": "gcloud",
        "args": [
          "run", "deploy", "$service_name",
          "--image", "gcr.io/\$PROJECT_ID/$service_name:\$COMMIT_SHA",
          "--region", "us-east4",
          "--platform", "managed",
          "--memory", "2Gi",
          "--cpu", "1",
          "--timeout", "3600s",
          "--concurrency", "80",
          "--min-instances", "0",
          "--max-instances", "100",
          "--allow-unauthenticated",
          "--service-account", "\${_SERVICE_ACCOUNT}",
          "--set-env-vars", "PROJECT_ID=\$PROJECT_ID,GEMINI_API_KEY=\${_GEMINI_API_KEY}"
        ]
      }
    ],
    "substitutions": {
      "_GEMINI_API_KEY": "projects/$PROJECT_ID/secrets/gemini-api-key/versions/latest",
      "_SERVICE_ACCOUNT": "$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
    },
    "options": {
      "logging": "CLOUD_LOGGING_ONLY",
      "machineType": "E2_HIGHCPU_8"
    }
  }
}
EOF

  if [ "$is_manual" = true ]; then
    # For manual triggers, add approvalRequired field
    jq '. += {"approvalRequired": true}' $TEMP_FILE > ${TEMP_FILE}.2
    mv ${TEMP_FILE}.2 $TEMP_FILE
  fi

  # Create the trigger using the JSON config
  gcloud builds triggers create --region=us-east4 --config=$TEMP_FILE

  # Clean up the temp file
  rm $TEMP_FILE
}

# Install jq if not present (required for JSON manipulation)
if ! command -v jq &> /dev/null; then
  echo "jq is required but not installed. Please install jq and run the script again."
  echo "You can install it with: brew install jq (macOS) or apt-get install jq (Linux)"
  exit 1
fi

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