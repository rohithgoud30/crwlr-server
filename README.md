# CRWLR Server

API server for CRWLR application.

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
