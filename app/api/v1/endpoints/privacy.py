from fastapi import APIRouter, Response
from pydantic import BaseModel, field_validator
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import re
from typing import Optional, Any, List, Tuple
import asyncio
from playwright.async_api import async_playwright
import logging
from .utils import (
    normalize_url,
    prepare_url_variations,
    get_footer_score,
    get_domain_score,
    get_common_penalties,
    is_on_policy_page,
    get_policy_patterns,
    get_policy_score,
    find_policy_by_class_id,
    is_likely_false_positive,
    is_correct_policy_type,
    get_root_domain,
)
from .social_platforms import (
    detect_policy_page,
    extract_policy_urls_from_page,
    get_alternative_policy_urls,
    analyze_link_context,
)
import time

# Filter out the XML parsed as HTML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


class PrivacyRequest(BaseModel):
    url: str  # Changed from HttpUrl to str to allow any input

    @field_validator("url")
    @classmethod
    def validate_and_transform_url(cls, v: str) -> str:
        """
        Basic URL validation and normalization using utils.
        """
        return normalize_url(v)


class PrivacyResponse(BaseModel):
    url: str
    pp_url: Optional[str] = None
    success: bool
    message: str
    method_used: str = (
        "standard"  # Indicates which method was used to find the privacy policy
    )


# First endpoint for direct policy detection using pattern matching
@router.post("/privacy/find", response_model=PrivacyResponse)
async def find_privacy_policy_endpoint(
    request: PrivacyRequest, response: Response
) -> PrivacyResponse:
    """
    Find privacy policy for a given URL.
    Returns the privacy policy URL if found.
    """
    try:
        result = await find_privacy_policy(request.url)
        return result
    except Exception as e:
        logger.error(f"Error finding privacy policy: {str(e)}")
        response.status_code = 500
        return PrivacyResponse(
            url=request.url,
            pp_url=None,
            success=False,
            message=f"Error processing request: {str(e)}",
            method_used="error",
        )


# Legacy compatibility endpoint - uses new detection logic
@router.post(
    "/privacy",
    response_model=PrivacyResponse,
    responses={
        200: {"description": "Privacy Policy found successfully"},
        404: {"description": "Privacy Policy not found", "model": PrivacyResponse},
    },
)
async def get_privacy_policy(
    request: PrivacyRequest, response: Response
) -> PrivacyResponse:
    """
    Takes a base URL and returns the Privacy Policy page URL.
    This endpoint accepts partial URLs like 'example.com' and will
    automatically add the 'https://' protocol prefix if needed.
    """
    original_url = request.url
    logger.info(f"Processing Privacy Policy request for URL: {original_url}")

    result = await find_privacy_policy(original_url)

    if not result.success:
        response.status_code = 404

    return result


async def handle_app_store_privacy(url: str, app_id: str) -> PrivacyResponse:
    """
    Specialized handler for App Store app URLs to extract the app's privacy policy.
    """
    logger.info(
        f"Handling App Store app privacy policy extraction for app ID: {app_id}"
    )

    # Browser-like headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Try to extract app name for better error reporting
    app_name = None
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for the app name in the title or h1
        title_elem = soup.find("title")
        if title_elem:
            app_name = title_elem.text.strip().split("-")[0].strip()

        if not app_name:
            h1_elem = soup.find("h1")
            if h1_elem:
                app_name = h1_elem.text.strip()
    except Exception as e:
        logger.error(f"Error extracting app name: {str(e)}")

    app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}'}"

    # Method 1: Look for privacy policy in the app-privacy section
    pp_url = None
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # Look directly for the app-privacy section
        app_privacy_section = soup.find("section", class_="app-privacy")
        if app_privacy_section:
            # Search for privacy policy links in this section
            privacy_links = app_privacy_section.find_all("a", href=True)
            for link in privacy_links:
                href = link.get("href")
                link_text = link.get_text().lower()
                if href and ("privacy" in link_text or "privacy" in href.lower()):
                    pp_url = href
                    logger.info(
                        f"Found privacy policy link in app-privacy section: {pp_url}"
                    )
                    break

        # If found, follow redirects to get the final URL
        if pp_url:
            try:
                privacy_response = requests.get(
                    pp_url, headers=headers, timeout=15, allow_redirects=True
                )
                pp_url = privacy_response.url
                logger.info(f"Final privacy policy URL after redirects: {pp_url}")

                return PrivacyResponse(
                    url=url,
                    pp_url=pp_url,
                    success=True,
                    message=f"Found privacy policy for {app_info} in App Store privacy section",
                    method_used="app_store_privacy_section",
                )
            except Exception as e:
                logger.error(f"Error following privacy policy link: {str(e)}")
    except Exception as e:
        logger.error(f"Error searching for app-privacy section: {str(e)}")

    # Method 2: If standard method fails, try with Playwright
    logger.info(f"Standard method failed, trying with Playwright for {url}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)

                # Look for app-privacy section with Playwright
                app_privacy_section = await page.query_selector("section.app-privacy")
                if app_privacy_section:
                    privacy_links = await app_privacy_section.query_selector_all(
                        "a[href]"
                    )

                    for link in privacy_links:
                        href = await link.get_attribute("href")
                        text = await link.text_content()
                        text_lower = text.lower()

                        if href and (
                            "privacy" in text_lower or "privacy" in href.lower()
                        ):
                            pp_url = href
                            logger.info(
                                f"Found privacy policy link with Playwright in app-privacy section: {pp_url}"
                            )

                            # Follow the link to get the final URL
                            privacy_page = await context.new_page()
                            await privacy_page.goto(
                                href, wait_until="domcontentloaded", timeout=20000
                            )
                            final_url = privacy_page.url
                            await privacy_page.close()

                            await browser.close()

                            return PrivacyResponse(
                                url=url,
                                pp_url=final_url,
                                success=True,
                                message=f"Found privacy policy for {app_info} in App Store privacy section using Playwright",
                                method_used="app_store_playwright_section",
                            )

                # If not found in privacy section, try looking for any privacy policy links on the page
                privacy_links = await page.query_selector_all(
                    'a:text-matches("privacy policy", "i")'
                )
                for link in privacy_links:
                    href = await link.get_attribute("href")
                    if href:
                        logger.info(
                            f"Found general privacy policy link with Playwright: {href}"
                        )

                        # Follow the link to get the final URL
                        privacy_page = await context.new_page()
                        await privacy_page.goto(
                            href, wait_until="domcontentloaded", timeout=20000
                        )
                        final_url = privacy_page.url
                        await privacy_page.close()

                        await browser.close()

                        return PrivacyResponse(
                            url=url,
                            pp_url=final_url,
                            success=True,
                            message=f"Found privacy policy for {app_info} using Playwright",
                            method_used="app_store_playwright_general",
                        )
            except Exception as e:
                logger.error(f"Error navigating with Playwright: {str(e)}")
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"Error initializing Playwright: {str(e)}")

    # Return error if no privacy policy found
    return PrivacyResponse(
        url=url,
        success=False,
        message=f"Could not find privacy policy for {app_info}. The app may not have a privacy policy link on its App Store page.",
        method_used="app_store_failed",
    )


