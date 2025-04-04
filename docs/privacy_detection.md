# Privacy Policy Detection

This document outlines our privacy policy detection system, which uses multiple fallback approaches to find privacy policies on websites with complex layouts.

## Overview

The privacy policy detection system uses a layered approach with multiple fallback methods:

1. **URL Pattern Detection** - First attempts to identify policies through URL patterns
2. **Common URL Testing** - Tests common privacy policy URL paths
3. **Content Analysis** - Extracts and analyzes links from page content
4. **Hidden Menu Detection** - Identifies links in dropdowns, modals, and other UI components
5. **JavaScript Extraction** - Extracts URLs embedded in JavaScript/JSON
6. **Dynamic Page Rendering** - Uses Playwright to render JavaScript and analyze the rendered page
7. **Interactive Detection** - Simulates clicks on menus and dropdowns to reveal hidden content

## Key Features

### Pattern-based Detection

Our system uses flexible patterns rather than hardcoded domains, making it adaptable to a wide range of websites:

- **Path Patterns** - Detects common URL paths for privacy policies
- **Query Patterns** - Identifies URLs with specific query parameters
- **Multilingual Support** - Recognizes patterns in various languages

### Content Analysis

The system analyzes page content to identify privacy policy links:

- **Link Text Analysis** - Examines link text for policy-related terms
- **Context Analysis** - Considers the link's surrounding elements and parent containers
- **Footer Detection** - Pays special attention to links located in page footers

### Hidden Element Detection

For websites with complex layouts:

- **Menu Toggle Detection** - Identifies elements that might reveal policy links when clicked
- **ARIA Attribute Analysis** - Uses accessibility attributes to find hidden menus
- **Dynamic Content Analysis** - Handles content that's loaded or revealed through JavaScript

### JavaScript Integration

- **Script Content Analysis** - Extracts URLs from JavaScript variables and JSON structures
- **Dynamic Interaction** - Clicks on potential menu toggles to reveal hidden content
- **Full JavaScript Rendering** - Uses headless browser to fully render and interact with pages

## How It Works

1. The system first tries standard approaches:

   - Checking for direct URL matches
   - Testing common privacy policy paths
   - Extracting links from page content

2. If standard approaches fail, it moves to more advanced techniques:

   - Analyzing hidden menus and dropdowns
   - Extracting URLs from JavaScript
   - Using a headless browser to render JavaScript
   - Interacting with the page to reveal hidden content

3. Each detection method assigns a confidence score, allowing the system to prioritize the most likely matches.

## Usage

```python
from app.api.v1.endpoints.privacy import find_privacy_policy

# Find privacy policy for a website
result = await find_privacy_policy("https://example.com")

# Check if successful
if result.success:
    print(f"Privacy policy found at: {result.pp_url}")
    print(f"Method used: {result.method_used}")
else:
    print("No privacy policy found")
```

## API Endpoint

The privacy policy detection is available through a REST API endpoint:

```
POST /api/v1/privacy/find
```

A legacy compatibility endpoint is also available:

```
POST /api/v1/privacy
```

Request body:

```json
{
  "url": "https://example.com"
}
```

Response:

```json
{
  "url": "https://example.com",
  "pp_url": "https://example.com/privacy",
  "success": true,
  "message": "Privacy policy found through context analysis (confidence: 0.8)",
  "method_used": "context_analysis"
}
```

## Limitations and Future Improvements

- Some heavily JavaScript-dependent sites may still be challenging
- Very unusual UI patterns might require additional detection strategies
- Future work includes:
  - Implementing machine learning for content analysis
  - Adding support for language-specific policy detection
  - Improving performance for handling large pages
  - Adding automated checks for policy content validity
  - Further enhancing detection for single-page applications (SPAs)
