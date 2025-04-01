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
