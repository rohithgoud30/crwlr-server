import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import Response
from time import time
import pytest_asyncio

from app.api.v1.endpoints.extract import (
    extract_text, sanitize_url, get_from_cache, add_to_cache, 
    extract_standard_html, extract_pdf, extract_with_playwright,
    extract_content_from_soup, CACHE, CACHE_TTL
)
from app.models.extract import ExtractRequest, ExtractResponse

class MockResponse:
    def __init__(self, text="", status_code=200, content=b"", headers=None):
        self.text = text
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error: {self.status_code}")

@pytest.fixture
def reset_cache():
    """Reset the cache before and after tests"""
    CACHE.clear()
    yield
    CACHE.clear()

@pytest.mark.asyncio
async def test_sanitize_url():
    """Test URL sanitization logic"""
    # Valid URLs
    assert sanitize_url("example.com") == "https://example.com"
    assert sanitize_url("http://example.com") == "http://example.com"
    assert sanitize_url("https://example.com") == "https://example.com"
    
    # Invalid URLs
    assert sanitize_url("") == ""
    assert sanitize_url("invalid") == ""
    assert sanitize_url("https://https://example.com") == ""

@pytest.mark.asyncio
@pytest.mark.usefixtures("reset_cache")
async def test_cache_functionality():
    """Test caching functionality"""
    test_url = "https://example.com/test"
    test_data = {"key": "value", "success": True}
    
    # Initially empty cache
    assert get_from_cache(test_url) is None
    
    # Add to cache
    add_to_cache(test_url, test_data)
    
    # Check if added to cache correctly
    cached_data = get_from_cache(test_url)
    assert cached_data is not None
    assert cached_data["key"] == "value"
    assert cached_data["success"] is True

@pytest.mark.asyncio
@pytest.mark.usefixtures("reset_cache")
async def test_extract_text_with_caching():
    """Test that extract_text uses and updates the cache properly"""
    test_url = "https://example.com"
    
    # Create a mock response for successful extraction
    mock_extract_response = ExtractResponse(
        url=test_url,
        document_type="tos",
        text="This is test content",
        success=True,
        message="Success",
        method_used="standard"
    )
    
    # Mock the extract_standard_html function
    with patch("app.api.v1.endpoints.extract.extract_standard_html", 
               new_callable=AsyncMock) as mock_extract:
        mock_extract.return_value = mock_extract_response
        
        # First call - should call extract_standard_html
        request = ExtractRequest(url=test_url)
        response = Response()
        
        result1 = await extract_text(request, response)
        assert result1.success is True
        assert result1.text == "This is test content"
        assert mock_extract.called
        
        # Reset the mock
        mock_extract.reset_mock()
        
        # Second call - should use cache
        result2 = await extract_text(request, response)
        assert result2.success is True
        assert result2.text == "This is test content"
        assert not mock_extract.called  # Extract function should not be called again

@pytest.mark.asyncio
async def test_extract_content_from_soup():
    """Test HTML content extraction"""
    from bs4 import BeautifulSoup
    
    # Simple HTML with article tag
    html = """
    <html>
        <head><title>Test</title></head>
        <body>
            <header>Header content</header>
            <article>
                <h1>Main Content</h1>
                <p>This is the important content that should be extracted.</p>
            </article>
            <footer>Footer content</footer>
        </body>
    </html>
    """
    
    soup = BeautifulSoup(html, 'html.parser')
    extracted = extract_content_from_soup(soup)
    
    # Check that we extracted the article content
    assert "Main Content" in extracted
    assert "This is the important content" in extracted
    
    # Check that we excluded header/footer
    assert "Header content" not in extracted
    assert "Footer content" not in extracted

@pytest.mark.asyncio
async def test_extraction_strategy_selection():
    """Test that the right extraction strategy is selected based on URL"""
    pdf_url = "https://example.com/document.pdf"
    html_url = "https://example.com/page.html"
    
    # Mock successful responses
    mock_pdf_response = ExtractResponse(
        url=pdf_url, document_type="tos", text="PDF content", 
        success=True, message="Success", method_used="pdf"
    )
    
    mock_html_response = ExtractResponse(
        url=html_url, document_type="tos", text="HTML content", 
        success=True, message="Success", method_used="standard"
    )
    
    # Mock extraction functions
    with patch("app.api.v1.endpoints.extract.extract_pdf", 
               new_callable=AsyncMock) as mock_pdf_extract, \
         patch("app.api.v1.endpoints.extract.extract_standard_html", 
               new_callable=AsyncMock) as mock_html_extract, \
         patch("app.api.v1.endpoints.extract.find_tos", 
               new_callable=AsyncMock) as mock_find_tos:
        
        # Set up the mocks
        mock_pdf_extract.return_value = mock_pdf_response
        mock_html_extract.return_value = mock_html_response
        
        # For PDF URL
        pdf_request = ExtractRequest(url=pdf_url)
        pdf_result = await extract_text(pdf_request, Response())
        
        # PDF extraction should be called for PDF URL
        assert mock_pdf_extract.called
        assert pdf_result.method_used == "pdf"
        
        # Reset mocks
        mock_pdf_extract.reset_mock()
        mock_html_extract.reset_mock()
        
        # For HTML URL
        html_request = ExtractRequest(url=html_url)
        html_result = await extract_text(html_request, Response())
        
        # Standard HTML extraction should be called for HTML URL
        assert mock_html_extract.called
        assert html_result.method_used == "standard"

