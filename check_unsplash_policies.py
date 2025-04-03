import asyncio
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from playwright.async_api import async_playwright
import time
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Target URL
UNSPLASH_URL = "https://unsplash.com"
OUTPUT_DIR = "policy_results"


async def check_unsplash_policies():
    """Check Unsplash's ToS and Privacy Policy links and save their content"""
    logger.info(f"Checking Unsplash policy pages: {UNSPLASH_URL}")

    # Create output directory if it doesn't exist
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # Dictionary to store our findings
    results = {
        "url": UNSPLASH_URL,
        "tos_link": None,
        "privacy_link": None,
        "tos_saved": False,
        "privacy_saved": False,
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        )

        # Check for Terms of Service
        tos_patterns = ["/terms", "/tos", "/terms-of-service", "/terms-conditions"]

        for pattern in tos_patterns:
            try_url = urljoin(UNSPLASH_URL, pattern)
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

                    # Get the title and content
                    title = await page.title()

                    # Save to file
                    tos_filename = os.path.join(OUTPUT_DIR, "unsplash_tos.html")
                    with open(tos_filename, "w", encoding="utf-8") as f:
                        f.write(content)

                    # Also extract text
                    tos_text_filename = os.path.join(OUTPUT_DIR, "unsplash_tos.txt")
                    with open(tos_text_filename, "w", encoding="utf-8") as f:
                        f.write(f"Title: {title}\n\n")
                        f.write(tos_soup.get_text())

                    results["tos_saved"] = True
                    break

                await page.close()

            except Exception as e:
                logger.warning(f"Error trying ToS URL {try_url}: {str(e)}")

        # Check for Privacy Policy
        privacy_patterns = ["/privacy", "/privacy-policy", "/data-policy"]

        for pattern in privacy_patterns:
            try_url = urljoin(UNSPLASH_URL, pattern)
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

                    # Save to file
                    privacy_filename = os.path.join(OUTPUT_DIR, "unsplash_privacy.html")
                    with open(privacy_filename, "w", encoding="utf-8") as f:
                        f.write(content)

                    # Also extract text
                    privacy_text_filename = os.path.join(
                        OUTPUT_DIR, "unsplash_privacy.txt"
                    )
                    with open(privacy_text_filename, "w", encoding="utf-8") as f:
                        f.write(f"Title: {title}\n\n")
                        f.write(privacy_soup.get_text())

                    results["privacy_saved"] = True
                    break

                await page.close()

            except Exception as e:
                logger.warning(f"Error trying Privacy URL {try_url}: {str(e)}")

        await browser.close()

    return results


async def main():
    results = await check_unsplash_policies()

    # Print results
    print("\n===== UNSPLASH POLICY CHECK RESULTS =====")
    print(f"URL: {results['url']}")

    if results["tos_link"]:
        print(f"\nTerms of Service: ✅ Found")
        print(f"Link: {results['tos_link']}")

        if results["tos_saved"]:
            print(f"Content saved to: {os.path.join(OUTPUT_DIR, 'unsplash_tos.html')}")
            print(f"Text extracted to: {os.path.join(OUTPUT_DIR, 'unsplash_tos.txt')}")
    else:
        print("\nTerms of Service: ❌ Not found")

    if results["privacy_link"]:
        print(f"\nPrivacy Policy: ✅ Found")
        print(f"Link: {results['privacy_link']}")

        if results["privacy_saved"]:
            print(
                f"Content saved to: {os.path.join(OUTPUT_DIR, 'unsplash_privacy.html')}"
            )
            print(
                f"Text extracted to: {os.path.join(OUTPUT_DIR, 'unsplash_privacy.txt')}"
            )
    else:
        print("\nPrivacy Policy: ❌ Not found")

    print("\nYou can examine the saved files to review Unsplash's policies.")


if __name__ == "__main__":
    asyncio.run(main())
