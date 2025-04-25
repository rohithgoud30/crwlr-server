import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
import asyncio
from bs4 import BeautifulSoup

from app.main import app
from app.api.v1.endpoints.company_info import extract_company_info, CompanyInfoRequest

client = TestClient(app)

# Sample HTML for testing
SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Example Company - Home</title>
    <meta property="og:image" content="https://example.com/logo.png">
    <link rel="icon" href="/favicon.ico">
</head>
<body>
    <header>
        <img src="/images/logo.png" alt="Example Logo" class="logo">
    </header>
    <main>
        <h1>Welcome to Example Company</h1>
    </main>
    <footer>
        <p>© 2023 Example Company</p>
    </footer>
</body>
</html>
"""

@pytest.mark.asyncio
async def test_extract_company_info_success():
    """Test successful company info extraction."""
    with patch('app.api.v1.endpoints.company_info.requests.get') as mock_get, \
         patch('app.api.v1.endpoints.company_info.requests.head') as mock_head:
        
        # Mock response for GET request
        mock_response = MagicMock()
        mock_response.text = SAMPLE_HTML
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        # Mock response for HEAD request (logo validation)
        mock_head_response = MagicMock()
        mock_head_response.status_code = 200
        mock_head.return_value = mock_head_response
        
        company_name, logo_url, success, message = await extract_company_info("https://example.com")
        
        assert success is True
        assert "Example Company" in company_name
        assert logo_url is not None
        assert message == "Successfully extracted company information"

@pytest.mark.asyncio
async def test_extract_company_info_fallback():
    """Test fallback to default values when extraction fails."""
    with patch('app.api.v1.endpoints.company_info.requests.get') as mock_get:
        # Mock GET request to raise an exception
        mock_get.side_effect = Exception("Connection error")
        
        company_name, logo_url, success, message = await extract_company_info("https://example.com")
        
        assert success is False
        assert company_name == "Example"
        assert logo_url == "/placeholder.svg?height=48&width=48"
        assert "Error" in message

@pytest.mark.asyncio
async def test_extract_company_info_no_title():
    """Test extraction when no title tag is present."""
    with patch('app.api.v1.endpoints.company_info.requests.get') as mock_get, \
         patch('app.api.v1.endpoints.company_info.requests.head') as mock_head:
        
        # HTML without title
        html_no_title = """
        <!DOCTYPE html>
        <html><head></head><body><h1>Example</h1></body></html>
        """
        
        # Mock response
        mock_response = MagicMock()
        mock_response.text = html_no_title
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        # Mock head request
        mock_head_response = MagicMock()
        mock_head_response.status_code = 200
        mock_head.return_value = mock_head_response
        
        company_name, logo_url, success, message = await extract_company_info("https://example.com")
        
        assert success is True
        assert company_name == "Example"  # Default from domain
        assert logo_url is not None
        assert message == "Successfully extracted company information"

@pytest.mark.asyncio
async def test_extract_company_info_logo_methods():
    """Test different logo extraction methods."""
    with patch('app.api.v1.endpoints.company_info.requests.get') as mock_get, \
         patch('app.api.v1.endpoints.company_info.requests.head') as mock_head:
        
        # HTML with different logo elements
        html_with_logos = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Test Company</title>
            <script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","logo":"https://test.com/schema-logo.png"}</script>
        </head>
        <body>
            <img class="logo" src="/site-logo.png">
        </body>
        </html>
        """
        
        # Mock response
        mock_response = MagicMock()
        mock_response.text = html_with_logos
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        # Mock head request
        mock_head_response = MagicMock()
        mock_head_response.status_code = 200
        mock_head.return_value = mock_head_response
        
        company_name, logo_url, success, message = await extract_company_info("https://test.com")
        
        assert success is True
        assert company_name == "Test Company"
        # Should find either schema.org logo or CSS-based logo
        assert "logo.png" in logo_url
        assert message == "Successfully extracted company information"

def test_api_endpoint_auth():
    """Test that the endpoint requires authentication."""
    response = client.post("/api/v1/extract-company-info", json={"url": "https://example.com"})
    assert response.status_code in (401, 403)  # Should be unauthorized without API key

# Integration test - requires API key, commented out for CI pipeline
"""
def test_api_endpoint_real():
    # Requires valid API key in X-API-Key header
    api_key = "your_test_api_key"  # Replace with a valid test API key
    response = client.post(
        "/api/v1/extract-company-info", 
        json={"url": "https://www.python.org"}, 
        headers={"X-API-Key": api_key}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "Python" in data["company_name"]
    assert "logo_url" in data
""" 