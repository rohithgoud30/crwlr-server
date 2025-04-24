# CRWLR Backend API

A simple but enterprise-structured FastAPI backend for the CRWLR project.

## Features

- FastAPI with async support
- Structured project layout following enterprise practices
- Environment-based configuration
- CORS middleware
- OpenAPI documentation
- Health check endpoint
- Legal document finder APIs:
  - Terms of Service (ToS) finder
  - Privacy Policy finder
  - Both support URLs without protocol prefix (e.g., "google.com")
  - Smart URL handling:
    - Prioritizes checking the exact URL provided before falling back to base domain variations
    - Tries multiple variations (with/without www, http/https, with/without trailing slash)
    - Intelligent handling of GitHub repositories:
      - Distinguishes between repository-specific links and site-wide GitHub links
      - Examines repository root for policy files and documentation
      - Checks README files for sections on privacy, terms, or links to policy files
      - Special handling for security policy pages, repository docs, and code of conduct links
      - For pages like security policies, can consider the content itself as a legal document
    - Special handling for App Store app URLs:
      - Automatically detects App Store app URLs (apps.apple.com/_/app/_/id\*)
      - Extracts app-specific privacy policies and terms of service
      - Ignores Apple's general policies in favor of app-specific ones
      - Works with all App Store regional domains
    - Respects redirects and follows them
  - Advanced detection for various link patterns
  - Looks for related legal links (Privacy links near Terms links and vice versa)
  - Detailed error messages directly in the response
  - JavaScript support: Falls back to Playwright browser automation when standard scraping fails

## Requirements

