# CRWLR API Server

CRWLR is a backend API for extracting and analyzing Terms of Service (ToS) and Privacy Policy (PP) documents from websites.

## Features

- Find and extract Terms of Service and Privacy Policy documents from any website URL
- Generate concise summaries of legal texts
- Analyze text for readability, complexity, and key terms
- Store and retrieve processed documents
- Search through processed documents

## Requirements

- Python 3.8 or higher
- See requirements.txt for Python packages

## Installation

1. Clone the repository
2. Create a virtual environment (Python 3.8+)
3. Install dependencies with `pip install -r requirements.txt`
4. Set up your environment variables (see Configuration below)
5. Run the server with `uvicorn app.main:app --reload`

## Configuration

Create a `.env` file in the root directory with the following variables:

```
# API Keys
API_KEY=your_api_key
GEMINI_API_KEY=your_gemini_api_key

# Firebase Configuration
FIREBASE_PROJECT_ID=your_firebase_project_id
FIREBASE_CLIENT_EMAIL=your_firebase_client_email
FIREBASE_PRIVATE_KEY="your_firebase_private_key"
FIREBASE_SERVICE_ACCOUNT_PATH=path/to/firebase-credentials.json

# Algolia Configuration (for search functionality)
ALGOLIA_APP_ID=your_algolia_app_id
ALGOLIA_API_KEY=your_algolia_admin_api_key

# CORS Settings (comma-separated)
BACKEND_CORS_ORIGINS=http://localhost:3000,http://localhost:8000,https://yourdomain.com
```

### Algolia Setup

For faster, more efficient document search:

