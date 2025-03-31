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
from .utils import normalize_url, prepare_url_variations

# Filter out the XML parsed as HTML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


class TosRequest(BaseModel):
    url: str  # Changed from HttpUrl to str to allow any input

    @field_validator('url')
    @classmethod
    def validate_and_transform_url(cls, v: str) -> str:
        """
        Basic URL validation and normalization using utils.
        """
        return normalize_url(v)


class TosResponse(BaseModel):
    url: str
    tos_url: Optional[str] = None
    success: bool
    message: str
    method_used: str = "standard"  # Indicates which method was used to find the ToS


@router.post("/tos", response_model=TosResponse, responses={
    200: {"description": "Terms of Service found successfully"},
    404: {"description": "Terms of Service not found", "model": TosResponse}
})
async def find_tos(request: TosRequest, response: Response) -> TosResponse:
    """
    Takes a base URL and returns the Terms of Service page URL.
    This endpoint accepts partial URLs like 'google.com' and will
    automatically add the 'https://' protocol prefix if needed.
    
    Features a fallback to Playwright for JavaScript-heavy sites.
    """
    original_url = request.url  # The exact URL provided by the user, already normalized
    
    logger.info(f"Processing ToS request for URL: {original_url}")
    
    # Check if this is an App Store app URL
    is_app_store_app = False
    app_id = None
    
    # Parse the URL to check if it's an App Store app URL
    parsed_url = urlparse(original_url)
    if ('apps.apple.com' in parsed_url.netloc or 'itunes.apple.com' in parsed_url.netloc):
        # Check if the path contains an app ID
        app_id_match = re.search(r'/id(\d+)', parsed_url.path)
        if app_id_match:
            is_app_store_app = True
            app_id = app_id_match.group(1)
            logger.info(f"Detected App Store app URL with ID: {app_id}")
    
    # Check if this is a Google Play Store app URL
    is_play_store_app = False
    play_app_id = None
    
    if 'play.google.com/store/apps' in original_url:
        # Extract the app ID from the URL
        play_app_id_match = re.search(r'[?&]id=([^&]+)', original_url)
        if play_app_id_match:
            is_play_store_app = True
            play_app_id = play_app_id_match.group(1)
            logger.info(f"Detected Google Play Store app URL with ID: {play_app_id}")
    
    # If this is an App Store app URL, handle it specially
    if is_app_store_app and app_id:
        result = await handle_app_store_tos(original_url, app_id)
        # Set status code to 404 if ToS not found
        if not result.success:
            response.status_code = 404
        return result
    
    # If this is a Google Play Store app URL, handle it specially
    if is_play_store_app and play_app_id:
        result = await handle_play_store_tos(original_url, play_app_id)
        # Set status code to 404 if ToS not found
        if not result.success:
            response.status_code = 404
        return result
    
    # Enhanced browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Sec-CH-UA': '"Chromium";v="123", "Google Chrome";v="123"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"macOS"',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
    }

    # Create a session to maintain cookies across requests
    session = requests.Session()
    
    # Get URL variations to try from our utility function
    variations_with_types = []
    
    # First prioritize the exact URL provided by the user
    variations_with_types.append((original_url, "original exact url"))
    
    # Then get base domain variations for fallback
    variations = prepare_url_variations(original_url)
    for idx, var_url in enumerate(variations[1:], 1):  # Skip the first one as it's the original
        variations_with_types.append((var_url, f"variation_{idx}"))
    
    logger.info(f"URL variations to try: {variations_with_types}")
    
    # Try the standard method first (requests + BeautifulSoup)
    standard_result = await standard_tos_finder(variations_with_types, headers, session)
    if standard_result.success:
        logger.info(f"Found ToS link with standard method: {standard_result.tos_url}")
        return standard_result
    
    # If standard method fails, try with Playwright
    logger.info(f"Standard method failed for {original_url}, trying with Playwright")
    
    # First try the specific URL with Playwright
    playwright_result = await playwright_tos_finder(original_url)
    
    # Get the base domain
    parsed = urlparse(original_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # If that fails and the original URL is different from the base domain, 
    # also try the base domain with Playwright
    if not playwright_result.success and original_url != base_domain:
        logger.info(f"Playwright failed on exact URL, trying base domain: {base_domain}")
        base_playwright_result = await playwright_tos_finder(base_domain)
        if base_playwright_result.success:
            logger.info(f"Found ToS link with Playwright on base domain: {base_playwright_result.tos_url}")
            return base_playwright_result
    
    # Return the Playwright result if it found something, otherwise return the standard result
    if playwright_result.success:
        logger.info(f"Found ToS link with Playwright: {playwright_result.tos_url}")
        return playwright_result
    
    # If both methods failed, include a message about what was tried
    logger.info(f"No ToS link found for {original_url} with any method")
    
    # Set status code to 404 since no ToS was found
    response.status_code = 404
    
    # Check if we have any specific error information from Playwright
    if hasattr(playwright_result, 'method_used') and 'timeout' in playwright_result.method_used:
        # Special handling for timeouts
        return TosResponse(
            url=original_url,
            success=False,
            message=f"No Terms of Service link found. The site may be slow or blocking automated access.",
            method_used="both_failed_timeout"
        )
    elif hasattr(playwright_result, 'method_used') and 'navigation_failed' in playwright_result.method_used:
        # Special handling for navigation failures
        return TosResponse(
            url=original_url,
            success=False,
            message=f"No Terms of Service link found. The site may be unavailable or blocking automated access.",
            method_used="both_failed_navigation"
        )
    else:
        # Generic failure message
        return TosResponse(
            url=original_url,
            success=False,
            message=f"No Terms of Service link found. Tried both standard scraping and JavaScript-enabled browser rendering on both the exact URL and base domain.",
            method_used="both_failed"
        )


async def standard_tos_finder(variations_to_try, headers, session) -> TosResponse:
    """Standard approach using requests and BeautifulSoup to find Terms of Service links."""
    
    # For each variation, try to follow redirects to the final destination
    for url, variation_type in variations_to_try:
        try:
            logger.info(f"Trying URL variation: {url} ({variation_type})")
            
            # First do a HEAD request to check for redirects
            head_response = session.head(url, headers=headers, timeout=10, allow_redirects=True)
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
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find the Terms of Service link from the page
            logger.info(f"Searching for ToS link in {final_url}")
            tos_link = find_tos_link(final_url, soup)
            
            if tos_link:
                logger.info(f"Found ToS link: {tos_link} in {final_url} ({variation_type})")
                return TosResponse(
                    url=final_url,  # Return the actual URL after redirects
                    tos_url=tos_link,
                    success=True,
                    message=f"Terms of Service link found on final destination page: {final_url}" + 
                            (f" (Found at {variation_type})" if variation_type != "original exact url" else ""),
                    method_used="standard"
                )
            else:
                logger.info(f"No ToS link found in {final_url} ({variation_type})")
                    
        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({variation_type}): {str(e)}")
            # If this variation fails, try the next one
            continue
        except Exception as e:
            logger.error(f"Exception for {url} ({variation_type}): {str(e)}")
            # For non-request exceptions, try the next one
            continue
    
    # If we get here, we tried all variations but didn't find a Terms of Service link
    try:
        # Try one more time with the original URL for a better error message
        base_url = variations_to_try[0][0]  # Original URL
        head_response = session.head(base_url, headers=headers, timeout=10, allow_redirects=True)
        final_url = head_response.url
        
        response = session.get(final_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        return TosResponse(
            url=final_url,  # Return the final URL after redirects
            success=False,
            message=f"No Terms of Service link found with standard method on the final destination page: {final_url}.",
            method_used="standard_failed"
        )
    except requests.RequestException as e:
        error_msg = ""
        
        # Handle request errors with more specific messages
        if hasattr(e, 'response') and e.response is not None:
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
                error_msg = f"Error fetching {base_url}: HTTP status code {status_code}."
        else:
            error_msg = f"Error connecting to {base_url}: {str(e)}"
        
        # Return the error in the response
        return TosResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed"
        )
    except Exception as e:
        error_msg = f"Error processing URL {base_url} with standard method: {str(e)}"
        
        # Return the error in the response
        return TosResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed"
        )

