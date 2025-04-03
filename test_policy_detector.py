import asyncio
import logging
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import time
import json
from datetime import datetime

# Import our utility functions
try:
    from app.api.v1.endpoints.utils import (
        find_policy_link,
        check_policy_urls,
        normalize_url,
    )
except ImportError:
    # For standalone testing, define fallback functions if the module is not available
    def normalize_url(url):
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url.rstrip("/")

    def check_policy_urls(base_url, policy_type):
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

    def find_policy_link(url, soup, policy_type):
        """Simplified version for standalone testing"""
        return {
            "policy_url": None,
            "head_link": None,
            "footer_link": None,
            "html_link": None,
        }


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# List of sites to test
SITES = [
    # Image hosting/photography
    "https://unsplash.com",  # Our main target
    "https://www.flickr.com",  # Another image hosting site
    "https://500px.com",  # Photography site
    # Social media
    "https://www.facebook.com",
    "https://www.instagram.com",
    "https://twitter.com",
    "https://www.linkedin.com",
    "https://www.pinterest.com",
    # Tech/Software
    "https://www.github.com",
    "https://developer.mozilla.org",
    "https://www.stackoverflow.com",
    # E-commerce
    "https://www.amazon.com",
    "https://www.etsy.com",
    "https://www.shopify.com",
    # Search/Services
    "https://www.google.com",
    "https://www.bing.com",
    "https://www.yahoo.com",
    # News/Media
    "https://www.nytimes.com",
    "https://www.bbc.com",
    "https://www.cnn.com",
]


