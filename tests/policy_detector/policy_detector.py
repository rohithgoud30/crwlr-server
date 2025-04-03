import asyncio
import logging
import json
import os
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright
import time
from datetime import datetime

try:
    from app.api.v1.endpoints.utils import (
        find_policy_link,
        check_policy_urls,
        normalize_url,
        is_on_policy_page,
    )
except ImportError:
    # For standalone testing, define fallback functions if the module is not available
    def normalize_url(url):
        """Normalize URLs to ensure they have a proper protocol."""
        url = url.strip()
        url = url.rstrip("/")

        # Add protocol if missing
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        return url

    def find_policy_link(url, soup, policy_type):
        """Simplified fallback version for standalone testing"""
        return {
            "policy_url": None,
            "head_link": None,
            "footer_link": None,
            "html_link": None,
        }

    def check_policy_urls(base_url, policy_type):
        """Return common patterns to try for policy pages"""
        if policy_type == "tos":
            patterns = [
                "/terms",
                "/tos",
                "/terms-of-service",
                "/terms-conditions",
                "/legal/terms",
            ]
        else:
            patterns = ["/privacy", "/privacy-policy", "/legal/privacy"]
        return [urljoin(base_url, pattern) for pattern in patterns]

    def is_on_policy_page(url, policy_type):
        """Check if URL appears to be already on a policy page"""
        url_lower = url.lower()

        if policy_type == "tos":
            return any(
                term in url_lower
                for term in [
                    "/terms",
                    "/tos",
                    "/terms-of-service",
                    "/terms-and-conditions",
                    "/legal/terms",
                    "/conditions",
                    "/user-agreement",
                    "/eula",
                ]
            )
        elif policy_type == "privacy":
            return any(
                term in url_lower
                for term in [
                    "/privacy",
                    "/privacy-policy",
                    "/data-policy",
                    "/data-protection",
                    "/legal/privacy",
                ]
            )

        return False


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Output directory for saving policy content
OUTPUT_DIR = "policy_results"


