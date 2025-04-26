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
- `DB_PASS` - Database password
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

- Cloud SQL Client role

## Deployment

Use the `deploy_to_cloud_run.sh` script to deploy with all necessary configurations:

```bash
./deploy_to_cloud_run.sh
```

## Troubleshooting

If you encounter issues:

1. Check the logs to verify the correct connection string is being used
2. Verify the Cloud SQL instance is in the same region as your Cloud Run service
3. Confirm the service account has the necessary permissions
4. Make sure the Cloud SQL instance name in your environment matches the one in GCP

## Commit Changes

Once all changes are applied, commit them with:

```bash
git commit -m "Feat(deploy): configure Cloud Run deployment with Cloud SQL Unix socket connection"
```
