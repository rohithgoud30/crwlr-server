import asyncio
import sys
import os

# Add tests directory to path if needed
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Import from our policy detector module
from tests.policy_detector.policy_detector import check_policy_with_playwright


async def test_policy_detector():
    """
    Simple test script to demonstrate the universal policy detector.
    """
    # Test URL - can be any website
    url = (
        input("Enter a URL to test (e.g., google.com): ")
        if len(sys.argv) < 2
        else sys.argv[1]
    )

    print(f"\nTesting policy detection for: {url}")
    print("This may take a moment...\n")

    # Run the detector
    results = await check_policy_with_playwright(url)

    # Print results
    print("\n===== POLICY DETECTION RESULTS =====")
    print(f"URL: {results['url']}")

    if results.get("tos_link"):
        print(f"\nTerms of Service: ✅ Found")
        print(f"Link: {results['tos_link']}")

        if results.get("tos_saved"):
            domain_clean = results["domain"].replace(".", "_").replace("-", "_")
            print(f"Content saved to: policy_results/{domain_clean}_tos.html")
            print(f"Text extracted to: policy_results/{domain_clean}_tos.txt")
    else:
        print("\nTerms of Service: ❌ Not found")

    if results.get("privacy_link"):
        print(f"\nPrivacy Policy: ✅ Found")
        print(f"Link: {results['privacy_link']}")

        if results.get("privacy_saved"):
            domain_clean = results["domain"].replace(".", "_").replace("-", "_")
            print(f"Content saved to: policy_results/{domain_clean}_privacy.html")
            print(f"Text extracted to: policy_results/{domain_clean}_privacy.txt")
    else:
        print("\nPrivacy Policy: ❌ Not found")

    if "error" in results:
        print(f"\nError: {results['error']}")

    print(
        f"\nResults also saved to: policy_results/{results['domain'].replace('.', '_').replace('-', '_')}_results.json"
    )


if __name__ == "__main__":
    asyncio.run(test_policy_detector())
