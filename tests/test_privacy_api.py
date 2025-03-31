import pytest
from fastapi.testclient import TestClient
from app.main import app
from unittest.mock import patch, MagicMock, AsyncMock
from app.api.v1.endpoints.privacy import PrivacyResponse

client = TestClient(app)


def test_find_privacy_for_github():
    """Test the /privacy endpoint with GitHub."""
    data = {
        "url": "github.com"
    }
    response = client.post("/api/v1/privacy", json=data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "pp_url" in data
    assert "github.com" in data["pp_url"].lower()


def test_find_privacy_invalid_url():
    """Test with an invalid URL."""
    response = client.post(
        "/api/v1/privacy",
        json={"url": "not-a-url"}
    )
    assert response.status_code == 200  # Should return 200 with error in message
    data = response.json()
    assert data["success"] is False
    assert "message" in data
    assert "Error" in data["message"]


def test_find_privacy_without_protocol():
    """Test URL without protocol."""
    data = {
        "url": "github.com"
    }
    response = client.post("/api/v1/privacy", json=data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "pp_url" in data
    assert "github.com" in data["pp_url"].lower()


def test_find_privacy_with_trailing_slash():
    """Test URL with trailing slash."""
    data = {
        "url": "github.com/"
    }
    response = client.post("/api/v1/privacy", json=data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "pp_url" in data


def test_find_privacy_with_www():
    """Test URL with www."""
    data = {
        "url": "www.github.com"
    }
    response = client.post("/api/v1/privacy", json=data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "pp_url" in data


def test_find_privacy_without_www():
    """Test URL without www."""
    data = {
        "url": "nodejs.org"
    }
    response = client.post("/api/v1/privacy", json=data)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "pp_url" in data
    assert "nodejs.org" in data["pp_url"].lower()


def test_privacy_error_handling_in_response():
    """Test that errors are properly returned in the response."""
    response = client.post(
        "/api/v1/privacy",
        json={"url": "https://nonexistent-domain-that-doesnt-exist123456789.com"}
    )
    assert response.status_code == 200  # Should return 200 even with error
    data = response.json()
    assert data["success"] is False
    assert "message" in data
    assert "Error" in data["message"]


def test_sites_with_both_privacy_and_tos():
    """Test handling of sites with both privacy and terms."""
    url = "github.com"
    
    # First get ToS
    tos_response = client.post("/api/v1/tos", json={"url": url})
    tos_data = tos_response.json()
    
    # Then get Privacy
    privacy_response = client.post("/api/v1/privacy", json={"url": url})
    privacy_data = privacy_response.json()
    
    # Both should succeed
    assert tos_data["success"] is True
    assert privacy_data["success"] is True
    
    # Both should have different URLs
    assert "pp_url" in privacy_data
    assert tos_data["tos_url"] != privacy_data["pp_url"]


def test_t3_chat_case():
    """Test t3.chat which is a special case that requires JavaScript rendering."""
    data = {
        "url": "t3.chat"
    }
    response = client.post("/api/v1/privacy", json=data)
    assert response.status_code == 200
    data = response.json()
    
    # Either we found a privacy URL or the app explained why in the message
    assert data["pp_url"] is not None
    assert "t3.chat" in data["pp_url"] or "Policy" in data["message"]


@patch("app.api.v1.endpoints.privacy.playwright_privacy_finder")
@patch("app.api.v1.endpoints.privacy.standard_privacy_finder")
def test_privacy_fallback_to_playwright(mock_standard, mock_playwright):
    """Test that the privacy endpoint falls back to Playwright when standard method fails."""
    # Mock standard method to fail
    mock_standard.return_value = PrivacyResponse(
        url="https://example.com",
        pp_url=None,
        success=False,
        message="No privacy policy found with standard method",
        method_used="standard_failed"
    )
    
    # Mock Playwright to succeed
    mock_playwright.return_value = PrivacyResponse(
        url="https://example.com",
        pp_url="https://example.com/privacy",
        success=True,
        message="Privacy policy found with Playwright",
        method_used="playwright"
    )
    
    data = {"url": "https://example.com"}
    response = client.post("/api/v1/privacy", json=data)
    data = response.json()
    
    # Verify standard method was called first
    mock_standard.assert_called_once()
    # Verify Playwright was called as fallback
    mock_playwright.assert_called_once()
    # Verify we got the Playwright result
    assert data["success"] is True
    assert data["pp_url"] == "https://example.com/privacy"


@patch("app.api.v1.endpoints.privacy.playwright_privacy_finder")
@patch("app.api.v1.endpoints.privacy.standard_privacy_finder")
def test_privacy_both_methods_fail(mock_standard, mock_playwright):
    """Test behavior when both standard and Playwright methods fail."""
    # Mock standard method to fail
    mock_standard.return_value = PrivacyResponse(
        url="https://example.com",
        pp_url=None,
        success=False,
        message="No privacy policy found with standard method",
        method_used="standard_failed"
    )
    
    # Mock Playwright to also fail
    mock_playwright.return_value = PrivacyResponse(
        url="https://example.com",
        pp_url=None,
        success=False,
        message="No privacy policy found with Playwright",
        method_used="playwright_failed"
    )
    
    data = {"url": "https://example.com"}
    response = client.post("/api/v1/privacy", json=data)
    data = response.json()
    
    # Verify both methods were called
    mock_standard.assert_called_once()
    mock_playwright.assert_called_once()
    # Verify the result shows failure
    assert data["success"] is False
    assert data["pp_url"] is None


@patch("app.api.v1.endpoints.privacy.playwright_privacy_finder")
@patch("app.api.v1.endpoints.privacy.standard_privacy_finder")
def test_privacy_standard_method_succeeds(mock_standard, mock_playwright):
    """Test that Playwright is not called when standard method succeeds."""
    # Mock standard method to succeed
    mock_standard.return_value = PrivacyResponse(
        url="https://example.com",
        pp_url="https://example.com/privacy",
        success=True,
        message="Privacy policy found with standard method",
        method_used="standard"
    )
    
    data = {"url": "https://example.com"}
    response = client.post("/api/v1/privacy", json=data)
    data = response.json()
    
    # Verify standard method was called
    mock_standard.assert_called_once()
    # Verify Playwright was NOT called
    mock_playwright.assert_not_called()
    # Verify we got the standard method result
    assert data["success"] is True
    assert data["pp_url"] == "https://example.com/privacy"


@patch("app.api.v1.endpoints.privacy.AsyncPlaywright")
@patch("app.api.v1.endpoints.privacy.requests.Session")
def test_case_insensitive_matching(mock_session, mock_playwright):
    """Test that the privacy policy finder is case-insensitive."""
    # Create a mock response with case variations
    mock_response = MagicMock()
    mock_response.text = """
    <html>
        <body>
            <a href="https://example.com/PRIVACY">PRIVACY POLICY</a>
            <a href="https://example.com/Privacy">Privacy Policy</a>
            <a href="https://example.com/privacy">privacy policy</a>
        </body>
    </html>
    """
    mock_response.url = "https://example.com"
    
    # Set up session mock
    mock_session_instance = mock_session.return_value
    mock_session_instance.get.return_value = mock_response
    mock_session_instance.head.return_value = mock_response
    
    # Make request
    data = {"url": "https://example.com"}
    response = client.post("/api/v1/privacy", json=data)
    
    # Check result
    result = response.json()
    assert result["success"] is True
    assert result["pp_url"] is not None
    
    # Test the actual API result
    data = {"url": "https://google.com"}  # Google's privacy policy has mixed case "Privacy"
    response = client.post("/api/v1/privacy", json=data)
    data = response.json()
    
    # Check that we found a privacy policy regardless of case
    assert data["pp_url"] is not None
    # Verify privacy is in the URL regardless of case
    assert "privacy" in data["pp_url"].lower() 