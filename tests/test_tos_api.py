import pytest
from fastapi.testclient import TestClient
from app.main import app
from unittest.mock import patch, MagicMock, AsyncMock

client = TestClient(app)


def test_find_tos_for_github():
    """Test the ToS endpoint with GitHub as an example."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://github.com"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "github.com" in data["url"].lower()
    assert data["success"] is True
    assert "tos_url" in data
    assert "github.com" in data["tos_url"].lower()
    assert data["method_used"] == "standard"  # Should use standard method for GitHub


def test_find_tos_invalid_url():
    """Test with an invalid URL."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "not-a-url"}
    )
    assert response.status_code == 200  # Should return 200 with error in message
    data = response.json()
    assert data["success"] is False
    assert "message" in data
    assert "Error" in data["message"]


def test_find_tos_without_protocol():
    """Test the ToS endpoint with a URL that doesn't have a protocol."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "github.com"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "github.com" in data["url"].lower()
    assert data["success"] is True
    assert "tos_url" in data
    assert "github.com" in data["tos_url"].lower()


def test_find_tos_with_trailing_slash():
    """Test the ToS endpoint with a URL that has a trailing slash."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "github.com/"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "tos_url" in data


def test_find_tos_with_www():
    """Test the ToS endpoint with a URL that explicitly has www."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "www.github.com"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "tos_url" in data


def test_find_tos_without_www():
    """Test the ToS endpoint with a URL that doesn't have www."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "nodejs.org"}  # nodejs.org doesn't use www
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "tos_url" in data
    assert "nodejs.org" in data["tos_url"].lower()


def test_error_handling_in_response():
    """Test that errors are properly returned in the response."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://nonexistent-domain-that-doesnt-exist123456789.com"}
    )
    assert response.status_code == 200  # Should return 200 even with error
    data = response.json()
    assert data["success"] is False
    assert "message" in data
    assert "Error" in data["message"]


def test_find_tos_chatgpt():
    """Test the ToS endpoint with ChatGPT as an example."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://chatgpt.com"}
    )
    assert response.status_code == 200
    data = response.json()
    
    # We only verify that we get some response, not specific domains
    if data["success"]:
        assert data["tos_url"] is not None
    else:
        # Even if it fails, we should have a meaningful message
        assert len(data["message"]) > 0
    
    # Test with www and different format
    response = client.post(
        "/api/v1/tos",
        json={"url": "www.chatgpt.com"}
    )
    assert response.status_code == 200
    data = response.json() 


