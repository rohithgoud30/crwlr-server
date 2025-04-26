# Cloud Run Deployment Without Cloud SQL Proxy

This branch implements changes to allow direct connections to Cloud SQL in Cloud Run without requiring the Cloud SQL Proxy.

## Changes Made

1. **Database Connection Logic**:

   - Added a new `NO_PROXY` mode in `app/core/database.py` that enables direct connections
   - The environment variable `NO_PROXY=true` triggers this mode

2. **Deployment Scripts**:

   - Updated `deploy-cloud-run.sh` to include the `NO_PROXY=true` environment variable
   - Updated `cloudbuild.yaml` to include the `NO_PROXY=true` environment variable
   - Updated GitHub Actions workflow to include the `NO_PROXY=true` environment variable
   - Created a new deployment script `deploy-no-proxy.sh` specifically for testing this feature

3. **Testing**:
   - Added a new test script `test_direct_connection.py` to verify direct connections

## How to Deploy

### Option 1: Use the new deployment script

```bash
# Make sure the script is executable
chmod +x deploy-no-proxy.sh

# Run the deployment script
./deploy-no-proxy.sh
```

This will deploy to a separate service named `crwlr-api-no-proxy` to avoid conflicts with the main deployment.

### Option 2: Update the existing deployment

```bash
# Execute the normal deployment script which now includes NO_PROXY=true
./deploy-cloud-run.sh
```

## Testing the Connection

To test the direct connection, use the provided test script:

```bash
# Set the environment variable
export NO_PROXY=true

# Run the test script
python test_direct_connection.py
```

## Configuration Requirements

For direct connections to work, you need to:

1. Set the following environment variables:

   - `NO_PROXY=true` - Enables direct connection mode
   - `DB_HOST` - Public IP address of your Cloud SQL instance
   - `DB_PORT` - Port for your Cloud SQL instance (usually 5432)
   - `DB_USER` - Database username
   - `DB_PASS` - Database password
   - `DB_NAME` - Database name

2. Configure your Cloud SQL instance to:
   - Enable public IP access
   - Add the Cloud Run service's IP to the authorized networks

## Security Considerations

When using direct connections instead of Cloud SQL Proxy:

1. Ensure your Cloud SQL instance is properly secured with:

   - Strong passwords
   - IP allowlist limitations
   - Proper IAM permissions

2. Consider implementing additional security measures:
   - TLS/SSL connections
   - Connection pooling to limit the number of connections

## Troubleshooting

If you encounter issues with direct connections:

1. Verify that your Cloud SQL instance has a public IP address assigned
2. Check that the public IP access is enabled in Cloud SQL
3. Ensure the Cloud Run service's IP is in the authorized networks list
4. Verify that the database credentials are correct and have proper permissions
