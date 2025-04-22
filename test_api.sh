#!/bin/bash

# Get API key from environment or use default
API_KEY=${API_KEY:-"crwlr-dev-api-key"}
BASE_URL=${BASE_URL:-"http://localhost:8000"}

# Echo starting message
echo "🧪 Testing CRWLR API 🧪"
echo "====================="
echo "API Key: $API_KEY"
echo "Base URL: $BASE_URL"
echo

# Test the API key-free endpoint
echo "1. Testing non-authenticated endpoint:"
curl -s "$BASE_URL/api/v1/test" | jq || echo "Error: curl request failed"
echo

# Test with API key
echo "2. Testing authenticated endpoint with API key:"
curl -s -H "X-API-Key: $API_KEY" "$BASE_URL/api/v1/tos" -d '{"url": "facebook.com"}' -H "Content-Type: application/json" | jq || echo "Error: curl request failed"
echo

# Try without API key (should fail)
echo "3. Testing authenticated endpoint WITHOUT API key (should fail):"
curl -s "$BASE_URL/api/v1/tos" -d '{"url": "facebook.com"}' -H "Content-Type: application/json" | jq || echo "Error: curl request failed"
echo 