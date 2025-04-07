"""
Test TOS detection with prioritized scanning order.
"""

import asyncio
import logging
import sys
from typing import Dict, Any, List
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.append(".")

from app.api.v1.endpoints.tos import TosRequest, TosResponse
from app.api.v1.endpoints.tos import router as tos_router
from app.api.v1.endpoints.utils import normalize_url

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a test client
app = FastAPI()
app.include_router(tos_router, prefix="/api/v1")
client = TestClient(app)


def test_tos_detection():
    """Test the TOS detection with various websites, including hungrystudio.com"""

    test_urls = [
        "https://hungrystudio.com",  # The site that was having issues
        "https://google.com",  # Known site with TOS
        "https://apple.com",  # Another known site
        "https://unsplash.com",  # Content site
    ]

    print("\nTesting TOS detection with direct HTTP requests:")
    for url in test_urls:
        print(f"\nTesting: {url}")
        try:
            # Make a direct HTTP request to the site
            response = requests.get(url, timeout=10)
            terms_text = "terms"
            if terms_text in response.text.lower():
                print(f"✅ Found 'terms' in {url} content")
            else:
                print(f"❌ Did not find 'terms' in {url} content")

            # Try to find common patterns
            base_url = url.rstrip("/")
            common_patterns = [
                f"{base_url}/terms",
                f"{base_url}/tos",
                f"{base_url}/terms-of-service",
                f"{base_url}/terms-of-use",
            ]

            for pattern in common_patterns:
                try:
                    print(f"  Checking pattern: {pattern}")
                    resp = requests.head(pattern, timeout=5)
                    if resp.status_code < 400:
                        print(
                            f"  ✅ Pattern accessible: {pattern} (Status: {resp.status_code})"
                        )
                    else:
                        print(
                            f"  ❌ Pattern inaccessible: {pattern} (Status: {resp.status_code})"
                        )
                except Exception as e:
                    print(f"  ❌ Error checking pattern {pattern}: {str(e)}")

        except Exception as e:
            print(f"Error during test: {str(e)}")

    # Test the API endpoint directly
    print("\nTesting the API endpoint directly:")
    for url in test_urls:
        print(f"\nTesting API with URL: {url}")
        try:
            response = client.post("/api/v1/tos", json={"url": url})
            if response.status_code == 200:
                result = response.json()
                print(f"Success: {result['success']}")
                print(f"Method: {result['method_used']}")
                print(f"Message: {result['message']}")
                if result["tos_url"]:
                    print(f"ToS URL: {result['tos_url']}")
                else:
                    print("No ToS URL found")
            else:
                print(f"❌ API error: {response.status_code} - {response.text}")
        except Exception as e:
            print(f"❌ API test error: {str(e)}")


if __name__ == "__main__":
    print("Testing ToS detection with prioritized scanning")
    print("================================================")
    print("Focusing on hungrystudio.com that was failing previously")

    test_tos_detection()

    print("\nTest completed")