async def test_site(url):
    """Test finding ToS and Privacy Policy links on a site"""
    try:
        logger.info(f"Testing site: {url}")

        # Dictionary to store our findings
        results = {
            "url": url,
            "tos_link": None,
            "privacy_link": None,
            "head_tos": None,
            "head_privacy": None,
            "footer_tos": None,
            "footer_privacy": None,
            "html_tos": None,
            "html_privacy": None,
            "verified_tos": False,
            "verified_privacy": False,
            "elapsed_time": 0,
        }

        start_time = time.time()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()

            # Navigate to the site
            logger.info(f"Navigating to {url}...")
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Get initial content
            initial_content = await page.content()
            initial_soup = BeautifulSoup(initial_content, "html.parser")

            # Try clicking menus/dropdowns to reveal more links
            logger.info("Checking for navigation dropdowns...")

            try:
                # Try to find and click menu buttons
                menu_buttons = await page.query_selector_all(
                    'button[aria-label*="Menu"], [class*="menu"], [id*="menu"], [role="navigation"] button, header button'
                )

                # Also try buttons in the top right area (common location for account menus)
                if not menu_buttons:
                    menu_buttons = await page.query_selector_all(
                        "header button, nav button"
                    )

                initial_link_count = len(initial_soup.select("a"))

                for button in menu_buttons:
                    try:
                        logger.info("Clicking potential menu button...")
                        await button.click()
                        await page.wait_for_timeout(1000)  # Wait for any animations

                        # Check if new menu items appeared
                        content_after_click = await page.content()
                        after_soup = BeautifulSoup(content_after_click, "html.parser")

                        # Check if more menu items are visible now
                        if len(after_soup.select("a")) > initial_link_count:
                            logger.info("Found more links after clicking menu")
                            initial_soup = after_soup  # Update our soup
                            break
                    except Exception as e:
                        logger.warning(f"Error clicking menu button: {str(e)}")
            except Exception as e:
                logger.warning(f"Error interacting with menus: {str(e)}")

            # Now get the latest content
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")

            # Use our utility function to find policy links
            logger.info("Using policy detection utilities to find links...")
            tos_result = find_policy_link(url, soup, "tos")
            privacy_result = find_policy_link(url, soup, "privacy")

            # Extract results
            results["tos_link"] = tos_result.get("policy_url")
            results["head_tos"] = tos_result.get("head_link")
            results["footer_tos"] = tos_result.get("footer_link")
            results["html_tos"] = tos_result.get("html_link")

            results["privacy_link"] = privacy_result.get("policy_url")
            results["head_privacy"] = privacy_result.get("head_link")
            results["footer_privacy"] = privacy_result.get("footer_link")
            results["html_privacy"] = privacy_result.get("html_link")

            # Log what we found
            if results["tos_link"]:
                logger.info(f"Found ToS link: {results['tos_link']}")
            if results["privacy_link"]:
                logger.info(f"Found Privacy link: {results['privacy_link']}")

            # If we still don't have the links, try common URL patterns
            if not results["tos_link"] or not results["privacy_link"]:
                logger.info("Trying common URL patterns...")

                # Parse the base URL
                parsed_url = urlparse(url)
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

                if not results["tos_link"]:
                    tos_urls = check_policy_urls(base_url, "tos")
                    for try_url in tos_urls:
                        logger.info(f"Trying direct ToS URL: {try_url}")
                        try:
                            response = await page.goto(
                                try_url, wait_until="domcontentloaded", timeout=10000
                            )
                            if (
                                response and response.status < 400
                            ):  # If not an error response
                                results["tos_link"] = try_url
                                logger.info(f"Found working ToS URL: {try_url}")
                                break
                        except Exception:
                            logger.info(f"URL {try_url} failed")

                # Navigate back to main page
                await page.goto(url, wait_until="domcontentloaded")

                if not results["privacy_link"]:
                    privacy_urls = check_policy_urls(base_url, "privacy")
                    for try_url in privacy_urls:
                        logger.info(f"Trying direct Privacy URL: {try_url}")
                        try:
                            response = await page.goto(
                                try_url, wait_until="domcontentloaded", timeout=10000
                            )
                            if (
                                response and response.status < 400
                            ):  # If not an error response
                                results["privacy_link"] = try_url
                                logger.info(f"Found working Privacy URL: {try_url}")
                                break
                        except Exception:
                            logger.info(f"URL {try_url} failed")

            # Verify the links by visiting them
            if results["tos_link"]:
                try:
                    logger.info(f"Verifying ToS link: {results['tos_link']}")
                    tos_page = await context.new_page()
                    await tos_page.goto(
                        results["tos_link"],
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )

                    # Check title and content for verification
                    title = await tos_page.title()
                    content = await tos_page.content()
                    tos_soup = BeautifulSoup(content, "html.parser")

                    # Simple verification of content
                    body_text = tos_soup.get_text().lower()
                    has_tos_terms = any(
                        term in body_text
                        for term in ["terms", "service", "conditions", "user agreement"]
                    )

                    if has_tos_terms:
                        logger.info(f"Verified ToS link: {results['tos_link']}")
                        results["verified_tos"] = True

                    await tos_page.close()
                except Exception as e:
                    logger.error(f"Error verifying ToS link: {str(e)}")

            # Do the same for privacy policy
            if results["privacy_link"]:
                try:
                    logger.info(
                        f"Verifying Privacy Policy link: {results['privacy_link']}"
                    )
                    privacy_page = await context.new_page()
                    await privacy_page.goto(
                        results["privacy_link"],
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )

                    # Check content for verification
                    content = await privacy_page.content()
                    privacy_soup = BeautifulSoup(content, "html.parser")

                    # Simple verification of content
                    body_text = privacy_soup.get_text().lower()
                    has_privacy_terms = any(
                        term in body_text
                        for term in ["privacy", "data", "information", "collect"]
                    )

                    if has_privacy_terms:
                        logger.info(
                            f"Verified Privacy Policy link: {results['privacy_link']}"
                        )
                        results["verified_privacy"] = True

                    await privacy_page.close()
                except Exception as e:
                    logger.error(f"Error verifying Privacy Policy link: {str(e)}")

            await browser.close()

            # Calculate elapsed time
            results["elapsed_time"] = time.time() - start_time

            return results
    except Exception as e:
        logger.error(f"Error testing {url}: {str(e)}")
        return {
            "url": url,
            "tos_link": None,
            "privacy_link": None,
            "error": str(e),
            "elapsed_time": time.time() - start_time,
        }