def find_tos_link(url, soup):
    """Find and return the Terms of Service link from a webpage."""
    # Store the original URL for later validation
    original_url = url
    
    # Check if this is a GitHub repository
    parsed_url = urlparse(url)
    is_github_repo = parsed_url.netloc == 'github.com'
    domain = parsed_url.netloc.lower()
    path = parsed_url.path.strip('/')
    path_parts = path.split('/')
    
    # Special case handling for GitHub repositories
    if is_github_repo:
        if len(path_parts) >= 2:
            logger.info(f"GitHub repository detected: {path_parts[0]}/{path_parts[1]}")
            # For GitHub, the Terms of Service is a global link
            return "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service"
    
    # Get all links from the page
    links = soup.find_all('a', href=True)
    
    # Common terms used in Terms of Service links - ordered by specificity
    # We want the most specific ones to match first
    tos_keywords = [
        "terms of service",  # Most specific
        "terms and conditions", 
        "terms of use",
        "legal terms",
        "user agreement",
        "tos"
    ]
    
    # Lower priority keywords that should only match as exact phrases
    # or in specific contexts like footers
    lower_priority_keywords = [
        "terms",  # This is too generic for content matching
        "conditions",
        "legal"
    ]
    
    # Get the footer and legal areas which are more likely to contain ToS links
    footers = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower() or 'legal' in c.lower() or 'bottom' in c.lower()))
    legal_sections = soup.find_all(['div', 'section', 'nav'], class_=lambda c: c and ('legal' in c.lower() or 'footer' in c.lower()))
    
    # Combine potential areas with legal links
    legal_areas = footers + legal_sections
    
    # APPROACH 1: First try to find links in footer/legal areas with specific ToS keywords
    for area in legal_areas:
        area_links = area.find_all('a', href=True)
        
        for link in area_links:
            href = link.get('href')
            if not href or href.startswith('javascript:') or href == '#':
                continue
            
            # Absolute URLs vs relative URLs
            if not href.startswith('http'):
                absolute_href = urljoin(original_url, href)
            else:
                absolute_href = href
            
            # Skip links that point back to the same page
            if absolute_href == original_url:
                continue
            
            link_text = link.get_text().lower().strip()
            
            # Check for exact or near-exact matches in footer/legal areas
            for keyword in tos_keywords + lower_priority_keywords:
                # Exact match or with punctuation
                if (keyword == link_text or 
                    f"{keyword}." == link_text or
                    f"{keyword}:" == link_text):
                    logger.info(f"Found exact ToS keyword '{keyword}' in footer/legal area: {absolute_href}")
                    return absolute_href
            
            # Check URL patterns in footer/legal links
            href_lower = href.lower()
            if ('terms' in href_lower and ('service' in href_lower or 'use' in href_lower or 'condition' in href_lower)) or 'tos' in href_lower:
                if not is_likely_article_link(href_lower, absolute_href):
                    logger.info(f"Found ToS keyword pattern in footer/legal href: {absolute_href}")
                    return absolute_href
    
    # APPROACH 2: Check the entire page for specific ToS patterns in URLs
    for link in links:
        href = link.get('href')
        if not href or href.startswith('javascript:') or href == '#':
            continue
        
        # Absolute URLs vs relative URLs
        if not href.startswith('http'):
            absolute_href = urljoin(original_url, href)
        else:
            absolute_href = href
        
        # Skip links that point back to the same page or to likely article URLs
        if absolute_href == original_url or is_likely_article_link(href.lower(), absolute_href):
            continue
        
        href_lower = href.lower()
        
        # Strong URL patterns that highly indicate ToS
        if (('/terms-of-service' in href_lower) or 
            ('/terms-of-use' in href_lower) or 
            ('/terms-and-conditions' in href_lower) or
            ('/tos' in href_lower and len(href_lower.split('/tos')) == 2) or  # Exactly "/tos"
            ('terms_of_service' in href_lower) or
            ('terms_of_use' in href_lower) or
            ('/legal/terms' in href_lower)):
            logger.info(f"Found strong ToS pattern in URL: {absolute_href}")
            return absolute_href
    
    # APPROACH 3: Look for exact text matches anywhere in the page
    for link in links:
        href = link.get('href')
        if not href or href.startswith('javascript:') or href == '#':
            continue
        
        # Absolute URLs vs relative URLs
        if not href.startswith('http'):
            absolute_href = urljoin(original_url, href)
        else:
            absolute_href = href
        
        # Skip links that point back to the same page or to likely article URLs
        if absolute_href == original_url or is_likely_article_link(href.lower(), absolute_href):
            continue
        
        link_text = link.get_text().lower().strip()
        
        # Check for exact text matches with higher-priority keywords
        for keyword in tos_keywords:
            if keyword == link_text or f"{keyword}." == link_text:
                # Verify this doesn't appear to be an article link
                if not is_likely_article_link(href.lower(), absolute_href):
                    logger.info(f"Found exact match for ToS keyword '{keyword}' in link text: {absolute_href}")
                    return absolute_href
    
    # APPROACH 4: For sites where all else failed, check using broader heuristics
    # But be more restrictive to avoid false positives
    
    # Get the main navigation menus - these often contain ToS links at the bottom
    navs = soup.find_all(['nav', 'ul'], class_=lambda c: c and ('nav' in str(c).lower() or 'menu' in str(c).lower()))
    
    # Look in navigation elements
    for nav in navs:
        nav_links = nav.find_all('a', href=True)
        
        for link in nav_links:
            href = link.get('href')
            if not href or href.startswith('javascript:') or href == '#':
                continue
            
            # Absolute URLs vs relative URLs
            if not href.startswith('http'):
                absolute_href = urljoin(original_url, href)
            else:
                absolute_href = href
            
            # Skip links that point back to the same page
            if absolute_href == original_url:
                continue
            
            link_text = link.get_text().lower().strip()
            
            # Check for partial matches with higher-priority keywords in navigation only
            for keyword in tos_keywords:
                if keyword in link_text and not is_likely_article_link(href.lower(), absolute_href):
                    link_text_words = link_text.split()
                    # Ensure "terms" isn't just part of another word (like "determines")
                    if any(word == "terms" or word == "tos" for word in link_text_words):
                        logger.info(f"Found ToS keyword '{keyword}' in navigation: {absolute_href}")
                        return absolute_href
    
    # If we've exhausted all options, return None
    return None

