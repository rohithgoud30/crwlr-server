# CRWLR Server

API server for CRWLR application.

## Technology Stack

- **FastAPI**: Modern web framework for building APIs
- **Neon (PostgreSQL)**: Primary data store and search backend

## Setup Instructions

### Prerequisites

- Python 3.9+

### Environment Variables

Create a `.env` file with the following configuration:

```
# API Keys
API_KEY=your_api_key_here
GEMINI_API_KEY=your_gemini_api_key
ZAI_API_KEY=your_zai_api_key

# Environment setting
ENVIRONMENT=development  # or 'production'

# Neon PostgreSQL
NEON_DATABASE_URL=postgresql://user:password@ep-your-url.neon.tech/neondb

# Summary provider configuration
SUMMARY_PROVIDER=google        # 'google' or 'zai'
```

### Summary Provider Selection

Set `SUMMARY_PROVIDER` to `google` (Gemini) or `zai` (Z.AI). When set to `google`, the service defaults to `gemini-2.0-flash-lite`; when set to `zai`, it uses `GLM-4.5-Air`. You can still override per request by sending `provider` and `model` fields to the `/summary` endpoint. Models that start with `glm` automatically route to Z.AI; models containing `gemini` route to Google.

## Deployment

### Render (Docker)

1. In Render, click **New + → Web Service** and select **Build & deploy from a Git repository**. Pick the `main` branch of this repo.
2. Choose **Docker** when asked for the environment. Render will detect `Dockerfile`, but you can also import the repository’s `render.yaml` blueprint (Render dashboard → **Blueprints → New Blueprint**). The blueprint tracks the `main` branch, uses the Dockerfile, enables the `/health` check, and sets auto-deploys.
3. Define the secrets referenced in `render.yaml` under **Settings → Secrets**:
   - `api-key`, `gemini-api-key`, `zai-api-key`
   - `neon-database-url`
   - `summary-provider`
4. Still in Render, enable a **Deploy Hook** for the service and copy the URL; this lets GitHub Actions trigger deployments after tests pass.

### GitHub Actions

- Workflow: `.github/workflows/render-deploy.yml`
- Triggers on pushes and pull requests targeting `main`.
- On pushes to `main`, the `deploy` job posts to the Render Deploy Hook.
- Add the hook URL as the `RENDER_DEPLOY_HOOK` secret in GitHub (Repository → Settings → Secrets and variables → Actions).

## API Documentation

Once running, access the API documentation at:

- http://localhost:8080/docs (local)
- https://your-production-host/docs (deployed)

## API Endpoints

Below is a detailed list of available API endpoints, including HTTP method, path, headers, request and response formats, and examples.

### 1. Search Documents

