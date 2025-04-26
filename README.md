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
