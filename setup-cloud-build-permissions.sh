#!/bin/bash
# Script to set up IAM permissions for Cloud Build to deploy to Cloud Run

# Set your Google Cloud project ID
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
CLOUD_BUILD_SA="$PROJECT_NUMBER@cloudbuild.gserviceaccount.com"
COMPUTE_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

echo "Setting up IAM permissions for Cloud Build service account: $CLOUD_BUILD_SA"

# Grant Cloud Run Admin role to Cloud Build service account
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$CLOUD_BUILD_SA" \
    --role="roles/run.admin"

# Grant IAM Service Account User role to Cloud Build service account for the compute service account
gcloud iam service-accounts add-iam-policy-binding $COMPUTE_SA \
    --member="serviceAccount:$CLOUD_BUILD_SA" \
    --role="roles/iam.serviceAccountUser"

# Grant Storage Admin role to Cloud Build service account for artifact storage
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$CLOUD_BUILD_SA" \
    --role="roles/storage.admin"

echo "✅ IAM permissions set up successfully!"
echo ""
echo "Next steps:"
echo "1. Create a Cloud Build trigger for your repository"
echo "2. Add the GEMINI_API_KEY as a secret in Secret Manager"
echo "3. Configure the Cloud Build trigger to use this secret"
echo "4. Push to your repository to trigger a build" 