- **Method:** POST
- **Path:** `/api/v1/documents/search`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "search_text": "example", // Text to search in company_name and url
    "document_type": "tos", // Optional filter: "tos" or "pp"
    "page": 1, // Page number (>=1)
    "per_page": 6, // Items per page (1–100)
    "sort_by": "relevance", // One of: relevance, views, company_name, updated_at
    "sort_order": "desc" // "asc" or "desc"
  }
  ```
- **Response:** `DocumentSearchResponse`
  ```json
  {
    "items": [
      {
        "id": "abc123",
        "url": "example.com",
        "document_type": "tos",
        "company_name": "Example Corp",
        "logo_url": "https://...",
        "views": 42,
        "updated_at": "2025-05-08T12:34:56"
      }
    ],
    "total": 1,
    "page": 1,
    "per_page": 6,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
  ```

### 2. Get Document Counts

- **Method:** GET
- **Path:** `/api/v1/documents/stats`
- **Headers:** `X-API-Key: {API_KEY}`
- **Response:** `DocumentCountResponse`
  ```json
  {
    "tos_count": 10,
    "pp_count": 15,
    "total_count": 25,
    "last_updated": "2025-05-08T12:00:00"
  }
  ```

### 3. Get Document by ID

- **Method:** GET
- **Path:** `/api/v1/documents/{document_id}`
- **Headers:** `X-API-Key: {API_KEY}`
- **Path Parameters:**
  - `document_id` (string): ID of the document
- **Response:** `Document` model
  ```json
  {
    "id": "abc123",
    "url": "example.com",
    "document_type": "tos",
    "company_name": "Example Corp",
    "logo_url": "https://...",
    "views": 43,                   // Incremented automatically
    "created_at": "2025-05-07T10:00:00",
    "updated_at": "2025-05-08T12:35:00",
    "raw_text": "...",
    "one_sentence_summary": "...",
    "hundred_word_summary": "...",
    "word_frequencies": [...],
    "text_mining_metrics": {...}
  }
  ```

### 4. Delete Document

- **Method:** DELETE
- **Path:** `/api/v1/documents/{document_id}`
- **Headers:** `X-API-Key: {API_KEY}`
- **Response:**
  ```json
  { "success": true, "message": "Document deleted successfully" }
  ```

### 5. Update Company Name

- **Method:** PATCH
- **Path:** `/api/v1/documents/{document_id}/company-name`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  { "company_name": "New Name" }
  ```
- **Response:** Updated `Document` model (same format as GET Document by ID)

### 6. Force Recount Stats

- **Method:** POST
- **Path:** `/api/v1/recount-stats`
- **Headers:** `X-API-Key: {API_KEY}`
- **Response:**
  ```json
  {
    "success": true,
    "message": "Stats recounted successfully",
    "counts": {
      "tos_count": 10,
      "pp_count": 15,
      "total_count": 25
    },
    "last_updated": "2025-05-08T12:00:00",
    "timestamp": "2025-05-08T12:01:00"
  }
  ```

<<<<<<< Updated upstream

- **Method:** POST
=======
### 7. Sync Documents from Queue

- **Method:** POST
- **Path:** `/api/v1/sync-documents`
>>>>>>> Stashed changes
- **Headers:** `X-API-Key: {API_KEY}`
- **Response:**
  ```json
  {
    "success": true,
    "indexed": 25,
    "failed": 0,
    "total": 25,
    "timestamp": "2025-05-08T12:02:00"
  }
  ```

### 8. Admin Search All Submissions

- **Method:** GET
- **Path:** `/api/v1/admin/search-all-submissions`
- **Headers:**
  - `X-API-Key: {API_KEY}`
- **Query Parameters:**
  - `query` (optional, default: ""): Search text for URLs (empty to list all)
  - `user_email` (optional): Filter by specific user email
  - `page` (optional, default: 1): Page number (>=1)
  - `size` (optional, default: 6): Items per page (allowed values: 6, 9, 12, 15)
  - `sort_order` (optional, default: "desc"): Sort order ("asc" or "desc")
  - `document_type` (optional): Filter by type ("tos" or "pp")
  - `status` (optional): Filter by status (one of: initialized, processing, success, failed)
  - `role` (required): Must be "admin" to access this endpoint
- **Possible Status Values:**
  - `initialized`: Submission created, processing not yet started
  - `processing`: Crawling or analysis in progress
  - `success`: Crawling and analysis completed successfully
  - `failed`: Crawling or analysis failed (check `error_message` for details)
- **Response:**
  ```json
  {
    "items": [
      {
        "id": "submission_id",
        "url": "https://example.com",
        "document_type": "tos",
        "status": "success",
        "document_id": "doc_id",
        "error_message": null,
        "created_at": "2024-03-20T10:00:00Z",
        "updated_at": "2024-03-20T10:01:00Z",
        "user_email": "user@example.com"
      }
    ],
    "total": 100,
    "page": 1,
    "size": 6,
    "pages": 17,
    "error_status": false,
    "error_message": null
  }
  ```

### 9. List User Submissions

- **Method:** GET
- **Path:** `/api/v1/submissions`
- **Headers:**
  - `X-API-Key: {API_KEY}`
- **Query Parameters:**
  - `user_email` (required): User's email to filter submissions
  - `page` (optional, default: 1): Page number (>=1)
  - `size` (optional, default: 6): Items per page (allowed values: 6, 9, 12, 15)
  - `sort_order` (optional, default: "desc"): Sort order ("asc" or "desc")
  - `search_url` (optional): Filter by base URL
  - `status` (optional): Filter by submission status (one of: initialized, processing, success, failed)
- **Possible Status Values:**
  - `initialized`: Submission created, processing not yet started
  - `processing`: Crawling or analysis in progress
  - `success`: Crawling and analysis completed successfully
  - `failed`: Crawling or analysis failed (check `error_message` for details)
- **Response:** Same format as Search Submissions response

### 10. Search Submissions

- **Method:** GET
- **Path:** `/api/v1/search-submissions`
- **Headers:**
  - `X-API-Key: {API_KEY}`
- **Query Parameters:**
  - `query` (string, optional, default: ""): Search query for URLs (empty string to list all)
  - `user_email` (string, required): User's email to filter submissions
  - `page` (integer, optional, default: 1): Page number (>=1)
  - `size` (integer, optional, default: 6): Items per page (allowed values: 6, 9, 12, 15)
  - `sort_order` (string, optional, default: "desc"): Sort order ("asc" or "desc")
  - `document_type` (string, optional): Filter by document type ("tos" or "pp")
  - `status` (string, optional): Filter by submission status (one of: initialized, processing, success, failed)
- **Response:** `PaginatedSubmissionsResponse`
  ```json
  {
    "items": [
      {
        "id": "submission_id",
        "url": "https://example.com",
        "document_type": "tos",
        "status": "success",
        "document_id": "doc_id",
        "error_message": null,
        "created_at": "2024-03-20T10:00:00Z",
        "updated_at": "2024-03-20T10:01:00Z",
        "user_email": "user@example.com"
      }
    ],
    "total": 10,
    "page": 1,
    "size": 6,
    "pages": 2,
    "error_status": false,
    "error_message": null
  }
  ```

### 11. Delete Submission

- **Method:** DELETE
- **Path:** `/api/v1/submissions/{submission_id}`
- **Headers:**
  - `X-API-Key: {API_KEY}`
- **Path Parameters:**
  - `submission_id` (string): ID of the submission to delete
- **Query Parameters:**
  - `user_email` (required): User's email to validate ownership
  - `role` (optional): If set to "admin", allows deleting any submission
- **cURL Example:**
  ```bash
  curl -X DELETE "https://api.example.com/api/v1/submissions/submission_123?user_email=user@example.com" \
    -H "X-API-Key: your_api_key"
  ```
- **Response:**
  ```json
  {
    "success": true,
    "message": "Submission deleted successfully"
  }
  ```

### 12. Cancel Submission

- **Method:** POST
- **Path:** `/api/v1/submissions/{submission_id}/cancel`
- **Headers:**
  - `X-API-Key: {API_KEY}`
- **Path Parameters:**
  - `submission_id` (string): ID of the submission to cancel
- **Query Parameters:**
  - `user_email` (required): User's email to validate ownership
- **Response:**
  ```json
  {
    "success": true,
    "message": "Submission canceled successfully"
  }
  ```

### 13. Trigger Reprocessing

- **Method:** POST
- **Path:** `/api/v1/submissions/{submission_id}/reprocess`
- **Headers:**
  - `X-API-Key: {API_KEY}`
- **Path Parameters:**
  - `submission_id` (string): ID of the submission to reprocess
- **Query Parameters:**
  - `user_email` (required): User's email to validate ownership
- **Response:**
  ```json
  {
    "success": true,
    "message": "Submission reprocessing triggered successfully"
  }
  ```

### 14. Crawl Terms of Service

- **Method:** POST
- **Path:** `/api/v1/crawl-tos`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "url": "https://example.com",
    "user_email": "user@example.com"
  }
  ```
- **Response:** `CrawlTosResponse`

### 15. Crawl Privacy Policy

- **Method:** POST
- **Path:** `/api/v1/crawl-pp`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "url": "https://example.com",
    "user_email": "user@example.com"
  }
  ```