def is_likely_article_link(href_lower: str, full_url: str) -> bool:
    """
    Determine if a URL is likely to be a news article rather than a ToS page.
    
    Args:
        href_lower: The lowercase href attribute
        full_url: The full URL for additional context
    
    Returns:
        bool: True if the URL appears to be an article, False otherwise
    """
    # News article patterns in URLs
    article_indicators = [
        "/article/", 
        "/news/",
        "/story/",
        "/blog/",
        "/post/",
        "/2023/",  # Year patterns
        "/2024/",
        "/politics/",
        "/business/",
        "/technology/",
        "/science/",
        "/health/",
        ".html",
        "/watch/",
        "/video/"
    ]
    
    # Common news domains (partial list)
    news_domains = [
        "reuters.com",
        "nytimes.com",
        "washingtonpost.com",
        "cnn.com",
        "bbc.com",
        "forbes.com"
    ]
    
    # Check if URL contains article indicators
    for indicator in article_indicators:
        if indicator in href_lower:
            return True
    
    # Check if URL is from a known news domain
    parsed_url = urlparse(full_url)
    domain = parsed_url.netloc.lower()
    for news_domain in news_domains:
        if news_domain in domain:
            # For news sites, be extra careful
            # Only consider it a ToS link if it clearly has terms in the path
            if not any(term in parsed_url.path.lower() for term in ['/terms', '/tos', '/legal']):
                return True
    
    # Check for date patterns in URL paths
    date_pattern = re.compile(r'/\d{4}/\d{1,2}/\d{1,2}/')
    if date_pattern.search(href_lower):
        return True
    
    return False

