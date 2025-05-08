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
