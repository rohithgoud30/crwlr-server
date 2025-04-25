# Company Information API

This document outlines the Company Information API endpoint which extracts company name and logo URL from websites.

## Endpoint

- **URL**: `POST /api/v1/extract-company-info`
- **Authentication**: Required - API key via `X-API-Key` header

## Request Format

The request should be in JSON format with the following structure:

| Parameter | Type   | Required | Description                                                |
| --------- | ------ | -------- | ---------------------------------------------------------- |
| url       | string | Yes      | The URL of the website to extract company information from |

Example:

```json
{
  "url": "https://example.com"
}
```

## Response Format

The response is in JSON format with the following structure:

| Field        | Type    | Description                                     |
| ------------ | ------- | ----------------------------------------------- |
| url          | string  | The original URL provided in the request        |
| company_name | string  | The extracted company name                      |
| logo_url     | string  | The URL of the company logo                     |
| success      | boolean | Indicates whether the extraction was successful |
| message      | string  | A message describing the result or error        |

Example successful response:

```json
{
  "url": "https://example.com",
  "company_name": "Example Company",
  "logo_url": "https://example.com/logo.png",
  "success": true,
  "message": "Successfully extracted company information with BeautifulSoup"
}
```

## Company Name Extraction

The API employs multiple methods to extract the company name:

1. Extracts from the page title tag
2. Cleans common suffixes like "- Home" or "| Official Website"
3. If the domain name matches part of the title, uses the title
4. Otherwise, limits to the first 50 characters of the title
5. Falls back to capitalizing the domain name if no title is found

## Logo URL Extraction

The API uses a cascading approach to find the best company logo:

1. Checks structured data (JSON-LD) for Organization logo
2. Looks for meta tags with 'logo' in the property/name
3. Checks for OpenGraph image
4. Searches for logo images in the header/navigation
5. Uses common CSS selectors for logos
6. Falls back to favicon
7. If all else fails, uses Google's favicon service

## Default Values

When extraction fails:

- For company name: Capitalizes the domain name (e.g., "example.com" → "Example")
- For logo URL: Uses the placeholder image `/placeholder.svg?height=48&width=48`

## Error Handling

The API is designed to be robust and will:

1. Handle invalid or malformed URLs
2. Validate and sanitize URLs before processing
3. Verify the existence of extracted logo URLs
4. Fall back to default values when extraction fails
5. Return detailed error messages in the response

## Example Usage

### cURL

```bash
curl -X 'POST' \
  'https://api.example.com/api/v1/extract-company-info' \
  -H 'accept: application/json' \
  -H 'X-API-Key: your_api_key_here' \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com"}'
```

### Python

```python
import requests
import json

url = "https://api.example.com/api/v1/extract-company-info"
headers = {
    "X-API-Key": "your_api_key_here",
    "Content-Type": "application/json"
}
data = {
    "url": "https://example.com"
}

response = requests.post(url, headers=headers, json=data)
print(json.dumps(response.json(), indent=2))
```

### JavaScript

```javascript
const fetchCompanyInfo = async () => {
  const response = await fetch(
    'https://api.example.com/api/v1/extract-company-info',
    {
      method: 'POST',
      headers: {
        'X-API-Key': 'your_api_key_here',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        url: 'https://example.com',
      }),
    }
  )

  const data = await response.json()
  console.log(data)
}

fetchCompanyInfo()
```

## Integration with Document Processing

This endpoint complements the document extraction system by providing company metadata that can be associated with Terms of Service and Privacy Policy documents. The extracted company name and logo URL can be stored alongside document content for a more complete representation of the document source.