async def playwright_tos_finder(url: str) -> TosResponse:
    """
    Find Terms of Service links using Playwright for JavaScript-rendered content.
    This is a fallback method for when the standard approach fails.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            # Set a reasonable timeout for navigation
            page.set_default_timeout(30000)  # 30 seconds
            
            try:
                # Navigate to the URL
                await page.goto(url, wait_until="networkidle")
                
                # Get the final URL after any redirects
                final_url = page.url
                
                # Get the content
                content = await page.content()
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # Find links using our existing function
                tos_link = find_tos_link(final_url, soup)
                
                # If standard approach didn't find links, try using Playwright's own selectors
                if not tos_link:
                    # Try to find links with text containing ToS terms
                    tos_keywords = ["terms", "terms of service", "terms of use", "tos", "terms and conditions", "legal terms"]
                    
                    for keyword in tos_keywords:
                        # Use case-insensitive search for links with text containing the keyword
                        links = await page.query_selector_all(f'a:text-matches("{keyword}", "i")')
                        
                        for link in links:
                            href = await link.get_attribute('href')
                            if href and not href.startswith('javascript:') and href != '#':
                                # Check if it's likely to be an article link
                                if not is_likely_article_link(href.lower(), urljoin(final_url, href)):
                                    tos_link = urljoin(final_url, href)
                                    break
                        
                        if tos_link:
                            break
                    
                    # If still not found, try clicking "I agree" or cookie consent buttons to reveal TOS links
                    if not tos_link:
                        # Try to find and click buttons that might reveal TOS content
                        consent_buttons = await page.query_selector_all('button:text-matches("(accept|agree|got it|cookie|consent)", "i")')
                        
                        for button in consent_buttons:
                            try:
                                await button.click()
                                await page.wait_for_timeout(1000)  # Wait for any changes to take effect
                                
                                # Check for new links after clicking
                                content_after_click = await page.content()
                                soup_after_click = BeautifulSoup(content_after_click, 'html.parser')
                                tos_link = find_tos_link(final_url, soup_after_click)
                                
                                if tos_link:
                                    break
                            except:
                                continue
                
                # Close the browser
                await browser.close()
                
                if tos_link:
                    return TosResponse(
                        url=final_url,
                        tos_url=tos_link,
                        success=True,
                        message=f"Terms of Service link found using JavaScript-enabled browser rendering on page: {final_url}",
                        method_used="playwright"
                    )
                else:
                    return TosResponse(
                        url=final_url,
                        success=False,
                        message=f"No Terms of Service link found even with JavaScript-enabled browser rendering on page: {final_url}",
                        method_used="playwright_failed"
                    )
            
            except Exception as e:
                await browser.close()
                if "Timeout" in str(e) or "timeout" in str(e).lower():
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Timeout while loading page with Playwright: {url}. The site may be slow or blocking automated access.",
                        method_used="playwright_failed_timeout"
                    )
                elif "Navigation failed" in str(e) or "ERR_CONNECTION" in str(e):
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Navigation failed for {url}. The site may be unavailable or blocking automated access.",
                        method_used="playwright_failed_navigation"
                    )
                else:
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Error using Playwright to process URL {url}: {str(e)}",
                        method_used="playwright_failed"
                    )
    
    except Exception as e:
        error_msg = f"Error using Playwright to process URL {url}: {str(e)}"
        logger.error(error_msg)
        
        return TosResponse(
            url=url,
            success=False,
            message=error_msg,
            method_used="playwright_failed"
        )

# Rest of the file stays the same