async def check_policy_with_playwright(url, policy_type="both"):
    """
    Check a website for policy links using Playwright to handle JavaScript rendering.
    Works with any website, not just specific ones.

    Args:
        url: The URL to check
        policy_type: 'tos', 'privacy', or 'both'

    Returns:
        Dictionary with policy link results
    """
    # Normalize URL
    url = normalize_url(url)
    logger.info(f"Checking policy pages for {url}")

    # Create output directory if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Get domain for file naming
    domain = urlparse(url).netloc
    domain_clean = domain.replace(".", "_").replace("-", "_")

    # Dictionary to store findings
    results = {
        "url": url,
        "domain": domain,
        "tos_link": None,
        "privacy_link": None,
        "tos_saved": False,
        "privacy_saved": False,
        "checked_at": datetime.now().isoformat(),
    }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            # First visit the main page to look for links
            main_page = await context.new_page()
            try:
                # Reduce timeout from 30000ms to 15000ms to avoid long hangs
                await main_page.goto(url, wait_until="networkidle", timeout=15000)
            except Exception as e:
                # Try again with domcontentloaded which is less strict
                try:
                    await main_page.goto(
                        url, wait_until="domcontentloaded", timeout=10000
                    )
                except Exception as inner_e:
                    logger.error(
                        f"Failed to load page even with reduced expectations: {str(inner_e)}"
                    )
                    raise inner_e

            # Get page content
            content = await main_page.content()
            soup = BeautifulSoup(content, "html.parser")

            # Check if we're already on a policy page
            if policy_type in ["tos", "both"] and is_on_policy_page(url, "tos"):
                results["tos_link"] = url
                logger.info(f"Already on ToS page: {url}")

            if policy_type in ["privacy", "both"] and is_on_policy_page(url, "privacy"):
                results["privacy_link"] = url
                logger.info(f"Already on Privacy Policy page: {url}")

            # Try to find policy links in the page
            if policy_type in ["tos", "both"] and not results["tos_link"]:
                tos_results = find_policy_link(url, soup, "tos")
                if tos_results["policy_url"]:
                    results["tos_link"] = tos_results["policy_url"]
                    logger.info(f"Found ToS link in page: {results['tos_link']}")

            if policy_type in ["privacy", "both"] and not results["privacy_link"]:
                privacy_results = find_policy_link(url, soup, "privacy")
                if privacy_results["policy_url"]:
                    results["privacy_link"] = privacy_results["policy_url"]
                    logger.info(
                        f"Found Privacy link in page: {results['privacy_link']}"
                    )

            # If links not found, try common patterns
            parsed_url = urlparse(url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

            # Check for Terms of Service if not found and requested
            if policy_type in ["tos", "both"] and not results["tos_link"]:
                tos_patterns = check_policy_urls(base_url, "tos")

                for try_url in tos_patterns:
                    logger.info(f"Trying ToS URL: {try_url}")

                    try:
                        page = await context.new_page()
                        response = await page.goto(
                            try_url, wait_until="domcontentloaded", timeout=10000
                        )

                        if response and response.status < 400:
                            results["tos_link"] = try_url
                            logger.info(f"Found working ToS URL: {try_url}")

                            # Save the content
                            content = await page.content()
                            tos_soup = BeautifulSoup(content, "html.parser")

                            # Get the title
                            title = await page.title()

                            # Save to files with domain-specific names
                            tos_filename = os.path.join(
                                OUTPUT_DIR, f"{domain_clean}_tos.html"
                            )
                            with open(tos_filename, "w", encoding="utf-8") as f:
                                f.write(content)

                            # Also extract text
                            tos_text_filename = os.path.join(
                                OUTPUT_DIR, f"{domain_clean}_tos.txt"
                            )
                            with open(tos_text_filename, "w", encoding="utf-8") as f:
                                f.write(f"Title: {title}\n\n")
                                f.write(tos_soup.get_text())

                            results["tos_saved"] = True
                            break

                        await page.close()

                    except Exception as e:
                        logger.warning(f"Error trying ToS URL {try_url}: {str(e)}")
                        await page.close()

            # Check for Privacy Policy if not found and requested
            if policy_type in ["privacy", "both"] and not results["privacy_link"]:
                privacy_patterns = check_policy_urls(base_url, "privacy")

                for try_url in privacy_patterns:
                    logger.info(f"Trying Privacy URL: {try_url}")

                    try:
                        page = await context.new_page()
                        response = await page.goto(
                            try_url, wait_until="domcontentloaded", timeout=10000
                        )

                        if response and response.status < 400:
                            results["privacy_link"] = try_url
                            logger.info(f"Found working Privacy URL: {try_url}")

                            # Save the content
                            content = await page.content()
                            privacy_soup = BeautifulSoup(content, "html.parser")

                            # Get the title
                            title = await page.title()

                            # Save to files with domain-specific names
                            privacy_filename = os.path.join(
                                OUTPUT_DIR, f"{domain_clean}_privacy.html"
                            )
                            with open(privacy_filename, "w", encoding="utf-8") as f:
                                f.write(content)

                            # Also extract text
                            privacy_text_filename = os.path.join(
                                OUTPUT_DIR, f"{domain_clean}_privacy.txt"
                            )
                            with open(
                                privacy_text_filename, "w", encoding="utf-8"
                            ) as f:
                                f.write(f"Title: {title}\n\n")
                                f.write(privacy_soup.get_text())

                            results["privacy_saved"] = True
                            break

                        await page.close()

                    except Exception as e:
                        logger.warning(f"Error trying Privacy URL {try_url}: {str(e)}")
                        await page.close()

            await browser.close()

    except Exception as e:
        logger.error(f"Error checking policies for {url}: {str(e)}")
        results["error"] = str(e)

    # Save the results to a JSON file
    results_filename = os.path.join(OUTPUT_DIR, f"{domain_clean}_results.json")
    with open(results_filename, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results


async def batch_check_policies(urls):
    """
    Check multiple websites for policy links and save results.

    Args:
        urls: List of URLs to check

    Returns:
        List of result dictionaries
    """
    all_results = []

    for url in urls:
        try:
            results = await check_policy_with_playwright(url)
            all_results.append(results)
        except Exception as e:
            logger.error(f"Error checking {url}: {str(e)}")
            all_results.append(
                {
                    "url": url,
                    "error": str(e),
                    "tos_link": None,
                    "privacy_link": None,
                    "checked_at": datetime.now().isoformat(),
                }
            )

    # Save summary to JSON
    summary_file = os.path.join(OUTPUT_DIR, "policy_check_summary.json")
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    return all_results


async def main():
    # Example usage
    if len(asyncio.get_event_loop()._ready) > 0:  # Check if called with arguments
        import sys

        if len(sys.argv) > 1:
            url = sys.argv[1]
            results = await check_policy_with_playwright(url)
        else:
            # Default set of websites to test
            test_urls = [
                "google.com",
                "facebook.com",
                "twitter.com",
                "amazon.com",
                "microsoft.com",
                "github.com",
            ]
            results = await batch_check_policies(test_urls)
    else:
        # Interactive mode
        url = input("Enter URL to check (or multiple URLs separated by commas): ")
        if "," in url:
            urls = [u.strip() for u in url.split(",")]
            results = await batch_check_policies(urls)
        else:
            results = await check_policy_with_playwright(url)

    # Print results
    if isinstance(results, list):
        print("\n===== POLICY CHECK RESULTS =====")
        for i, result in enumerate(results):
            print(f"\n--- {i+1}. {result['url']} ---")

            if result.get("tos_link"):
                print(f"Terms of Service: ✅ Found at {result['tos_link']}")
                if result.get("tos_saved"):
                    domain_clean = result["domain"].replace(".", "_").replace("-", "_")
                    print(
                        f"Content saved to: {os.path.join(OUTPUT_DIR, f'{domain_clean}_tos.html')}"
                    )
            else:
                print("Terms of Service: ❌ Not found")

            if result.get("privacy_link"):
                print(f"Privacy Policy: ✅ Found at {result['privacy_link']}")
                if result.get("privacy_saved"):
                    domain_clean = result["domain"].replace(".", "_").replace("-", "_")
                    print(
                        f"Content saved to: {os.path.join(OUTPUT_DIR, f'{domain_clean}_privacy.html')}"
                    )
            else:
                print("Privacy Policy: ❌ Not found")

            if "error" in result:
                print(f"Error: {result['error']}")
    else:
        print("\n===== POLICY CHECK RESULTS =====")
        print(f"URL: {results['url']}")

        if results.get("tos_link"):
            print(f"\nTerms of Service: ✅ Found")
            print(f"Link: {results['tos_link']}")

            if results.get("tos_saved"):
                domain_clean = results["domain"].replace(".", "_").replace("-", "_")
                print(
                    f"Content saved to: {os.path.join(OUTPUT_DIR, f'{domain_clean}_tos.html')}"
                )
                print(
                    f"Text extracted to: {os.path.join(OUTPUT_DIR, f'{domain_clean}_tos.txt')}"
                )
        else:
            print("\nTerms of Service: ❌ Not found")

        if results.get("privacy_link"):
            print(f"\nPrivacy Policy: ✅ Found")
            print(f"Link: {results['privacy_link']}")

            if results.get("privacy_saved"):
                domain_clean = results["domain"].replace(".", "_").replace("-", "_")
                print(
                    f"Content saved to: {os.path.join(OUTPUT_DIR, f'{domain_clean}_privacy.html')}"
                )
                print(
                    f"Text extracted to: {os.path.join(OUTPUT_DIR, f'{domain_clean}_privacy.txt')}"
                )
        else:
            print("\nPrivacy Policy: ❌ Not found")

    print(f"\nAll results saved to {OUTPUT_DIR} directory")


if __name__ == "__main__":
    asyncio.run(main())