1. Create an [Algolia account](https://dashboard.algolia.com/)
2. Create a new application in Algolia dashboard
3. Get your Application ID and Admin API Key
4. Add these to your `.env` file as shown above

## API Endpoints

The server exposes a FastAPI interface with the following key endpoints:

- `/api/v1/crawl/crawl-tos` - Find and analyze a Terms of Service document
- `/api/v1/crawl/crawl-pp` - Find and analyze a Privacy Policy document
- `/api/v1/browse/` - Browse previously processed documents
- `/api/v1/search/` - Search through documents

## Development

- Run tests with `pytest`
- Format code with `black`
- Check typing with `mypy`

## Deployment

The application can be deployed using Docker:

```bash
docker build -t crwlr-api .
docker run -p 8000:8000 crwlr-api
```

Or deploy directly to Google Cloud Run using the provided `deploy_to_cloud_run.sh` script.

## License

See LICENSE file.

# CRWLR Server

API server for CRWLR application.

## Firebase Configuration

The application now uses Firebase Firestore instead of PostgreSQL/Cloud SQL.

### Setting Up Firebase

1. Create a Firebase project at [https://console.firebase.google.com/](https://console.firebase.google.com/)
2. Enable the Firestore database service
3. Generate a service account key:
   - Go to Project Settings > Service accounts
   - Click "Generate new private key"
   - Save the JSON file securely

### Local Development

Configure Firebase by setting these environment variables:

```
ENVIRONMENT=production
FIREBASE_PROJECT_ID=your-firebase-project-id
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYour private key goes here\n-----END PRIVATE KEY-----\n"
FIREBASE_CLIENT_EMAIL=firebase-adminsdk-xxxxx@your-project-id.iam.gserviceaccount.com
```

### Cloud Run Deployment

When deploying to Cloud Run:

1. Add Firebase credentials as environment variables:

```bash
gcloud run deploy crwlr-server \
  --update-env-vars="FIREBASE_PROJECT_ID=your-project-id,FIREBASE_CLIENT_EMAIL=your-client-email" \
  --update-secrets="FIREBASE_PRIVATE_KEY=firebase-private-key:latest" \
  # other configuration options
```

2. Store sensitive information in Secret Manager:

```bash
echo -n "-----BEGIN PRIVATE KEY-----\nYour private key goes here\n-----END PRIVATE KEY-----\n" | \
gcloud secrets create firebase-private-key --data-file=-
```

## Emergency Deployment

The project contains an emergency deployment setup that's been tested and verified to work with Cloud Run.

### How to Deploy

To deploy the emergency version of the application:

```bash
# Make the deployment script executable
chmod +x deploy.sh
chmod +x deploy-emergency.sh

# Run the emergency deployment
./deploy-emergency.sh
```

The API will be available at: https://crwlr-server-662250507742.us-east4.run.app

### Clean Up Unwanted Scripts

To clean up unwanted scripts:

```bash
chmod +x cleanup.sh
./cleanup.sh
```

## Future Improvements

1. Gradually add back database connectivity with proper error handling
2. Add back Playwright browser initialization with timeouts
3. Update the GitHub Actions workflow to use the improved deployment approach

## Technical Details

The emergency deployment uses a minimal FastAPI application that:

- Has no database connectivity
- Has no complex initialization that could time out
- Includes only the essential files needed to run

This approach works because it eliminates all the complex initialization steps that were timing out.

---

# Cloud Run with Cloud SQL Setup Guide

This document explains how to properly set up your Cloud Run service to connect to Cloud SQL.

## Issue

When deploying the CRWLR API to Cloud Run, you encountered a timeout error when connecting to Cloud SQL:

```
TimeoutError: Connection to database timed out
```

## Solution

The issue occurs because Cloud Run services need to connect to Cloud SQL instances using a Unix socket, but the application was trying to use a TCP connection.

### 1. Connect using Unix Socket

In Cloud Run, connections to Cloud SQL must use the Unix socket at:

```
/cloudsql/PROJECT_ID:REGION:INSTANCE_NAME
```

For asyncpg with SQLAlchemy, the correct connection string format is:

```
postgresql+asyncpg://USERNAME:PASSWORD@/DATABASE_NAME?host=/cloudsql/PROJECT_ID:REGION:INSTANCE_NAME
```

### 2. Properly Configure Service Connection

When deploying to Cloud Run, you must add the Cloud SQL instance to your service:

```bash
gcloud run deploy SERVICE_NAME \
  --add-cloudsql-instances=PROJECT_ID:REGION:INSTANCE_NAME \
  # other configuration options
```

### 3. Environment Variables

Make sure the following environment variables are set in Cloud Run:

- `DB_USER` - Database username
- `DB_PASS` - Database password (use Secret Manager)
- `DB_NAME` - Database name
- `INSTANCE_CONNECTION_NAME` - Full instance connection name (PROJECT_ID:REGION:INSTANCE_NAME)

### 4. Connection Timeouts

Increase connection timeouts to allow for potential delays in establishing connections:

```python
async_engine = create_async_engine(
    async_connection_string,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_timeout=60,  # Increase from default 30 seconds
    pool_pre_ping=True,
    pool_recycle=1800,
)
```

### 5. Service Account Permissions

Ensure the service account running your Cloud Run service has the following permissions:

- Cloud SQL Client role (`roles/cloudsql.client`)
- Secret Manager Secret Accessor role (`roles/secretmanager.secretAccessor`) for any secrets used (like DB_PASS).

## Deployment

Use the `deploy_to_cloud_run.sh` script to deploy with all necessary configurations:

```bash
./deploy_to_cloud_run.sh
```

Alternatively, the GitHub Actions workflow in `.github/workflows/ci_cd.yml` handles deployment on push to `main`.

## Troubleshooting

If you encounter issues:

1. Check the Cloud Run service logs.
2. Check the Cloud SQL instance logs.
3. Verify the Cloud SQL instance is running and in the same region as Cloud Run.
4. Confirm the service account has the necessary IAM roles (`Cloud SQL Client`, `Secret Manager Secret Accessor`).
5. Double-check that the `INSTANCE_CONNECTION_NAME` environment variable in Cloud Run exactly matches the Cloud SQL instance connection name.

## Commit Changes

Once all changes related to the Cloud SQL setup are applied, commit them with:

```bash
git commit -m "Feat(deploy): configure Cloud Run deployment with Cloud SQL Unix socket connection"
```

---

# Local Development with Cloud SQL Proxy

When developing locally, you can connect to the Cloud SQL instance using the Cloud SQL Proxy tool. This allows you to securely connect to your production database without exposing it to the public internet.

## Setting Up Cloud SQL Proxy

### Mac Installation

1. Install using Homebrew:

   ```bash
   brew install google-cloud-sdk-cloud-sql-proxy
   ```

2. Alternatively, download the binary directly:
   ```bash
   curl -o cloud-sql-proxy https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.1/cloud-sql-proxy.darwin.amd64
   chmod +x cloud-sql-proxy
   ```

### Windows Installation

1. Download the Windows executable from Google Cloud Storage:

   ```
   https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.8.1/cloud-sql-proxy.x64.exe
   ```

2. Rename it to `cloud-sql-proxy.exe` for ease of use

3. Add the location to your PATH or move the executable to a directory that's already in your PATH

## Running the Proxy

Start the proxy with the following command:

```bash
cloud-sql-proxy crwlr-server:us-east4:crwlr-db --port 5432
```

This will make your Cloud SQL database available at `localhost:5432`.

## Configuring Your Application

When running locally with the proxy, use the following database connection string:

```
postgresql+asyncpg://USERNAME:PASSWORD@localhost:5432/DATABASE_NAME
```

You may want to set up environment variables to handle the switch between local and Cloud Run deployments:

```python
# Example code for handling different environments
import os

# Check if running in Cloud Run (INSTANCE_CONNECTION_NAME will be set)
if "INSTANCE_CONNECTION_NAME" in os.environ:
    # Cloud Run - use Unix socket
    host_connection = f"/cloudsql/{os.environ.get('INSTANCE_CONNECTION_NAME')}"
    connection_string = f"postgresql+asyncpg://{os.environ.get('DB_USER')}:{os.environ.get('DB_PASS')}@/{os.environ.get('DB_NAME')}?host={host_connection}"
else:
    # Local development - use TCP via Cloud SQL Proxy
    connection_string = f"postgresql+asyncpg://{os.environ.get('DB_USER')}:{os.environ.get('DB_PASS')}@localhost:5432/{os.environ.get('DB_NAME')}"
```

## Authentication

The Cloud SQL Proxy uses your gcloud authentication. Make sure you're logged in:

```bash
gcloud auth login
```

And set the correct project:

```bash
gcloud config set project crwlr-server
```