- Python 3.8+
- [uv](https://github.com/astral-sh/uv) for dependency management (recommended)
- Playwright for JavaScript-rendered sites

## Setup

1. Clone the repository:

```bash
git clone https://github.com/yourusername/crwlr-backend-api.git
cd crwlr-backend-api
```

2. Set up a virtual environment:

```bash
# Using uv
uv venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Or using Python venv
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
```

3. Install dependencies:

```bash
# Using uv (recommended)
uv pip install -r requirements.txt

# Or using pip
pip install -r requirements.txt
```

4. Install Playwright browsers:

```bash
playwright install chromium
```

5. Copy the example environment file and adjust it as needed:

```bash
cp .env.example .env
```

## Running the Application

Run the application with uvicorn:

```bash
uvicorn app.main:app --reload
```

Or use the provided run script:

```bash
python run.py
```

The API will be available at http://localhost:8000, and the interactive documentation at http://localhost:8000/docs.

## API Authentication

The API is protected with API key authentication to ensure secure access. This requires:

1. Setting up an API key in your `.env` file for local development:

   ```
   API_KEY=your_unique_api_key_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

2. Including the API key in all requests as an HTTP header:

   ```
   X-API-Key: your_unique_api_key_here
   ```

3. For deployment to Cloud Run:
   - The deployment script (`deploy.sh`) automatically reads API keys from your `.env` file
   - Make sure your `.env` file contains both `API_KEY` and `GEMINI_API_KEY` values
   - Simply run `./deploy.sh` to deploy with the correct API keys
   - The script never reveals or exposes your API keys in logs
   - For Cloud Build deployments, the API keys are stored as Secret Manager secrets
   - The cloudbuild.yaml file is configured to access these secrets securely

To manually set or update the API key on an existing Cloud Run service:

```bash
# Get API key from .env file
API_KEY=$(grep -o 'API_KEY=.*' .env | cut -d '=' -f2)
# Update Cloud Run service
gcloud run services update crwlr-server --platform managed --region us-east4 --set-env-vars API_KEY=$API_KEY
```

Without a valid API key, all API endpoints will return a 401 Unauthorized error.

## Database Setup

CRWLR uses a PostgreSQL database to store users, documents, and submissions. The database schema includes:

- **users**: User accounts with clerk_user_id, email, name, and role
- **documents**: Document data with URL, document_type (ToS or Privacy Policy), retrieved content, and analysis
- **submissions**: Records of URL processing requests with status tracking and relations to users and documents

### Database Configuration

CRWLR connects to a PostgreSQL database instance. You need the following environment variables in your `.env` file:

```
DB_USER=postgres
DB_PASS=your_password
DB_NAME=postgres
DB_HOST=your-db-host-ip
DB_PORT=5432
```

For Google Cloud SQL, also include:

```
INSTANCE_CONNECTION_NAME=your-project:region:instance-name
```

To initialize the database:

```bash
./init_db.sh
```

The initialization process:

1. Drops existing tables if they exist
2. Creates the `pgcrypto` extension for UUID generation
3. Creates the `DocumentType` enum type for categorizing documents
4. Creates all database tables with the correct relationships

### Database Schema

The database uses UUID primary keys and maintains proper relationships between tables:

- **users** table: Stores user information with authentication details
- **documents** table: Stores document content, metadata, and analysis results
- **submissions** table: Tracks document processing requests and their status

All tables include created_at and updated_at timestamps for tracking record history.

## Project Structure

```
app/
├── api/
│   └── v1/
│       ├── api.py           # API router
│       └── endpoints/       # API endpoints
│           ├── health.py    # Health check endpoint
│           └── tos.py       # Terms of Service finder endpoint
├── core/
│   └── config.py            # App configuration
└── main.py                  # Main application entry point
```

## API Endpoints

### Health Check

- `GET /api/v1/health`: Returns the health status of the API

### Terms of Service Finder

- `POST /api/v1/tos`: Finds the Terms of Service page URL for a given website
  - Request body: `{ "url": "example.com" }` or `{ "url": "https://example.com" }` or `{ "url": "https://example.com/specific/page" }`
  - Special handling for different URL types:
    - Regular websites: Searches for ToS links on the page/domain
    - GitHub repositories: Finds repository-specific ToS documents
    - App Store apps: Extracts app-specific terms of service (example: `{ "url": "https://apps.apple.com/us/app/assassins-creed-shadows/id6497794841" }`)
  - Smart URL handling:
    - Automatically adds protocol if missing
    - Prioritizes checking the exact URL provided (useful for deep links)
    - Falls back to base domain and its variations if no links found on the specific page
    - Tries multiple variations (with/without www, http/https)
    - Respects redirects and follows them
    - Works even for sites that require specific domain format (with or without www)
    - Fallback to Playwright for JavaScript-heavy sites that dynamically load content
  - All errors are handled gracefully and returned in the response (no HTTP error codes)
  - Response for success:
    ```json
    {
      "url": "https://example.com/specific/page",
      "tos_url": "https://example.com/terms",
      "success": true,
      "message": "Terms of Service link found on final destination page: https://example.com/specific/page",
      "method_used": "standard" // or "playwright" if JS rendering was used
    }
    ```
  - Response for App Store app success:
    ```json
    {
      "url": "https://apps.apple.com/us/app/app-name/id123456789",
      "tos_url": "https://developer.com/terms",
      "success": true,
      "message": "Terms of Service link found for app: App Name",
      "method_used": "app_store_standard"
    }
    ```
  - Response for errors:
    ```json
    {
      "url": "https://example.com/specific/page",
      "tos_url": null,
      "success": false,
      "message": "[Detailed error message explaining what went wrong]",
      "method_used": "standard_failed" // or other failure type
    }
    ```

### Privacy Policy Finder

- `POST /api/v1/privacy`: Finds the Privacy Policy page URL for a given website
  - Request body: `{ "url": "example.com" }` or `{ "url": "https://example.com" }` or `{ "url": "https://example.com/specific/page" }`
  - Special handling for different URL types:
    - Regular websites: Searches for privacy policy links on the page/domain
    - GitHub repositories: Finds repository-specific privacy documents
    - App Store apps: Extracts app-specific privacy policies (example: `{ "url": "https://apps.apple.com/us/app/assassins-creed-shadows/id6497794841" }`)
  - Follows the same URL handling strategy as the ToS endpoint
  - Also includes Playwright fallback for JavaScript-heavy sites
  - Response for success:
    ```json
    {
      "url": "https://developer.com",
      "pp_url": "https://developer.com/privacy",
      "success": true,
      "message": "Privacy Policy link found on final destination page: https://developer.com"
    }
    ```
  - Response for errors:
    ```json
    {
      "url": "https://example.com",
      "pp_url": null,
      "success": false,
      "message": "No Privacy Policy link found with standard method on the final destination page: https://example.com."
    }
    ```

### App Store Legal Document Finder

- `POST /api/v1/app-store-legal`: Extracts privacy policy and terms of service links for App Store apps
  - **IMPORTANT**: This endpoint only works with specific App Store app URLs, not the general App Store website
  - Valid URL format: `https://apps.apple.com/{country}/app/{app-name}/id{app-id}`
  - Example: `https://apps.apple.com/us/app/assassins-creed-shadows/id6497794841`
  - Request body: `{ "url": "https://apps.apple.com/us/app/app-name/id123456789" }`
  - When to use this endpoint vs. general endpoints:
    - ✅ Use this endpoint: For specific app pages (apps.apple.com/_/app/_/id\*)
    - ❌ Don't use: For Apple's main website (apple.com) or general App Store (apple.com/app-store)
    - ❌ Don't use: For Google Play Store (use the general privacy/tos endpoints instead)
  - Specialized handling for App Store pages:
    - Extracts app name and developer information
    - Finds links to privacy policy and terms of service
    - Extracts privacy label data from the App Store page
    - Works with various App Store regional domains
    - Fallback to Playwright for dynamic content
  - Response for success:
    ```json
    {
      "url": "https://apps.apple.com/us/app/app-name/id123456789",
      "app_name": "App Name",
      "developer": "Developer Name",
      "privacy_url": "https://developer.com/privacy",
      "tos_url": "https://developer.com/terms",
      "privacy_label": {
        "data_collection": ["Location", "Identifiers", "Usage Data"]
      },
      "success": true,
      "message": "Found legal information for app: App Name",
      "method_used": "standard" // or "playwright" if JS rendering was used
    }
    ```
  - Response for errors or partial success:
    ```json
    {
      "url": "https://apps.apple.com/us/app/app-name/id123456789",
      "app_name": "App Name",
      "developer": "Developer Name",
      "privacy_url": null,
      "tos_url": null,
      "privacy_label": null,
      "success": false,
      "message": "Found app information but couldn't locate legal URLs for: App Name",
      "method_used": "partial"
    }
    ```

## Error Handling

The API handles various error scenarios and provides detailed messages:

- For sites that block web scraping (403, 400 errors): Explains that the site is blocking automated access
- For non-existent domains or connection errors: Provides specific network-related errors
- For sites without accessible ToS links: Suggests possibilities (JavaScript requirement, hidden links)
- For rate limiting (429 errors): Explains that the site is rate-limiting requests

All errors are returned in the standard response format with HTTP 200 status code,
allowing for easier client-side handling and consistent error presentation.

## URL Handling Strategies

The API uses a smart prioritized URL handling strategy:

1. First checks the exact URL provided by the user (even if it's a deep link)
2. Follows redirects to identify the final destination page for that URL
3. Searches for legal links directly on that specific page
4. If no links are found, falls back to checking the base domain (example.com instead of example.com/page)
5. For the base domain, tries a few basic variations:
   - With/without www prefix
   - HTTP instead of HTTPS (as a last resort)
6. If standard scraping fails, falls back to browser automation with Playwright:
   - Fully renders the page with JavaScript
   - Attempts to find links after JavaScript execution
   - Tries to interact with cookie/consent buttons that might reveal links
   - Handles single-page applications better than standard scraping

This approach handles many common issues like:

- Sites that have specific pages with their own legal links (like GitHub repository pages)
- Sites that redirect from the main domain to specific pages (e.g., t3.chat redirecting to t3.chat/chat)
- Login or welcome pages that contain footer links to legal documents
- Sites that operate differently on www vs non-www domains
- Sites that use HTTP instead of HTTPS
- Single-page applications that load content dynamically with JavaScript
- Sites with cookie/consent banners that hide content until user interaction

The API focuses exclusively on finding links in the actual HTML content and doesn't rely on predefined paths or patterns.

## Development

### Adding New Endpoints

1. Create a new file in `app/api/v1/endpoints/` for your endpoint.
2. Add the router to `app/api/v1/api.py`.

## License

[MIT](LICENSE)

## CI/CD Pipeline with Google Cloud Build

This repository is configured for continuous integration and deployment using Google Cloud Build, which automatically builds and deploys your application to Cloud Run whenever changes are pushed to the main branch.

### Prerequisites

- Google Cloud Platform project with billing enabled
- Cloud Build, Cloud Run, and Container Registry APIs enabled
- Appropriate permissions to set up IAM roles

### Setup Instructions

1. **Set up IAM permissions**

   Run the provided script to set up necessary IAM permissions:

   ```bash
   chmod +x setup-cloud-build-permissions.sh
   ./setup-cloud-build-permissions.sh
   ```

   This script grants the Cloud Build service account the necessary permissions to:

   - Deploy to Cloud Run
   - Access the Compute service account
   - Upload images to Container Registry

2. **Set up a Cloud Build Trigger**

   1. Go to the [Cloud Build Triggers](https://console.cloud.google.com/cloud-build/triggers) page
   2. Click "Create Trigger"
   3. Connect your repository (GitHub, Bitbucket, etc.)
   4. Configure the trigger:
      - Name: `crwlr-server-deploy`
      - Event: "Push to a branch"
      - Source: Select your repository and branch (e.g., main)
      - Configuration: Select "Cloud Build configuration file (yaml or json)"
      - Location: `cloudbuild.yaml`
   5. Add substitution variables:
      - `_GEMINI_API_KEY`: Your Gemini API key (treat as a secret)
      - `_SERVICE_ACCOUNT`: (Optional) Custom service account email
   6. Click "Create"

3. **Set up Secrets (Optional but Recommended)**

   For better security, store your API key in Secret Manager:

   ```bash
   # Create a secret
   echo -n "your-gemini-api-key" | gcloud secrets create gemini-api-key --data-file=-

   # Grant Cloud Build access to the secret
   PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
   gcloud secrets add-iam-policy-binding gemini-api-key \
     --member="serviceAccount:$PROJECT_NUMBER@cloudbuild.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```

   Then update your trigger to reference this secret.

### How It Works

The CI/CD pipeline performs the following steps:

1. Builds a Docker container using the project's Dockerfile
2. Pushes the container to Google Container Registry
3. Deploys the container to Cloud Run
4. Tags the container with `latest` for future reference

These steps are defined in the `cloudbuild.yaml` file in the root of the repository.

### Manual Deployment

If you need to deploy manually, you can trigger a build using:

```bash
gcloud builds submit --config=cloudbuild.yaml
```

### Troubleshooting

- Check Cloud Build logs for any build or deployment errors
- Ensure all required IAM permissions are set
- Verify the `cloudbuild.yaml` file is properly formatted
- Check that your Docker container builds and runs locally

Don't forget to commit your changes with:

```bash
git add .github/workflows/ cloudbuild.yaml setup-cloud-build-permissions.sh
git commit -m "Chore(deps): set up Cloud Build CI/CD pipeline"
```
