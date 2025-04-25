# Company Information API

The Company Information API extracts a company's name and logo URL from a website using BeautifulSoup, with fallbacks to reliable defaults.

## Endpoint

`POST /api/v1/extract-company-info`

## Authentication

Requires API key authentication via the `X-API-Key` header.

## Default Values

- **Default Logo URL**: `/placeholder.svg?height=48&width=48`
- **Default Company Name**: Capitalized domain name (e.g., "example.com" → "Example")

These defaults are used when extraction fails or when the extracted values are invalid.

## Request Format

```json
{
  "url": "https://example.com"
}
```

### Parameters

| Parameter | Type   | Required | Description                                     |
| --------- | ------ | -------- | ----------------------------------------------- |
| url       | string | Yes      | Website URL to extract company information from |

## Response Format

```json
{
  "url": "https://example.com",
  "company_name": "Example Company",
  "logo_url": "https://example.com/logo.png",
  "success": true,
  "message": "Successfully extracted company information"
}
```

### Response Fields

| Field        | Type    | Description                                     |
| ------------ | ------- | ----------------------------------------------- |
| url          | string  | The original URL provided in the request        |
| company_name | string  | Extracted company name                          |
| logo_url     | string  | URL to the company's logo                       |
| success      | boolean | Whether the extraction was successful           |
| message      | string  | Descriptive message about the extraction result |

## Extraction Methods

The endpoint uses multiple methods to extract information:

### Company Name Extraction

1. Extracts from the HTML title tag (with cleanup of common suffixes)
2. Falls back to the domain name if no title is found (e.g., "example.com" → "Example")

### Logo Extraction (in order of priority)

1. Meta tags with "logo" in property/name
2. OpenGraph image (`meta property="og:image"`)
3. Schema.org structured data with logo property
4. Common logo class/id patterns in HTML (e.g., `img.logo`, `.logo img`, etc.)
5. Favicon (`link rel="icon"` or `rel="shortcut icon"`)
6. Google's favicon service as last resort (`https://www.google.com/s2/favicons?domain=example.com&sz=128`)
7. Default placeholder logo: `/placeholder.svg?height=48&width=48`

## Error Handling

If extraction fails, the API falls back to defaults:

- Default company name: Capitalized domain name (e.g., "example.com" → "Example")
- Default logo URL: `/placeholder.svg?height=48&width=48`

## Example Usage

### cURL

```bash
curl -X POST "https://your-api-url/api/v1/extract-company-info" \
     -H "Content-Type: application/json" \
     -H "X-API-Key: your_api_key_here" \
     -d '{"url": "https://www.python.org"}'
```

### Python

```python
import requests

api_url = "https://your-api-url/api/v1/extract-company-info"
headers = {
    "Content-Type": "application/json",
    "X-API-Key": "your_api_key_here"
}
data = {
    "url": "https://www.python.org"
}

response = requests.post(api_url, json=data, headers=headers)
result = response.json()
print(f"Company Name: {result['company_name']}")
print(f"Logo URL: {result['logo_url']}")
```

### JavaScript

```javascript
fetch('https://your-api-url/api/v1/extract-company-info', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-API-Key': 'your_api_key_here',
  },
  body: JSON.stringify({
    url: 'https://www.python.org',
  }),
})
  .then((response) => response.json())
  .then((data) => {
    console.log(`Company Name: ${data.company_name}`)
    console.log(`Logo URL: ${data.logo_url}`)
  })
  .catch((error) => console.error('Error:', error))
```

## Integration with Other Endpoints

This endpoint complements the existing document extraction system:

- The `/crawl-tos` and `/crawl-pp` endpoints now save documents with company names and logos
- Use this endpoint to manually extract company info from any website
- All endpoints use the same default logo URL (`/placeholder.svg?height=48&width=48`) for consistency