- **Response:** `CrawlPrivacyResponse` (similar format to Crawl ToS response)

### 16. Reanalyze Terms of Service

- **Method:** POST
- **Path:** `/api/v1/reanalyze-tos`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "document_id": "doc123",
    "user_email": "user@example.com"
  }
  ```
- **Response:** `ReanalyzeTosResponse` (similar format to Crawl ToS response)

### 17. Reanalyze Privacy Policy

- **Method:** POST
- **Path:** `/api/v1/reanalyze-pp`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "document_id": "doc123",
    "user_email": "user@example.com"
  }
  ```
- **Response:** `ReanalyzePrivacyResponse` (similar format to Crawl ToS response)

### 18. Generate Summary

If no `provider` is supplied, the service uses the `SUMMARY_PROVIDER` value from the environment. Models whose names start with `glm` automatically route to Z.AI; models containing `gemini` route to the Google Gemini API.

If no `provider` is supplied, the service uses the `SUMMARY_PROVIDER` value from the environment. Models whose names start with `glm` automatically route to Z.AI; models containing `gemini` route to the Google Gemini API.

- **Method:** POST
- **Path:** `/api/v1/summary`
- **Headers:**
  - `X-API-Key: {API_KEY}`
  - `Content-Type: application/json`
- **Request Body:**
  ```json
  {
    "text": "Long document text...",
    "url": "https://example.com",
    "document_type": "tos", // "tos" or "pp"
    "company_name": "Example Corp", // Optional
    "provider": "zai", // Optional: "google" or "zai"
    "model": "GLM-4.5-Air" // Optional model override
  }
  ```
- **Response:**
  ```json
  {
    "url": "https://example.com",
    "document_type": "tos",
    "provider": "zai",
    "model": "GLM-4.5-Air",
    "success": true,
    "message": "Summary generated successfully",
    "one_sentence_summary": "...",
    "hundred_word_summary": "..."
  }
  ```

### Notes on Pagination

- All submissions endpoints use consistent pagination with page sizes of 6, 9, 12, or 15 items
- Default page size is 6 items
- Results are sorted by creation date (newest first by default)
- The response includes total count and total pages for pagination UI
- Use the `page` parameter to navigate through results

## Database Configuration

### Neon (PostgreSQL)

Set `NEON_DATABASE_URL` to your Neon connection string. The application uses Postgres for document, submission, and stats storage, including full-text search.
<<<<<<< Updated upstream

=======
>>>>>>> Stashed changes

## Troubleshooting

### Common Issues

- **Connection errors** – Confirm network/firewall rules allow access to your Neon endpoint.
- **Search not working** – Verify migrations have populated the Postgres tables and that the full-text indexes exist.

### Getting Help

Create an issue in the GitHub repository for assistance.

## Development Workflow

1. Create a feature branch

   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make changes and test locally

3. Commit with standardized messages

   ```bash
   git commit -m "Feat(component): add new feature"
   # or
   git commit -m "Fix(search): fix search issue"
   ```

4. Push changes and create pull request

## License

This project is proprietary software. All rights reserved.