@pytest.mark.asyncio
async def test_pdf_extraction():
    """Test PDF extraction function"""
    pdf_url = "https://example.com/document.pdf"
    
    # Mock a PDF response
    mock_requests_response = MockResponse(
        content=b"%PDF-1.5\nfake pdf content",
        headers={"Content-Type": "application/pdf"}
    )
    
    with patch("app.api.v1.endpoints.extract.requests.get", 
               return_value=mock_requests_response), \
         patch("app.api.v1.endpoints.extract.extract_text_from_pdf", 
               return_value="Extracted PDF text"):
        
        result = await extract_pdf(pdf_url, "tos", pdf_url)
        
        assert result.success is True
        assert result.text == "Extracted PDF text"
        assert result.method_used == "pdf"

@pytest.mark.asyncio
async def test_standard_html_extraction():
    """Test standard HTML extraction function"""
    html_url = "https://example.com/page.html"
    
    # Mock an HTML response
    mock_requests_response = MockResponse(
        text="<html><body><main>Content to extract</main></body></html>",
        headers={"Content-Type": "text/html"}
    )
    
    with patch("app.api.v1.endpoints.extract.requests.get", 
               return_value=mock_requests_response), \
         patch("app.api.v1.endpoints.extract.extract_content_from_soup", 
               return_value="Content to extract"):
        
        result = await extract_standard_html(html_url, "tos", html_url)
        
        assert result.success is True
        assert result.text == "Content to extract"
        assert result.method_used == "standard"

@pytest.mark.asyncio
async def test_extraction_fallbacks():
    """Test fallbacks when primary extraction method fails"""
    html_url = "https://example.com/page.html"
    
    # Set up mocks where standard extraction fails but playwright succeeds
    with patch("app.api.v1.endpoints.extract.extract_standard_html", 
               new_callable=AsyncMock) as mock_standard_extract, \
         patch("app.api.v1.endpoints.extract.extract_with_playwright", 
               new_callable=AsyncMock) as mock_playwright_extract, \
         patch("app.api.v1.endpoints.extract.find_tos", 
               new_callable=AsyncMock) as mock_find_tos:
        
        # Standard extraction raises exception
        mock_standard_extract.side_effect = Exception("Standard extraction failed")
        
        # Playwright extraction succeeds
        mock_playwright_extract.return_value = ExtractResponse(
            url=html_url, document_type="tos", text="JS-rendered content", 
            success=True, message="Success", method_used="playwright"
        )
        
        # Execute extraction
        request = ExtractRequest(url=html_url)
        result = await extract_text(request, Response())
        
        # Both methods should be called in order
        assert mock_standard_extract.called
        assert mock_playwright_extract.called
        
        # Should succeed with the fallback method
        assert result.success is True
        assert result.text == "JS-rendered content"
        assert result.method_used == "playwright"

@pytest.mark.asyncio
async def test_extraction_timeouts():
    """Test that timeouts are properly set"""
    from app.api.v1.endpoints.extract import STANDARD_TIMEOUT, PLAYWRIGHT_TIMEOUT
    
    # Verify timeout values are reduced from original
    assert STANDARD_TIMEOUT == 15  # Reduced from 30
    assert PLAYWRIGHT_TIMEOUT == 20000  # Reduced from 30000
    
    # Test that timeouts are passed correctly to requests
    with patch("app.api.v1.endpoints.extract.requests.get") as mock_get:
        mock_get.return_value = MockResponse(
            text="<html><body>Test</body></html>",
            headers={"Content-Type": "text/html"}
        )
        
        html_url = "https://example.com/page.html"
        try:
            await extract_standard_html(html_url, "tos", html_url)
        except:
            pass
        
        # Verify timeout was passed correctly
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert "timeout" in kwargs
        assert kwargs["timeout"] == STANDARD_TIMEOUT 