# CRWLR Server

API server for CRWLR application.

## Technology Stack

- **FastAPI**: Modern web framework for building APIs
- **Firebase**: Firestore for document storage
- **Typesense**: Fast, typo-tolerant search engine
- **Google Cloud**: Cloud Run for serverless deployment

## Setup Instructions

### Prerequisites

- Python 3.9+
- Docker (for local Typesense setup)
- Firebase account
- Typesense account or self-hosted instance

### Environment Variables

Create a `.env` file with the following configuration:

```
# API Keys
API_KEY=your_api_key_here
GEMINI_API_KEY=your_gemini_api_key

# Environment setting
PROJECT_ID=your_project_id
ENVIRONMENT=development  # or 'production'

# Firebase Configuration
FIREBASE_TYPE=service_account
FIREBASE_PROJECT_ID=your_firebase_project_id
FIREBASE_PRIVATE_KEY_ID=your_private_key_id
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\nYour key here\n-----END PRIVATE KEY-----\n"
FIREBASE_CLIENT_EMAIL=your_client_email@example.com
FIREBASE_CLIENT_ID=your_client_id
FIREBASE_AUTH_URI=https://accounts.google.com/o/oauth2/auth
FIREBASE_TOKEN_URI=https://oauth2.googleapis.com/token
FIREBASE_AUTH_PROVIDER_CERT_URL=https://www.googleapis.com/oauth2/v1/certs
FIREBASE_CLIENT_CERT_URL=your_client_cert_url

# Typesense Configuration
TYPESENSE_HOST=your_typesense_host
TYPESENSE_PORT=443  # 8108 for local
TYPESENSE_PROTOCOL=https  # http for local
TYPESENSE_API_KEY=your_typesense_api_key
```

### Installation

1. Clone the repository

   ```bash
   git clone https://github.com/rohithgoud30/crwlr-server.git
   cd crwlr-server
   ```

2. Create a virtual environment

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install dependencies

   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables (copy `.env.example` to `.env` and update values)

5. Start the server
   ```bash
   python run.py
   ```

## Typesense Integration

This project uses Typesense v28 for fast, typo-tolerant full-text search of documents.

### About Typesense

Typesense is an open source, typo tolerant search engine optimized for instant search experiences. It offers:

- Fast search with automatic typo correction
- Easy setup and configuration
- RESTful API and client libraries
- Horizontal scalability

### Local Development with Typesense

To set up Typesense locally:

```bash
docker run -p 8108:8108 -v /path/to/data:/data typesense/typesense:0.25.0 \
  --data-dir /data \
  --api-key=your_api_key \
  --enable-cors
```

Update your `.env` file with local Typesense settings:

```
TYPESENSE_HOST=localhost
TYPESENSE_PORT=8108
TYPESENSE_PROTOCOL=http
TYPESENSE_API_KEY=your_api_key
```

### Typesense Cloud Setup

1. Create an account on [Typesense Cloud](https://cloud.typesense.org/)
2. Create a new cluster
3. Generate API keys
4. Update your environment variables with the provided credentials

### Typesense Collection Schema

The project defines a schema for document indexing with the following fields:

- `id` (string): Document ID
- `url` (string, infix-searchable): Document URL
- `document_type` (string, facetable): Type of document (tos or pp)
- `company_name` (string, sortable, infix-searchable): Company name
- `views` (int32): View count
- `logo_url` (string): URL to company logo
- `updated_at` (int64, sortable): Last update timestamp

### Search Functionality

The search endpoints use Typesense for:

- Relevance-based searching across company names and URLs
- Prefix and infix matching for partial terms
- Faceted filtering by document type

### Maintenance Endpoints

The API includes endpoints for Typesense maintenance:

- `/api/v1/documents/sync-typesense` - Synchronize all Firebase documents to Typesense
- `/api/v1/documents/clean-typesense` - Reset Typesense collection

## Deployment

### Cloud Run Deployment

To deploy to Google Cloud Run:

```bash
gcloud run deploy crwlr-server \
  --image=gcr.io/PROJECT_ID/crwlr-server \
  --platform=managed \
  --region=us-east4 \
  --allow-unauthenticated \
  --update-env-vars="ENVIRONMENT=production,TYPESENSE_HOST=your_host"
```

### CI/CD Pipeline

The repository includes GitHub Actions workflows for CI/CD pipeline:

- Automated testing
- Docker build
- Cloud Run deployment

## API Documentation

Once running, access the API documentation at:

- http://localhost:8080/docs (local)
- https://your-cloud-run-url/docs (deployed)

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

### 7. Sync All Documents to Typesense

- **Method:** POST
- **Path:** `/api/v1/documents/sync-typesense`
- **Headers:** `X-API-Key: {API_KEY}`
- **Response:**
  ```json
  {
    "success": true,
    "message": "Synchronized 25 documents to Typesense",
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
  - `status` (string, optional): Filter by status
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
    "message": "Submission deleted successfully",
    "submission_id": "submission_123"
  }
  ```
- **Error Responses:**
  ```json
  {
    "success": false,
    "message": "Submission with ID submission_123 not found"
  }
  ```
  ```json
  {
    "success": false,
    "message": "You do not have permission to delete this submission"
  }
  ```

### Notes on Pagination

- All submissions endpoints use consistent pagination with page sizes of 6, 9, 12, or 15 items
- Default page size is 6 items
- Results are sorted by creation date (newest first by default)
- The response includes total count and total pages for pagination UI
- Use the `page` parameter to navigate through results

## Building and Running Locally

### Docker Build

Build a Docker image for the server:

```bash
docker build -t crwlr-server .
```

### Docker Run

Run the container, mapping port 8080 and loading environment variables:

```bash
docker run --env-file .env -p 8080:8080 crwlr-server
```

The API will be available at `http://localhost:8080`.

---

## Firebase Configuration

The application uses Firebase Firestore instead of PostgreSQL/Cloud SQL.

### Setting Up Firebase

1. Create a Firebase project at [https://console.firebase.google.com/](https://console.firebase.google.com/)
2. Enable the Firestore database service
3. Generate a service account key:
   - Go to Project Settings > Service accounts
   - Click "Generate new private key"
   - Save the JSON file securely

### Local Development

Configure Firebase by setting the environment variables listed above.

## Troubleshooting

### Common Issues

1. **Typesense Connection Failures**

   - Verify your Typesense host and API key
   - Check firewall settings
   - Ensure Typesense version compatibility (v28+)

2. **Firebase Authentication Errors**

   - Verify private key format (newlines should be `\n`)
   - Check permissions of service account

3. **Search Not Working**
   - Run the sync endpoint to populate Typesense
   - Verify schema definitions match

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
