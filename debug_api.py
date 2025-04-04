import asyncio
import aiohttp
import json
import sys


async def test_api_endpoint(url):
    """Test the privacy policy API endpoint with the given URL."""
    print(f"\nTesting API endpoint for: {url}")

    api_url = "http://localhost:8000/api/v1/privacy/find"
    payload = {"url": url}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=payload) as response:
                status = response.status
                print(f"API Response Status: {status}")

                try:
                    result = await response.json()
                    print(f"Response JSON: {json.dumps(result, indent=2)}")

                    if result.get("success"):
                        print(
                            f"✅ SUCCESS: Privacy policy found at {result.get('pp_url')}"
                        )
                        print(f"  Method used: {result.get('method_used')}")
                    else:
                        print(f"❌ FAILED: {result.get('message')}")
                except:
                    text = await response.text()
                    print(f"Raw response: {text}")

    except Exception as e:
        print(f"Error calling API: {str(e)}")


async def main():
    # URLs to test
    urls = [
        "https://unsplash.com",
        "https://pinterest.com",
        "https://reddit.com",
        "https://twitter.com",
    ]

    # Add additional URLs from command line arguments
    if len(sys.argv) > 1:
        urls.extend(sys.argv[1:])

    for url in urls:
        await test_api_endpoint(url)
        print("-" * 50)


if __name__ == "__main__":
    asyncio.run(main())
