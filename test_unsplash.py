import asyncio
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.async_api import async_playwright
import time

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

# Target URL
UNSPLASH_URL = "https://unsplash.com"


async def test_unsplash():
    """Test finding ToS and Privacy Policy links on Unsplash.com"""
    try:
        logger.info(f"Testing Unsplash: {UNSPLASH_URL}")

        # Dictionary to store our findings
        results = {
            "url": UNSPLASH_URL,
            "tos_link": None,
            "privacy_link": None,
            "head_tos": None,
            "head_privacy": None,
            "footer_tos": None,
            "footer_privacy": None,
            "html_tos": None,
            "html_privacy": None,
            "verified": False,
        }

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()

            # Navigate to the site
            logger.info("Navigating to Unsplash...")
            await page.goto(UNSPLASH_URL, wait_until="networkidle", timeout=30000)

            # Get initial content
            initial_content = await page.content()
            initial_soup = BeautifulSoup(initial_content, "html.parser")

            # First try to find links in menus that might require interaction
            logger.info("Checking for navigation dropdowns...")

            # Look for menu items or buttons that might open menus
            try:
                # Based on the image, we can see there's a dropdown menu - let's check if clicking reveals ToS links

                # Try to find visible menu/hamburger buttons
                menu_buttons = await page.query_selector_all(
                    'button[aria-label*="Menu"], [class*="menu"], [id*="menu"], [role="navigation"] button, header button'
                )

                # Try buttons in the top right area (based on the screenshot where account/menu buttons usually are)
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
                            # We'll update our soup to use this new content
                            soup = after_soup
                            break
                    except Exception as e:
                        logger.warning(f"Error clicking menu button: {str(e)}")
                        # Continue to try other buttons
            except Exception as e:
                logger.warning(f"Error interacting with menus: {str(e)}")

            # Now get the latest content
            content = await page.content()
            soup = BeautifulSoup(content, "html.parser")

            # Based on the screenshot, there appears to be a dropdown with company/product/community sections
            # Look specifically for those elements
            logger.info("Looking for specific Unsplash menu elements...")

            # Try to find the Terms and Privacy Policy in the footer
            logger.info("Looking for 'Terms' and 'Privacy Policy' text...")
            terms_elements = soup.find_all(string=lambda text: text and "Terms" in text)
            privacy_elements = soup.find_all(
                string=lambda text: text and "Privacy Policy" in text
            )
            license_elements = soup.find_all(
                string=lambda text: text and "License" in text
            )

            # Check for links containing these terms
            logger.info(f"Found {len(terms_elements)} elements with 'Terms'")
            logger.info(f"Found {len(privacy_elements)} elements with 'Privacy Policy'")
            logger.info(f"Found {len(license_elements)} elements with 'License'")

            # Process elements with exact text matches
            for term_element in terms_elements:
                parent = term_element.parent
                if parent and parent.name == "a" and parent.has_attr("href"):
                    href = parent["href"]
                    absolute_url = urljoin(UNSPLASH_URL, href)
                    results["html_tos"] = absolute_url
                    logger.info(f"Found Terms link: {absolute_url}")

            for privacy_element in privacy_elements:
                parent = privacy_element.parent
                if parent and parent.name == "a" and parent.has_attr("href"):
                    href = parent["href"]
                    absolute_url = urljoin(UNSPLASH_URL, href)
                    results["html_privacy"] = absolute_url
                    logger.info(f"Found Privacy Policy link: {absolute_url}")

            for license_element in license_elements:
                parent = license_element.parent
                if parent and parent.name == "a" and parent.has_attr("href"):
                    href = parent["href"]
                    absolute_url = urljoin(UNSPLASH_URL, href)
                    if not results["html_tos"]:
                        results["html_tos"] = absolute_url
                        logger.info(f"Found License link: {absolute_url}")

            # 1. Check head element for links
            logger.info("Checking head element...")
            head_element = soup.find("head")
            if head_element:
                head_links = head_element.find_all("a", href=True)
                for link in head_links:
                    href = link.get("href", "").strip()
                    if not href or href.startswith(("#", "javascript:", "mailto:")):
                        continue

                    link_text = link.get_text().strip().lower()
                    link_title = link.get("title", "").lower()

                    # Check for terms-related or privacy-related terms
                    absolute_url = urljoin(UNSPLASH_URL, href)

                    if (
                        "terms" in link_text
                        or "terms" in link_title
                        or "tos" in href.lower()
                        or "terms" in href.lower()
                    ):
                        results["head_tos"] = absolute_url
                        logger.info(f"Found ToS link in head: {absolute_url}")

                    if (
                        "privacy" in link_text
                        or "privacy" in link_title
                        or "privacy" in href.lower()
                    ):
                        results["head_privacy"] = absolute_url
                        logger.info(f"Found Privacy link in head: {absolute_url}")

            # 2. Check footer elements
            logger.info("Checking footer elements...")
            footer_elements = soup.select(
                'footer, [class*="footer"], [id*="footer"], [role="contentinfo"]'
            )

            # Based on the screenshot, we know there's a specific footer section with links
            # Let's also check at the bottom of the page for direct links
            if not footer_elements:
                # Try to find elements that are visually at the bottom of the page
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)  # Wait for any lazy-loaded content

                # Get updated content after scrolling
                bottom_content = await page.content()
                bottom_soup = BeautifulSoup(bottom_content, "html.parser")

                # Look for potential footer elements after scrolling
                footer_elements = bottom_soup.select(
                    'footer, [class*="footer"], [id*="footer"], [role="contentinfo"]'
                )

                # Also just try the last few links on the page
                bottom_links = bottom_soup.select(
                    "body > *:last-child a, body > *:last-child > *:last-child a"
                )

                if bottom_links:
                    logger.info(
                        f"Found {len(bottom_links)} links near the bottom of the page"
                    )

                    for link in bottom_links:
                        href = link.get("href", "").strip()
                        if not href or href.startswith(("#", "javascript:", "mailto:")):
                            continue

                        text = link.get_text().strip().lower()
                        absolute_url = urljoin(UNSPLASH_URL, href)

                        logger.info(f"Bottom link: {text} - {absolute_url}")

                        if text == "terms" or "terms" in text:
                            results["footer_tos"] = absolute_url
                            logger.info(f"Found ToS link at bottom: {absolute_url}")

                        if text == "privacy policy" or "privacy" in text:
                            results["footer_privacy"] = absolute_url
                            logger.info(f"Found Privacy link at bottom: {absolute_url}")

                        if text == "license":
                            if not results["footer_tos"]:
                                results["footer_tos"] = absolute_url
                                logger.info(
                                    f"Found License link at bottom: {absolute_url}"
                                )

            # Process any footer elements found
            for footer in footer_elements:
                for link in footer.find_all("a", href=True):
                    href = link.get("href", "").strip()
                    if not href or href.startswith(("#", "javascript:", "mailto:")):
                        continue

                    absolute_url = urljoin(UNSPLASH_URL, href)
                    link_text = link.get_text().strip().lower()

                    logger.info(f"Footer link: {link_text} - {absolute_url}")

                    # Check for terms in footer
                    if (
                        "terms" in link_text
                        or "tos" in link_text
                        or "/terms" in href.lower()
                        or "/tos" in href.lower()
                    ):
                        results["footer_tos"] = absolute_url
                        logger.info(f"Found ToS link in footer: {absolute_url}")

                    # Check for privacy in footer
                    if "privacy" in link_text or "privacy" in href.lower():
                        results["footer_privacy"] = absolute_url
                        logger.info(f"Found Privacy link in footer: {absolute_url}")

                    # Check for license which may contain terms
                    if "license" in link_text or "license" in href.lower():
                        if not results["footer_tos"]:
                            results["footer_tos"] = absolute_url
                            logger.info(f"Found License link in footer: {absolute_url}")

            # 3. Check entire HTML document
            logger.info("Checking entire HTML document...")
            for link in soup.find_all("a", href=True):
                href = link.get("href", "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue

                absolute_url = urljoin(UNSPLASH_URL, href)
                link_text = link.get_text().strip().lower()

                # Check for terms in general
                if (
                    "terms" in link_text
                    or "/terms" in href.lower()
                    or "/tos" in href.lower()
                    or "terms-of-service" in href.lower()
                ):
                    results["html_tos"] = absolute_url
                    logger.info(f"Found ToS link in HTML: {absolute_url}")

                # Check for privacy in general
                if (
                    "privacy" in link_text
                    or "/privacy" in href.lower()
                    or "privacy-policy" in href.lower()
                ):
                    results["html_privacy"] = absolute_url
                    logger.info(f"Found Privacy link in HTML: {absolute_url}")

                # Check for license as potential ToS
                if "license" in link_text or "/license" in href.lower():
                    if not results["html_tos"]:
                        results["html_tos"] = absolute_url
                        logger.info(f"Found License link in HTML: {absolute_url}")

            # Set the final links, prioritizing in order: head, footer, general HTML
            results["tos_link"] = (
                results["head_tos"] or results["footer_tos"] or results["html_tos"]
            )
            results["privacy_link"] = (
                results["head_privacy"]
                or results["footer_privacy"]
                or results["html_privacy"]
            )

            # If we still haven't found the ToS and Privacy, try directly accessing known URL patterns
            if not results["tos_link"] or not results["privacy_link"]:
                logger.info("Trying known URL patterns...")

                # Common URL patterns for terms and privacy
                tos_patterns = [
                    "/terms",
                    "/tos",
                    "/terms-of-service",
                    "/terms-conditions",
                    "/legal/terms",
                ]
                privacy_patterns = ["/privacy", "/privacy-policy", "/legal/privacy"]

                # Check each pattern
                for pattern in tos_patterns:
                    if not results["tos_link"]:
                        try_url = urljoin(UNSPLASH_URL, pattern)
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
                await page.goto(UNSPLASH_URL, wait_until="domcontentloaded")

                # Check privacy patterns
                for pattern in privacy_patterns:
                    if not results["privacy_link"]:
                        try_url = urljoin(UNSPLASH_URL, pattern)
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

            # Verify the links by visiting them if found
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
                        results["verified"] = True

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
                        results["verified"] = results["verified"] and True

                    await privacy_page.close()
                except Exception as e:
                    logger.error(f"Error verifying Privacy Policy link: {str(e)}")

            await browser.close()

            return results
    except Exception as e:
        logger.error(f"Error testing Unsplash: {str(e)}")
        return {
            "url": UNSPLASH_URL,
            "tos_link": None,
            "privacy_link": None,
            "verified": False,
            "error": str(e),
        }


async def main():
    results = await test_unsplash()

    # Print results
    print("\n===== UNSPLASH TEST RESULTS =====")
    print(f"URL: {results['url']}")

    # ToS Results
    if results["tos_link"]:
        print("\nTERMS OF SERVICE:")
        print(f"Found: {'✅' if results['tos_link'] else '❌'}")
        print(f"Link: {results['tos_link']}")
        print(f"Found in head: {'✅' if results['head_tos'] else '❌'}")
        print(f"Found in footer: {'✅' if results['footer_tos'] else '❌'}")
        print(f"Found in HTML: {'✅' if results['html_tos'] else '❌'}")
    else:
        print("\nTERMS OF SERVICE: ❌ Not Found")

    # Privacy Results
    if results["privacy_link"]:
        print("\nPRIVACY POLICY:")
        print(f"Found: {'✅' if results['privacy_link'] else '❌'}")
        print(f"Link: {results['privacy_link']}")
        print(f"Found in head: {'✅' if results['head_privacy'] else '❌'}")
        print(f"Found in footer: {'✅' if results['footer_privacy'] else '❌'}")
        print(f"Found in HTML: {'✅' if results['html_privacy'] else '❌'}")
    else:
        print("\nPRIVACY POLICY: ❌ Not Found")

    # Verification Results
    print(
        f"\nVerification: {'✅ VERIFIED' if results['verified'] else '❌ NOT VERIFIED'}"
    )
    if "error" in results:
        print(f"Error: {results['error']}")


if __name__ == "__main__":
    asyncio.run(main())
