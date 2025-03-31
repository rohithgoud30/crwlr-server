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
    
    # Common terms used in Terms of Service links
    tos_keywords = [
        "terms", "terms of service", "terms of use", "tos", "terms and conditions", 
        "conditions", "legal", "legal terms"
    ]
    
    # First, try to find exact matches in link text or href
    for link in links:
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
        
        # Check text of the link
        link_text = link.get_text().lower().strip()
        
        # Check for exact matches in link text
        for keyword in tos_keywords:
            if keyword == link_text or f"{keyword}." == link_text:
                logger.info(f"Found exact match for ToS keyword '{keyword}' in link text: {absolute_href}")
                return absolute_href
        
        # Check for keywords in href
        href_lower = href.lower()
        for keyword in tos_keywords:
            # Replace spaces with dashes or underscores for URL format
            keyword_url = keyword.replace(' ', '-')
            keyword_url2 = keyword.replace(' ', '_')
            
            if f"/{keyword_url}" in href_lower or f"/{keyword_url2}" in href_lower or keyword_url in href_lower or keyword_url2 in href_lower:
                logger.info(f"Found ToS keyword '{keyword}' in href: {absolute_href}")
                return absolute_href
    
    # If exact matches didn't work, look for terms contained within link text
    for link in links:
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
        
        # Check for partial matches in link text
        for keyword in tos_keywords:
            if keyword in link_text:
                logger.info(f"Found partial match for ToS keyword '{keyword}' in link text: {absolute_href}")
                return absolute_href
    
    # Look in page footers specifically (common place for ToS links)
    footers = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower() or 'legal' in c.lower() or 'bottom' in c.lower()))
    
    for footer in footers:
        footer_links = footer.find_all('a', href=True)
        
        for link in footer_links:
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
            
            # Check for ToS keywords in footer links
            for keyword in tos_keywords:
                if keyword in link_text or keyword in href.lower():
                    logger.info(f"Found ToS keyword '{keyword}' in footer link: {absolute_href}")
                    return absolute_href
    
    # If we've exhausted all options, return None
    return None

# Rest of the file stays the same