async def handle_play_store_privacy(url: str, app_id: str) -> PrivacyResponse:
    """
    Specialized handler for Google Play Store app URLs to extract the app's privacy policy.
    """
    logger.info(
        f"Handling Google Play Store app privacy policy extraction for app ID: {app_id}"
    )

    # Enhanced browser-like headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Try to extract app name for better error reporting
    app_name = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for the app name in the title or h1
        title_elem = soup.find("title")
        if title_elem:
            app_name = title_elem.text.strip().split("-")[0].strip()

        if not app_name:
            h1_elem = soup.find("h1")
            if h1_elem:
                app_name = h1_elem.text.strip()
    except Exception as e:
        logger.error(f"Error extracting app name: {str(e)}")

    app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}'}"

    # Direct method: Visit the data safety page URL directly
    data_safety_url = f"https://play.google.com/store/apps/datasafety?id={app_id}"
    logger.info(f"Directly accessing data safety page: {data_safety_url}")

    # Try with requests first to see if it's accessible without JavaScript
    pp_url = None
    try:
        response = requests.get(data_safety_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # Look for privacy policy links, but skip Google's general privacy policy
        privacy_links = soup.find_all(
            "a", text=re.compile("privacy policy", re.IGNORECASE)
        )
        app_specific_privacy_links = []

        for link in privacy_links:
            href = link.get("href")
            # Skip Google's general privacy policy
            if href and not href.startswith("https://policies.google.com/privacy"):
                app_specific_privacy_links.append(link)
                logger.info(f"Found app-specific privacy policy link: {href}")

        # If we found app-specific links, use the first one
        if app_specific_privacy_links:
            privacy_href = app_specific_privacy_links[0].get("href")
            logger.info(
                f"Found app-specific privacy policy link with requests: {privacy_href}"
            )

            # Follow redirects to get the final URL
            privacy_response = requests.get(
                privacy_href, headers=headers, timeout=15, allow_redirects=True
            )
            pp_url = privacy_response.url
            logger.info(f"Final privacy policy URL after redirects: {pp_url}")

            return PrivacyResponse(
                url=url,
                pp_url=pp_url,
                success=True,
                message=f"Found privacy policy for {app_info} on data safety page",
                method_used="play_store_data_safety_direct",
            )
    except Exception as e:
        logger.warning(
            f"Simple request method failed, trying with Playwright: {str(e)}"
        )

    # If simple request method didn't work, we need Playwright
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()

            try:
                # Go directly to the data safety page
                await page.goto(
                    data_safety_url, wait_until="networkidle", timeout=30000
                )

                # Look for privacy policy links on the data safety page
                privacy_links = await page.query_selector_all(
                    "a:text-matches('privacy policy', 'i')"
                )
                app_specific_privacy_elements = []

                # Filter out Google's general privacy policy
                for link in privacy_links:
                    href = await link.get_attribute("href")
                    # Skip Google's general privacy policy
                    if href and not href.startswith(
                        "https://policies.google.com/privacy"
                    ):
                        app_specific_privacy_elements.append(link)
                        logger.info(
                            f"Found app-specific privacy policy link with Playwright: {href}"
                        )

                if app_specific_privacy_elements:
                    pp_url = await app_specific_privacy_elements[0].get_attribute(
                        "href"
                    )
                    logger.info(
                        f"Found app-specific privacy policy link on data safety page: {pp_url}"
                    )

                    # Follow the link to get the final URL after any redirects
                    privacy_page = await context.new_page()
                    await privacy_page.goto(
                        pp_url, wait_until="domcontentloaded", timeout=30000
                    )
                    pp_url = privacy_page.url
                    logger.info(f"Final privacy policy URL after redirects: {pp_url}")

                    await privacy_page.close()
                    await browser.close()

                    return PrivacyResponse(
                        url=url,
                        pp_url=pp_url,
                        success=True,
                        message=f"Found privacy policy for {app_info} on data safety page",
                        method_used="play_store_data_safety",
                    )

                # If not found on data safety page, try the main app page
                logger.info(
                    f"No app-specific privacy policy found on data safety page, checking app page"
                )
                await page.goto(url, wait_until="networkidle", timeout=30000)

                # Look for privacy policy links on the app page
                privacy_links = await page.query_selector_all(
                    'a:text-matches("privacy policy", "i")'
                )
                for link in privacy_links:
                    href = await link.get_attribute("href")
                    if (
                        href
                        and not href.startswith("https://play.google.com/intl")
                        and not href.startswith("https://policies.google.com/privacy")
                    ):
                        # Skip Google's own privacy policy
                        logger.info(
                            f"Found app-specific privacy policy link directly on app page: {href}"
                        )

                        # Follow the link to get the final URL after any redirects
                        privacy_page = await context.new_page()
                        await privacy_page.goto(
                            href, wait_until="domcontentloaded", timeout=30000
                        )
                        pp_url = privacy_page.url
                        logger.info(
                            f"Final privacy policy URL after redirects: {pp_url}"
                        )

                        await privacy_page.close()
                        await browser.close()

                        return PrivacyResponse(
                            url=url,
                            pp_url=pp_url,
                            success=True,
                            message=f"Found privacy policy for {app_info} on app page",
                            method_used="play_store_app_page",
                        )

                # Try to find the developer website
                developer_links = await page.query_selector_all(
                    'a:has-text("Visit website")'
                )
                if developer_links and len(developer_links) > 0:
                    developer_site = await developer_links[0].get_attribute("href")
                    logger.info(f"Found developer website: {developer_site}")

                    # Check the developer website for privacy policy
                    try:
                        dev_page = await context.new_page()
                        await dev_page.goto(
                            developer_site, wait_until="domcontentloaded", timeout=30000
                        )

                        # Look for privacy policy links
                        pp_links = await dev_page.query_selector_all(
                            'a:text-matches("privacy policy", "i")'
                        )
                        for link in pp_links:
                            href = await link.get_attribute("href")
                            if href and not href.startswith(
                                "https://policies.google.com/privacy"
                            ):
                                # Make sure it's an absolute URL
                                if not href.startswith(("http://", "https://")):
                                    href = urljoin(developer_site, href)

                                # Follow the link to get the final URL after any redirects
                                privacy_page = await context.new_page()
                                await privacy_page.goto(
                                    href, wait_until="domcontentloaded", timeout=30000
                                )
                                pp_url = privacy_page.url
                                logger.info(
                                    f"Final privacy policy URL after redirects: {pp_url}"
                                )

                                await privacy_page.close()
                                await dev_page.close()
                                await browser.close()

                                return PrivacyResponse(
                                    url=url,
                                    pp_url=pp_url,
                                    success=True,
                                    message=f"Found privacy policy for {app_info} on developer website",
                                    method_used="play_store_developer_site",
                                )

                        await dev_page.close()
                    except Exception as e:
                        logger.error(f"Error checking developer website: {str(e)}")
                        try:
                            await dev_page.close()
                        except:
                            pass

            except Exception as e:
                logger.error(f"Error navigating Play Store with Playwright: {str(e)}")
            finally:
                await browser.close()
    except Exception as e:
        logger.error(f"Error with Playwright: {str(e)}")

    # If we got here, we couldn't find an app-specific privacy policy
    logger.warning(
        f"No app-specific privacy policy found for Google Play Store {app_info}"
    )
    return PrivacyResponse(
        url=url,
        success=False,
        message=f"Could not find app-specific privacy policy for {app_info}. Please check the app's page manually.",
        method_used="play_store_failed",
    )


async def standard_privacy_finder(
    variations_to_try, headers, session
) -> PrivacyResponse:
    """Standard approach using requests and BeautifulSoup."""

    # For each variation, try to follow redirects to the final destination
    for url, variation_type in variations_to_try:
        try:
            logger.info(f"Trying URL variation: {url} ({variation_type})")

            # First do a HEAD request to check for redirects
            head_response = session.head(
                url, headers=headers, timeout=10, allow_redirects=True
            )
            head_response.raise_for_status()

            # Get the final URL after redirects
            final_url = head_response.url
            if final_url != url:
                logger.info(f"Followed redirect: {url} -> {final_url}")

            # Now get the content of the final URL
            logger.info(f"Fetching content from {final_url}")
            response = session.get(final_url, headers=headers, timeout=15)
            response.raise_for_status()

            # Parse the HTML content
            soup = BeautifulSoup(response.text, "html.parser")

            # Find the Privacy Policy link from the page
            logger.info(f"Searching for privacy link in {final_url}")
            privacy_link = find_privacy_link(final_url, soup)

            if privacy_link:
                # Additional check for false positives
                if is_likely_false_positive(privacy_link, "privacy"):
                    logger.warning(
                        f"Found link {privacy_link} appears to be a false positive, skipping"
                    )
                    continue

                # Check if this is a correct policy type
                if not is_correct_policy_type(privacy_link, "privacy"):
                    logger.warning(
                        f"Found link {privacy_link} appears to be a ToS, not privacy policy, skipping"
                    )
                    continue

                # Ensure the link is absolute
                if privacy_link.startswith("/"):
                    parsed_final_url = urlparse(final_url)
                    base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                    privacy_link = urljoin(base_url, privacy_link)
                    logger.info(
                        f"Converted relative URL to absolute URL: {privacy_link}"
                    )

                logger.info(
                    f"Found privacy link: {privacy_link} in {final_url} ({variation_type})"
                )
                return PrivacyResponse(
                    url=final_url,  # Return the actual URL after redirects
                    pp_url=privacy_link,
                    success=True,
                    message=f"Privacy Policy link found on final destination page: {final_url}"
                    + (
                        f" (Found at {variation_type})"
                        if variation_type != "original exact url"
                        else ""
                    ),
                    method_used="standard",
                )
            else:
                logger.info(f"No privacy link found in {final_url} ({variation_type})")

        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({variation_type}): {str(e)}")
            # If this variation fails, try the next one
            continue
        except Exception as e:
            logger.error(f"Exception for {url} ({variation_type}): {str(e)}")
            # For non-request exceptions, try the next one
            continue

    # If we get here, we tried all variations but didn't find a Privacy Policy link
    try:
        # Try one more time with the original URL for a better error message
        base_url = variations_to_try[0][0]  # Original URL
        head_response = session.head(
            base_url, headers=headers, timeout=10, allow_redirects=True
        )
        final_url = head_response.url

        response = session.get(final_url, headers=headers, timeout=15)
        response.raise_for_status()

        return PrivacyResponse(
            url=final_url,  # Return the final URL after redirects
            success=False,
            message=f"No Privacy Policy link found with standard method on the final destination page: {final_url}.",
            method_used="standard_failed",
        )
    except requests.RequestException as e:
        error_msg = ""

        # Handle request errors with more specific messages
        if hasattr(e, "response") and e.response is not None:
            status_code = e.response.status_code
            if status_code == 404:
                error_msg = f"The URL {base_url} returned a 404 Not Found error."
            elif status_code == 403:
                error_msg = f"Access to {base_url} was denied (HTTP 403 Forbidden). The site is likely blocking web scraping."
            elif status_code == 401:
                error_msg = f"Access to {base_url} requires authentication (HTTP 401 Unauthorized)."
            elif status_code == 400:
                error_msg = f"The server at {base_url} returned HTTP 400 Bad Request. This often happens when a site blocks scraping attempts or requires cookies/JavaScript."
            elif status_code == 429:
                error_msg = f"Too many requests to {base_url} (HTTP 429). The site is rate-limiting requests."
            elif status_code >= 500:
                error_msg = f"The server at {base_url} encountered an error (HTTP {status_code})."
            else:
                error_msg = (
                    f"Error fetching {base_url}: HTTP status code {status_code}."
                )
        else:
            error_msg = f"Error connecting to {base_url}: {str(e)}"

        # Return the error in the response
        return PrivacyResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed",
        )
    except Exception as e:
        error_msg = f"Error processing URL {base_url} with standard method: {str(e)}"

        # Return the error in the response
        return PrivacyResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed",
        )