async def main():
    # Run tests for all sites
    results = []

    for site in SITES:
        try:
            site_result = await test_site(site)
            results.append(site_result)
        except Exception as e:
            logger.error(f"Fatal error testing {site}: {str(e)}")
            results.append(
                {
                    "url": site,
                    "tos_link": None,
                    "privacy_link": None,
                    "error": str(e),
                    "fatal_error": True,
                }
            )

    # Print summary results
    print("\n===== POLICY DETECTOR TEST RESULTS =====")

    tos_success = 0
    privacy_success = 0
    both_success = 0
    failures = 0

    for result in results:
        site_url = result["url"]
        tos_found = result["tos_link"] is not None
        privacy_found = result["privacy_link"] is not None

        tos_verified = result.get("verified_tos", False)
        privacy_verified = result.get("verified_privacy", False)

        if tos_found and privacy_found:
            both_success += 1
            status = "✅ BOTH"
        elif tos_found:
            tos_success += 1
            status = "⚠️ TOS ONLY"
        elif privacy_found:
            privacy_success += 1
            status = "⚠️ PRIVACY ONLY"
        else:
            failures += 1
            status = "❌ FAILED"

        # Add verification status
        if tos_found and not tos_verified:
            status += " (ToS unverified)"
        if privacy_found and not privacy_verified:
            status += " (Privacy unverified)"

        print(f"{site_url}: {status}")

        if "error" in result:
            print(f"  Error: {result['error']}")

        if tos_found:
            print(f"  ToS: {result['tos_link']}")
            print(f"    Found in head: {'✅' if result['head_tos'] else '❌'}")
            print(f"    Found in footer: {'✅' if result['footer_tos'] else '❌'}")
            print(f"    Found in HTML: {'✅' if result['html_tos'] else '❌'}")

        if privacy_found:
            print(f"  Privacy: {result['privacy_link']}")
            print(f"    Found in head: {'✅' if result['head_privacy'] else '❌'}")
            print(f"    Found in footer: {'✅' if result['footer_privacy'] else '❌'}")
            print(f"    Found in HTML: {'✅' if result['html_privacy'] else '❌'}")

        print(f"  Time: {result.get('elapsed_time', 0):.2f}s")
        print()

    # Calculate success rates
    total_sites = len(SITES)
    tos_rate = ((both_success + tos_success) / total_sites) * 100
    privacy_rate = ((both_success + privacy_success) / total_sites) * 100
    both_rate = (both_success / total_sites) * 100
    failure_rate = (failures / total_sites) * 100

    print("\n===== SUMMARY =====")
    print(f"Total sites tested: {total_sites}")
    print(
        f"Both ToS and Privacy found: {both_success}/{total_sites} ({both_rate:.1f}%)"
    )
    print(
        f"ToS success rate: {both_success + tos_success}/{total_sites} ({tos_rate:.1f}%)"
    )
    print(
        f"Privacy success rate: {both_success + privacy_success}/{total_sites} ({privacy_rate:.1f}%)"
    )
    print(f"Failure rate: {failures}/{total_sites} ({failure_rate:.1f}%)")

    # Save results to JSON file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"policy_detector_results_{timestamp}.json"

    with open(filename, "w") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "total_sites": total_sites,
                "both_success": both_success,
                "tos_success": tos_success,
                "privacy_success": privacy_success,
                "failures": failures,
                "tos_rate": tos_rate,
                "privacy_rate": privacy_rate,
                "both_rate": both_rate,
                "failure_rate": failure_rate,
                "results": results,
            },
            f,
            indent=2,
        )

    print(f"\nDetailed results saved to {filename}")


if __name__ == "__main__":
    asyncio.run(main())
