#!/bin/bash

# Default API key if not provided
DEFAULT_API_KEY="crwlr-dev-api-key"

# Get the API key as an argument or use the default
API_KEY=${1:-$DEFAULT_API_KEY}

# Export the API key
export API_KEY=$API_KEY

# Print info
echo "✅ API_KEY is now set in your environment!"
echo "API_KEY=$API_KEY"
echo
echo "Run your application with this API key by running:"
echo "source ./set_api_key.sh && python -m uvicorn app.main:app --reload --port 8000"
echo
echo "Or test with curl:"
echo "curl -H \"X-API-Key: $API_KEY\" http://localhost:8000/api/v1/test" 