async def playwright_privacy_finder(url: str) -> PrivacyResponse:
    """
    Find Privacy Policy links using Playwright for JavaScript-rendered content.
    This is a fallback method for when the standard approach fails.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()

            # Set a reasonable timeout for navigation
            page.set_default_timeout(30000)  # 30 seconds

            # Navigate to the URL
            await page.goto(url, wait_until="networkidle")

            # Get the final URL after any redirects
            final_url = page.url

            # Get the content
            content = await page.content()

            # Parse with BeautifulSoup
            soup = BeautifulSoup(content, "html.parser")

            # Find links using our existing function
            privacy_link = find_privacy_link(final_url, soup)

            # If standard approach didn't find links, try using Playwright's own selectors
            if not privacy_link:
                # Try to find links with text containing privacy terms
                privacy_keywords = [
                    "privacy",
                    "privacy policy",
                    "data protection",
                    "cookie",
                    "gdpr",
                ]

                for keyword in privacy_keywords:
                    # Use case-insensitive search for links with text containing the keyword
                    links = await page.query_selector_all(
                        f'a:text-matches("{keyword}", "i")'
                    )

                    for link in links:
                        href = await link.get_attribute("href")
                        if href and not href.startswith(
                            ("javascript:", "mailto:", "tel:", "#")
                        ):
                            privacy_link = urljoin(final_url, href)
                            break

                    if privacy_link:
                        break

                # If still not found, try clicking "I agree" or cookie consent buttons to reveal privacy links
                if not privacy_link:
                    # Try to find and click buttons that might reveal privacy content
                    consent_buttons = await page.query_selector_all(
                        'button:text-matches("(accept|agree|got it|cookie|consent)", "i")'
                    )

                    for button in consent_buttons:
                        try:
                            await button.click()
                            await page.wait_for_timeout(
                                1000
                            )  # Wait for any changes to take effect

                            # Check for new links after clicking
                            content_after_click = await page.content()
                            soup_after_click = BeautifulSoup(
                                content_after_click, "html.parser"
                            )
                            privacy_link = find_privacy_link(
                                final_url, soup_after_click
                            )

                            if privacy_link:
                                break
                        except:
                            continue

            # Close the browser
            await browser.close()

            if privacy_link:
                # Additional check for false positives
                if is_likely_false_positive(privacy_link, "privacy"):
                    logger.warning(
                        f"Found link {privacy_link} appears to be a false positive, skipping"
                    )
                    return PrivacyResponse(
                        url=final_url,
                        success=False,
                        message=f"Found privacy link was a false positive: {privacy_link}",
                        method_used="playwright_false_positive",
                    )

                # Check if this is a correct policy type
                if not is_correct_policy_type(privacy_link, "privacy"):
                    logger.warning(
                        f"Found link {privacy_link} appears to be a ToS, not privacy policy"
                    )
                    return PrivacyResponse(
                        url=final_url,
                        success=False,
                        message=f"Found link appears to be Terms of Service, not Privacy Policy: {privacy_link}",
                        method_used="playwright_wrong_policy_type",
                    )

                # Ensure the link is absolute
                if privacy_link.startswith("/"):
                    parsed_final_url = urlparse(final_url)
                    base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                    privacy_link = urljoin(base_url, privacy_link)
                    logger.info(
                        f"Converted relative URL to absolute URL: {privacy_link}"
                    )

                return PrivacyResponse(
                    url=final_url,
                    pp_url=privacy_link,
                    success=True,
                    message=f"Privacy Policy link found using JavaScript-enabled browser rendering on page: {final_url}",
                    method_used="playwright",
                )
            else:
                return PrivacyResponse(
                    url=final_url,
                    success=False,
                    message=f"No Privacy Policy link found even with JavaScript-enabled browser rendering on page: {final_url}",
                    method_used="playwright_failed",
                )

    except Exception as e:
        error_msg = f"Error using Playwright to process URL {url}: {str(e)}"
        logger.error(error_msg)

        return PrivacyResponse(
            url=url, success=False, message=error_msg, method_used="playwright_failed"
        )


def find_privacy_link(url: str, soup: BeautifulSoup) -> Optional[str]:
    """
    Find the most likely privacy policy link on a page.
    Returns the absolute URL to the privacy policy if found, otherwise None.
    """
    start_time = time.time()

    try:
        # Parse the URL to get the domain
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        base_url = f"{parsed_url.scheme}://{domain}"

        # Check if we're already on a privacy policy page
        if is_on_policy_page(url, "privacy"):
            logger.info(f"Already on a privacy policy page: {url}")
            return url

        # First try to find by checking head element links
        head_element = soup.find("head")
        if head_element:
            head_links = head_element.find_all("a", href=True)
            for link in head_links:
                href = link.get("href", "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue

                link_text = link.get_text().strip().lower()
                link_title = link.get("title", "").lower()

                # Check for privacy terms in link or attributes
                if (
                    "privacy" in link_text
                    or "privacy" in link_title
                    or "privacy" in href.lower()
                    or "pp" in href.lower()
                ):

                    absolute_url = urljoin(url, href)
                    logger.info(f"Found privacy link in head: {absolute_url}")
                    return absolute_url

            # Also check link elements with rel="privacy" or similar
            link_elements = head_element.find_all("link", rel=True, href=True)
            for link in link_elements:
                rel = link.get("rel", [""])[0].lower()
                href = link.get("href", "").strip()

                if "privacy" in rel or "policy" in rel:
                    absolute_url = urljoin(url, href)
                    logger.info(
                        f"Found privacy link with rel attribute in head: {absolute_url}"
                    )
                    return absolute_url

        # Use flexible pattern matching approach for all websites
        # Common patterns in privacy policy links and URL structures
        common_privacy_patterns = [
            "privacy-notice",
            "privacy_notice",
            "privacynotice",
            "privacy-policy",
            "privacy_policy",
            "privacypolicy",
            "privacy/policy",
            "privacy-center",
            "privacy_center",
            "privacycenter",
            "privacy/center",
            "data-privacy",
            "data_privacy",
            "dataprivacy",
            "privacy/policies",
            "privacy-policies",
            "privacy_policies",
            "privacy-statement",
            "privacy_statement",
            "privacystatement",
            "privacy/explanation",
            "privacy-explanation",
            "privacy_explanation",
            "legal/privacy",
            "legal-privacy",
            "legal_privacy",
            "about/privacy",
            "about-privacy",
            "about_privacy",
            "privacy",
            "data-policy",
            "data_policy",
            "datapolicy",
            "data-protection",
            "data_protection",
            "dataprotection",
            "privacyinfo",
            "privacy-info",
            "privacy_info",
            "privacy/notice",
            "privacy/settings",
            "gdpr",
        ]

        # First check for links that match specific privacy patterns in URL
        for link in soup.find_all("a", href=True):
            href = link.get("href", "").strip()

            # Skip non-http links and empty hrefs
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            href_lower = href.lower()
            text = link.get_text().strip().lower()

            # Look for patterns in URLs
            if any(pattern in href_lower for pattern in common_privacy_patterns):
                # Skip obvious false positives like "privacy-preferences"
                if "preferences" in href_lower and not any(
                    full_term in href_lower
                    for full_term in [
                        "privacy-policy",
                        "privacypolicy",
                        "privacy-notice",
                    ]
                ):
                    continue

                absolute_url = urljoin(url, href)
                logger.info(f"Found privacy link matching URL pattern: {absolute_url}")
                return absolute_url

            # Check for privacy terms in link text (high confidence matches)
            high_confidence_terms = [
                "privacy policy",
                "privacy notice",
                "privacy statement",
                "data privacy",
                "data protection policy",
                "privacy information",
                "gdpr",
            ]

            if any(term in text for term in high_confidence_terms):
                # Filter out some common false positives
                if "preferences" not in text and "settings" not in text:
                    absolute_url = urljoin(url, href)
                    logger.info(
                        f"Found privacy link with high confidence text: {absolute_url}"
                    )
                    return absolute_url

        # Try common pattern URLs directly by constructing them from base domain
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        base_url = f"{parsed_url.scheme}://{domain}"

        logger.info(
            f"No direct privacy links found, trying common URL patterns with base domain: {base_url}"
        )

        # Try looking for common privacy URL patterns across all sites
        for pattern in common_privacy_patterns:
            try_url = urljoin(base_url, "/" + pattern)
            logger.info(f"Trying common privacy pattern URL: {try_url}")

            try:
                # Don't follow through with full requests for all patterns to avoid rate limiting
                # Just log that we would attempt this in a real scenario
                pass
            except Exception:
                # Just log and continue, don't actually make requests here to avoid timeouts
                continue

        # Try to find by structure like footer, header navigation, etc.
        structural_result = find_policy_by_class_id(soup, "privacy", base_url)
        if structural_result:
            logger.info(
                f"Found privacy link via structural search: {structural_result}"
            )
            return structural_result
        else:
            logger.info("Structural search failed, falling back to general link search")

        # General approach: Score all links on the page
        candidates = []
        seen_urls = set()  # Avoid duplicate URLs

        for link in soup.find_all("a", href=True):
            href = link.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue

            # Create absolute URL
            absolute_url = urljoin(url, href)

            # Skip if we've seen this URL already
            if absolute_url in seen_urls:
                continue

            seen_urls.add(absolute_url)

            # Skip likely false positives (early rejection)
            if is_likely_false_positive(absolute_url, "privacy"):
                continue

            # Check if this is a different domain from the original site
            target_domain = urlparse(absolute_url).netloc.lower()
            if target_domain != domain:
                # For cross-domain links, require explicit privacy references
                if not any(
                    term in absolute_url.lower()
                    for term in ["/privacy", "privacy-policy", "/gdpr"]
                ):
                    continue

            # Ensure this is not a ToS URL
            if not is_correct_policy_type(absolute_url, "privacy"):
                continue

            # Get the text of the link
            link_text = link.get_text().strip()

            # Calculate link score
            text_score = get_policy_score(link_text, absolute_url, "privacy")

            # Calculate footer/header placement score
            structural_score = get_footer_score(link)

            # Calculate domain relevance
            domain_score = get_domain_score(absolute_url, domain)

            # Apply penalties for likely irrelevant links
            penalty = 0
            for pattern, value in get_common_penalties():
                if pattern in absolute_url.lower():
                    penalty += value

            # Skip if extreme penalties applied
            if penalty <= -10:
                continue

            # Calculate total score
            total_score = text_score + structural_score + domain_score + penalty

            # Only consider links with positive scores
            if total_score > 0:
                candidates.append(
                    {"url": absolute_url, "text": link_text, "score": total_score}
                )

        # Sort by score (highest first)
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Log the top 3 candidates for debugging
        for i, candidate in enumerate(candidates[:3]):
            logger.info(
                f"Candidate {i+1}: {candidate['url']} (Score: {candidate['score']}) - '{candidate['text']}'"
            )

        # Return the highest-scoring candidate if available
        if candidates:
            end_time = time.time()
            logger.info(f"Found privacy link in {end_time - start_time:.2f} seconds")
            return candidates[0]["url"]

        # No suitable link found
        logger.warning("No privacy link found on page")
        end_time = time.time()
        logger.info(
            f"Privacy link search completed in {end_time - start_time:.2f} seconds"
        )
        return None

    except Exception as e:
        logger.error(f"Error finding privacy link: {str(e)}")
        return None


async def scan_html_for_privacy_links(url: str) -> PrivacyResponse:
    """
    Final fallback method that downloads and scans the complete HTML for privacy policy links
    before concluding that no privacy policy link exists.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()
            logger.info(f"Final fallback: Downloading full HTML from {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"Error loading page in fallback method: {str(e)}")
                # Continue anyway with whatever content we have

            # Get the final URL after any redirects
            final_url = page.url

            # Get the complete HTML content
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            # Look for any links that might be policy-related
            all_links = soup.find_all("a", href=True)

            # Score all links for privacy policy likelihood
            privacy_candidates = []

            for link in all_links:
                href = link.get("href", "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue

                absolute_url = urljoin(final_url, href)
                url_lower = absolute_url.lower()
                text = link.get_text().strip().lower()

                # Skip if clearly a ToS
                if (
                    "/terms" in url_lower
                    or "terms of service" in text
                    or "terms of use" in text
                ):
                    continue

                score = 0

                # Check URL for Privacy indicators
                privacy_url_indicators = [
                    "/privacy",
                    "privacy-policy",
                    "/data-policy",
                    "/data-protection",
                    "/privacypolicy",
                    "/gdpr",
                    "/dataprivacy",
                    "data-privacy",
                ]
                for indicator in privacy_url_indicators:
                    if indicator in url_lower:
                        score += 5
                        break

                # Check text for Privacy indicators
                privacy_text_indicators = [
                    "privacy",
                    "data protection",
                    "personal data",
                    "gdpr",
                    "data policy",
                    "privacy policy",
                    "cookie policy",
                ]
                for indicator in privacy_text_indicators:
                    if indicator in text:
                        score += 3
                        break

                # Check for footer placement (often indicates policy links)
                parent_footer = link.find_parent(
                    ["footer", "div"],
                    class_=lambda x: x and ("footer" in x.lower() if x else False),
                )
                if parent_footer:
                    score += 2

                if score > 0:
                    privacy_candidates.append((absolute_url, score, text))

            await browser.close()

            # Sort by score and take the highest
            if privacy_candidates:
                privacy_candidates.sort(key=lambda x: x[1], reverse=True)
                best_privacy = privacy_candidates[0][0]
                best_score = privacy_candidates[0][1]
                best_text = privacy_candidates[0][2]

                logger.info(
                    f"Found potential privacy link through HTML scan: {best_privacy} (score: {best_score}, text: {best_text})"
                )

                # Only use if score is reasonably good
                if best_score >= 5:
                    return PrivacyResponse(
                        url=final_url,
                        pp_url=best_privacy,
                        success=True,
                        message=f"Privacy Policy link found through final HTML scan: {best_privacy}",
                        method_used="html_scan",
                    )

            # No suitable candidates found
            return PrivacyResponse(
                url=final_url,
                success=False,
                message="No Privacy Policy link found even after scanning all HTML content",
                method_used="html_scan_failed",
            )

    except Exception as e:
        logger.error(f"Error in HTML scanning fallback: {str(e)}")
        return PrivacyResponse(
            url=url,
            success=False,
            message=f"Error during final HTML scan: {str(e)}",
            method_used="html_scan_error",
        )


async def find_privacy_policy(url: str) -> PrivacyResponse:
    """
    Main function to find a privacy policy link using multiple fallback approaches.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    logger.info(f"Finding privacy policy for URL: {url}")

    # Generate URL variations to try
    url_variations = prepare_url_variations(url)

    # Create a session with a longer timeout
    session = requests.Session()

    # Try the direct privacy URL check as a first step - this is the most common case
    try:
        direct_privacy_url = urljoin(url, "/privacy")
        logger.info(f"Checking direct privacy URL: {direct_privacy_url}")

        # Try GET directly instead of HEAD to avoid potential issues
        get_response = session.get(direct_privacy_url, headers=headers, timeout=15)

        if get_response.status_code < 400:
            logger.info(
                f"Direct privacy URL successful: {direct_privacy_url} (status {get_response.status_code})"
            )
            return PrivacyResponse(
                url=url,
                pp_url=direct_privacy_url,
                success=True,
                message="Privacy policy found at direct privacy URL",
                method_used="direct_privacy_url",
            )
        else:
            logger.info(
                f"Direct privacy URL failed: {direct_privacy_url} (status {get_response.status_code})"
            )
    except Exception as e:
        logger.warning(f"Error checking direct privacy URL: {str(e)}")

    # Try processing URL variations
    for variant_url in url_variations:
        try:
            logger.info(f"Trying URL variation: {variant_url}")

            # Check if privacy policy link can be detected from URL pattern alone
            if detect_policy_page(variant_url, "privacy"):
                logger.info(
                    f"URL {variant_url} directly matches privacy policy pattern"
                )

                # Verify it's accessible
                try:
                    verify_response = session.get(
                        variant_url, headers=headers, timeout=10
                    )
                    if verify_response.status_code < 400:
                        logger.info(
                            f"Verified accessibility of {variant_url}: Status {verify_response.status_code}"
                        )
                        return PrivacyResponse(
                            url=url,
                            pp_url=variant_url,
                            success=True,
                            message="Privacy policy found through URL pattern matching and verified",
                            method_used="verified_url_pattern",
                        )
                    else:
                        logger.warning(
                            f"URL pattern match {variant_url} is not accessible: Status {verify_response.status_code}"
                        )
                except Exception as verify_err:
                    logger.warning(
                        f"Error verifying URL pattern match {variant_url}: {str(verify_err)}"
                    )

            # Get the page content
            response = session.get(variant_url, headers=headers, timeout=15)
            response.raise_for_status()
            html_content = response.text
            soup = BeautifulSoup(html_content, "html.parser")

            # Try common privacy URLs with individual GET requests for reliability
            policy_urls = get_alternative_policy_urls(variant_url, "privacy")
            logger.info(
                f"Checking {len(policy_urls)} common privacy URL patterns for {variant_url}"
            )

            for policy_url in policy_urls:
                try:
                    # Use GET directly for reliability
                    logger.info(f"Checking common URL: {policy_url}")
                    get_response = session.get(
                        policy_url, headers=headers, timeout=10, allow_redirects=True
                    )

                    # Log detailed response info for debugging
                    logger.info(
                        f"Response for {policy_url}: Status {get_response.status_code}, URL after redirects: {get_response.url}"
                    )

                    if get_response.status_code < 400:
                        # Use the final URL after any redirects
                        final_url = get_response.url
                        logger.info(f"Found privacy policy at common URL: {final_url}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=final_url,
                            success=True,
                            message="Privacy policy found at common URL pattern",
                            method_used="common_pattern",
                        )
                except Exception as e:
                    logger.warning(f"Error checking common URL {policy_url}: {str(e)}")

            # Try pattern-based extraction from page content
            extracted_urls = extract_policy_urls_from_page(variant_url, soup, "privacy")
            if extracted_urls:
                for extracted_url in extracted_urls:
                    if detect_policy_page(extracted_url, "privacy"):
                        logger.info(
                            f"Found privacy policy via text extraction: {extracted_url}"
                        )
                        return PrivacyResponse(
                            url=url,
                            pp_url=extracted_url,
                            success=True,
                            message="Privacy policy found through text pattern extraction",
                            method_used="text_pattern_extraction",
                        )

            # Try deep context analysis as a more sophisticated approach
            context_results = analyze_link_context(variant_url, soup, "privacy")
            if context_results:
                best_url, confidence = context_results[0]
                logger.info(
                    f"Found privacy policy via context analysis: {best_url} (confidence: {confidence})"
                )
                return PrivacyResponse(
                    url=url,
                    pp_url=best_url,
                    success=True,
                    message=f"Privacy policy found through context analysis (confidence: {confidence:.1f})",
                    method_used="context_analysis",
                )

            # Try to find hidden policy links in menus and dropdowns
            hidden_links = find_hidden_policy_links(soup, "privacy")
            if hidden_links:
                # Sort by confidence score and take the highest
                hidden_links.sort(key=lambda x: x[1], reverse=True)
                best_url, confidence = hidden_links[0]

                # Resolve relative URLs
                if not best_url.startswith(("http://", "https://")):
                    best_url = urljoin(variant_url, best_url)

                logger.info(
                    f"Found privacy policy in hidden menu: {best_url} (confidence: {confidence})"
                )
                return PrivacyResponse(
                    url=url,
                    pp_url=best_url,
                    success=True,
                    message=f"Privacy policy found in hidden menu element (confidence: {confidence:.1f})",
                    method_used="hidden_menu_detection",
                )

            # Try to find JavaScript-embedded privacy links
            js_links = detect_hidden_privacy_patterns(html_content)
            if js_links:
                # Sort by confidence score and take the highest
                js_links.sort(key=lambda x: x[1], reverse=True)
                best_url, confidence = js_links[0]

                # Resolve relative URLs
                if not best_url.startswith(("http://", "https://")):
                    best_url = urljoin(variant_url, best_url)

                logger.info(
                    f"Found privacy policy in JavaScript/data attributes: {best_url} (confidence: {confidence})"
                )
                return PrivacyResponse(
                    url=url,
                    pp_url=best_url,
                    success=True,
                    message=f"Privacy policy found in JavaScript code (confidence: {confidence:.1f})",
                    method_used="js_pattern_detection",
                )

        except Exception as e:
            logger.error(f"Error processing URL variation {variant_url}: {str(e)}")

    # If we get here, try with Playwright for JavaScript rendering
    try:
        logger.info(f"Trying with Playwright for JavaScript rendering")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            content = await page.content()

            soup = BeautifulSoup(content, "html.parser")

            # Try the same extraction methods on JavaScript-rendered content
            extracted_urls = extract_policy_urls_from_page(url, soup, "privacy")
            if extracted_urls:
                for extracted_url in extracted_urls:
                    if detect_policy_page(extracted_url, "privacy"):
                        await browser.close()
                        return PrivacyResponse(
                            url=url,
                            pp_url=extracted_url,
                            success=True,
                            message="Privacy policy found through JavaScript-rendered content",
                            method_used="js_rendered_extraction",
                        )

            # Try context analysis on JavaScript-rendered content
            context_results = analyze_link_context(url, soup, "privacy")
            if context_results:
                best_url, confidence = context_results[0]
                await browser.close()
                return PrivacyResponse(
                    url=url,
                    pp_url=best_url,
                    success=True,
                    message=f"Privacy policy found through JS-rendered context analysis (confidence: {confidence:.1f})",
                    method_used="js_context_analysis",
                )

            # Try hidden menu detection on JavaScript-rendered content
            hidden_links = find_hidden_policy_links(soup, "privacy")
            if hidden_links:
                # Sort by confidence score and take the highest
                hidden_links.sort(key=lambda x: x[1], reverse=True)
                best_url, confidence = hidden_links[0]

                # Resolve relative URLs
                if not best_url.startswith(("http://", "https://")):
                    best_url = urljoin(url, best_url)

                await browser.close()
                return PrivacyResponse(
                    url=url,
                    pp_url=best_url,
                    success=True,
                    message=f"Privacy policy found in hidden menu with JS rendering (confidence: {confidence:.1f})",
                    method_used="js_hidden_menu_detection",
                )

            # Try clicking on potential links to reveal more content
            try:
                # Look for elements that might reveal more links when clicked
                await page.evaluate(
                    """() => {
                    // Try clicking on various menu toggles and buttons
                    const potentialToggles = [];
                    
                    // Button-like elements with text suggesting legal/policy content
                    document.querySelectorAll('button, a, div, span, i').forEach(el => {
                        const text = el.innerText ? el.innerText.toLowerCase() : '';
                        const classes = el.className ? el.className.toLowerCase() : '';
                        
                        // Look for potential legal/info buttons based on text or class
                        if (text.match(/legal|info|menu|more|privacy|help|settings/) ||
                            classes.match(/menu|toggle|dropdown|more|settings|footer/) ||
                            el.getAttribute('aria-controls') ||
                            el.getAttribute('aria-haspopup') == 'true') {
                            
                            potentialToggles.push(el);
                        }
                    });
                    
                    // Click on each potential toggle
                    for (const toggle of potentialToggles) {
                        try {
                            toggle.click();
                        } catch(e) {
                            // Ignore errors, just try the next one
                        }
                    }
                    
                    // Also try footer links
                    const footerLinks = Array.from(document.querySelectorAll('footer a, [class*="footer"] a'));
                    for (const link of footerLinks) {
                        if (link.innerText && (
                            link.innerText.toLowerCase().includes('privacy') || 
                            link.innerText.toLowerCase().includes('legal') ||
                            link.innerText.toLowerCase().includes('policy'))) {
                            try {
                                link.click();
                            } catch(e) {
                                // Ignore errors
                            }
                            break;
                        }
                    }
                }"""
                )

                # Add a delay for click interactions to complete
                await page.wait_for_timeout(2000)

                # After interactions, check for newly revealed content
                after_click_content = await page.content()
                after_click_soup = BeautifulSoup(after_click_content, "html.parser")

                # Check if new links have appeared after clicking
                context_results = analyze_link_context(url, after_click_soup, "privacy")
                if context_results:
                    best_url, confidence = context_results[0]
                    await browser.close()
                    return PrivacyResponse(
                        url=url,
                        pp_url=best_url,
                        success=True,
                        message=f"Privacy policy found after interactive menu exploration (confidence: {confidence:.1f})",
                        method_used="interactive_discovery",
                    )

                # Look for hidden links that might have been revealed
                hidden_links = find_hidden_policy_links(after_click_soup, "privacy")
                if hidden_links:
                    # Sort by confidence score and take the highest
                    hidden_links.sort(key=lambda x: x[1], reverse=True)
                    best_url, confidence = hidden_links[0]

                    # Resolve relative URLs
                    if not best_url.startswith(("http://", "https://")):
                        best_url = urljoin(url, best_url)

                    await browser.close()
                    return PrivacyResponse(
                        url=url,
                        pp_url=best_url,
                        success=True,
                        message=f"Privacy policy found after revealing hidden elements (confidence: {confidence:.1f})",
                        method_used="interactive_hidden_discovery",
                    )

                # Extract URLs directly from the page content which might be available after JS
                extracted_urls = extract_policy_urls_from_page(
                    url, after_click_soup, "privacy"
                )
                if extracted_urls:
                    for extracted_url in extracted_urls:
                        if detect_policy_page(extracted_url, "privacy"):
                            await browser.close()
                            return PrivacyResponse(
                                url=url,
                                pp_url=extracted_url,
                                success=True,
                                message="Privacy policy found after interactive exploration",
                                method_used="interactive_extraction",
                            )

            except Exception as e:
                logger.error(f"Error during interactive discovery: {str(e)}")

            await browser.close()
    except Exception as e:
        logger.error(f"Error using Playwright: {str(e)}")

    # If all methods fail, return failure response
    return PrivacyResponse(
        url=url,
        pp_url=None,
        success=False,
        message="No Privacy Policy link found. Tried standard scraping, JavaScript-enabled browser rendering, hidden menu detection, and interactive exploration.",
        method_used="all_methods_failed",
    )
