#!/bin/bash

# Build the container
docker build -t gcr.io/crwlr-app/crwlr-server:latest .

# Push to Container Registry
docker push gcr.io/crwlr-app/crwlr-server:latest

# Deploy to Cloud Run with improved settings
gcloud run deploy crwlr-server \
  --image gcr.io/crwlr-app/crwlr-server:latest \
  --region us-east4 \
  --platform managed \
  --memory 2Gi \
  --cpu 1 \
  --timeout 3600s \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 100 \
  --allow-unauthenticated \
  --service-account crwlr-server@crwlr-app.iam.gserviceaccount.com \
  --set-env-vars PROJECT_ID=crwlr-app,ENVIRONMENT=development \
  --startup-cpu-boost \
  --cpu-throttling \
  --port 8080 \
  --startup-probe-path /health \
  --startup-probe-initial-delay 60s \
  --container-command python \
  --container-arg -u \
  --container-arg run.py 