def test_find_tos_facebook():
    """Test the ToS endpoint with Facebook, which typically blocks scraping."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://facebook.com"}
    )
    assert response.status_code == 200
    data = response.json()
    
    # We're just testing that we get a response, not making assumptions about success
    if data["success"]:
        assert data["tos_url"] is not None
    else:
        assert "message" in data
        # The message should explain what went wrong without relying on pre-stored knowledge
        assert len(data["message"]) > 0 


def test_t3_chat_case():
    """Test the ToS endpoint with t3.chat, which redirects to a chat page."""
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://t3.chat/"}
    )
    assert response.status_code == 200
    data = response.json()
    
    # We should find a ToS link for t3.chat
    assert data["success"] is True
    assert data["tos_url"] is not None
    assert "t3.chat" in data["tos_url"] or "Terms" in data["message"]


@patch('app.api.v1.endpoints.tos.standard_tos_finder')
@patch('app.api.v1.endpoints.tos.playwright_tos_finder')
async def test_tos_fallback_to_playwright(mock_playwright_finder, mock_standard_finder):
    """Test that the API falls back to Playwright when standard scraping fails."""
    # Mock the standard finder to fail
    mock_standard_finder.return_value = AsyncMock(return_value=MagicMock(
        success=False, 
        message="No ToS found with standard method",
        method_used="standard_failed"
    ))()
    
    # Mock the Playwright finder to succeed
    mock_playwright_finder.return_value = AsyncMock(return_value=MagicMock(
        success=True,
        tos_url="https://example.com/terms",
        url="https://example.com",
        message="ToS found with Playwright",
        method_used="playwright"
    ))()
    
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://example.com"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["tos_url"] == "https://example.com/terms"
    assert data["method_used"] == "playwright"
    
    # Verify both methods were called
    mock_standard_finder.assert_called_once()
    mock_playwright_finder.assert_called_once()


@patch('app.api.v1.endpoints.tos.standard_tos_finder')
@patch('app.api.v1.endpoints.tos.playwright_tos_finder')
async def test_tos_both_methods_fail(mock_playwright_finder, mock_standard_finder):
    """Test behavior when both standard scraping and Playwright fail."""
    # Mock both finders to fail
    mock_standard_finder.return_value = AsyncMock(return_value=MagicMock(
        success=False, 
        message="No ToS found with standard method",
        method_used="standard_failed"
    ))()
    
    mock_playwright_finder.return_value = AsyncMock(return_value=MagicMock(
        success=False,
        tos_url=None,
        url="https://example.com",
        message="No ToS found even with Playwright",
        method_used="playwright_failed"
    ))()
    
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://example.com"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["tos_url"] is None
    # Should indicate both methods were tried
    assert "both" in data["method_used"]
    
    # Verify both methods were called
    mock_standard_finder.assert_called_once()
    mock_playwright_finder.assert_called_once()


@patch('app.api.v1.endpoints.tos.playwright_tos_finder')
@patch('app.api.v1.endpoints.tos.standard_tos_finder')
async def test_tos_standard_method_succeeds(mock_standard_finder, mock_playwright_finder):
    """Test that Playwright is not called when standard scraping succeeds."""
    # Mock the standard finder to succeed
    mock_standard_finder.return_value = AsyncMock(return_value=MagicMock(
        success=True, 
        tos_url="https://example.com/terms",
        url="https://example.com",
        message="ToS found with standard method",
        method_used="standard"
    ))()
    
    response = client.post(
        "/api/v1/tos",
        json={"url": "https://example.com"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["tos_url"] == "https://example.com/terms"
    assert data["method_used"] == "standard"
    
    # Verify standard method was called but Playwright was not
    mock_standard_finder.assert_called_once()
    mock_playwright_finder.assert_not_called()


@patch('app.api.v1.endpoints.tos.playwright.async_api')
async def test_playwright_interaction(mock_playwright_api):
    """Test that Playwright tries clicking consent buttons when needed."""
    # This is a more complex test that mocks the entire Playwright chain
    # Create mock objects for the Playwright API chain
    mock_browser = AsyncMock()
    mock_context = AsyncMock()
    mock_page = AsyncMock()
    mock_button = AsyncMock()
    mock_link = AsyncMock()
    
    # Configure the page content before and after clicking
    mock_page.content.side_effect = [
        "<html><body>No terms link yet</body></html>",  # Before clicking
        "<html><body><a href='/terms'>Terms</a></body></html>"  # After clicking
    ]
    
    # Configure the URL and link attributes
    mock_page.url = "https://example.com"
    mock_link.get_attribute.return_value = "/terms"
    
    # Set up the chain of mock returns
    mock_playwright = AsyncMock()
    mock_playwright_api.return_value = mock_playwright
    mock_playwright.__aenter__.return_value = mock_playwright
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    
    # Configure the mocks for button clicking
    mock_page.query_selector_all.side_effect = [
        [],  # No direct links found
        [mock_button]  # One button found
    ]
    
    # Run the test directly on the playwright_tos_finder function
    from app.api.v1.endpoints.tos import playwright_tos_finder
    
    result = await playwright_tos_finder("https://example.com")
    
    # Verify the result
    assert result.success is True
    assert result.tos_url is not None
    assert result.method_used == "playwright"
    
    # Verify Playwright was used correctly
    mock_playwright.chromium.launch.assert_called_once()
    mock_page.goto.assert_called_once()
    # Should have tried to click the consent button
    mock_button.click.assert_called_once() 