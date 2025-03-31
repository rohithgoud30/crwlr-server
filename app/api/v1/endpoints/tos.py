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
        Basic URL validation and normalization.
        """
        # Strip any trailing slashes to normalize
        v = v.rstrip('/')
        
        # Check if URL has protocol, if not add https://
        if not v.startswith(('http://', 'https://')):
            v = f"https://{v}"
                
        return v


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
    original_url = request.url  # The exact URL provided by the user
    
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
    
    # Continue with regular processing for non-App Store URLs
    variations_to_try = []
    
    # 1. First prioritize the exact URL provided by the user
    variations_to_try.append((original_url, "original exact url"))
    
    # 2. Then add base domain variations for fallback
    parsed = urlparse(original_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # Only add base domain if it's different from the original URL
    if base_domain != original_url:
        variations_to_try.append((base_domain, "base domain"))
        
        # Also add www/non-www variations of the base domain
        domain = parsed.netloc.lower()
        if domain.startswith('www.'):
            domain_without_www = domain[4:]
            new_url = base_domain.replace(domain, domain_without_www)
            variations_to_try.append((new_url, "base domain without www"))
        else:
            domain_with_www = f"www.{domain}"
            new_url = base_domain.replace(domain, domain_with_www)
            variations_to_try.append((new_url, "base domain with www"))
        
        # Try HTTP instead of HTTPS as last resort
        if base_domain.startswith('https://'):
            http_url = 'http://' + base_domain[8:]
            variations_to_try.append((http_url, "HTTP protocol base domain"))
    
    logger.info(f"URL variations to try: {variations_to_try}")
    
    # Try the standard method first (requests + BeautifulSoup)
    standard_result = await standard_tos_finder(variations_to_try, headers, session)
    if standard_result.success:
        logger.info(f"Found ToS link with standard method: {standard_result.tos_url}")
        return standard_result
    
    # If standard method fails, try with Playwright
    logger.info(f"Standard method failed for {original_url}, trying with Playwright")
    
    # First try the specific URL with Playwright
    playwright_result = await playwright_tos_finder(original_url)
    
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


async def handle_app_store_tos(url: str, app_id: str) -> TosResponse:
    """
    Specialized handler for App Store app URLs to extract the app's terms of service.
    First finds the privacy policy, then uses its base URL to look for terms of service.
    """
    logger.info(f"Handling App Store app terms of service extraction for app ID: {app_id}")
    
    # Browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    # Try to extract app name for better error reporting
    app_name = None
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for the app name in the title or h1
        title_elem = soup.find('title')
        if title_elem:
            app_name = title_elem.text.strip().split('-')[0].strip()
        
        if not app_name:
            h1_elem = soup.find('h1')
            if h1_elem:
                app_name = h1_elem.text.strip()
    except Exception as e:
        logger.error(f"Error extracting app name: {str(e)}")
    
    app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}'}"
    
    # Step 1: First try to find the privacy policy
    pp_url = None
    try:
        # Look directly for the app-privacy section
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        app_privacy_section = soup.find('section', class_='app-privacy')
        if app_privacy_section:
            # Search for privacy policy links in this section
            privacy_links = app_privacy_section.find_all('a', href=True)
            for link in privacy_links:
                href = link.get('href')
                link_text = link.get_text().lower()
                if href and ('privacy' in link_text or 'privacy' in href.lower()):
                    pp_url = href
                    logger.info(f"Found privacy policy link in app-privacy section: {pp_url}")
                    break
        
        # If not found in the privacy section, try general privacy policy links
        if not pp_url:
            privacy_links = soup.find_all('a', string=re.compile(r'privacy policy', re.IGNORECASE))
            if privacy_links:
                pp_url = privacy_links[0].get('href')
                logger.info(f"Found general privacy policy link: {pp_url}")
    except Exception as e:
        logger.error(f"Error searching for privacy policy: {str(e)}")
    
    # Step 2: If privacy policy was found, follow redirects to get final URL
    final_pp_url = None
    base_domain = None
    
    if pp_url:
        try:
            logger.info(f"Following privacy policy URL to extract base domain: {pp_url}")
            privacy_response = requests.get(pp_url, headers=headers, timeout=15, allow_redirects=True)
            final_pp_url = privacy_response.url
            
            # Extract base domain from privacy policy URL
            parsed_url = urlparse(final_pp_url)
            base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
            logger.info(f"Extracted base domain from privacy policy: {base_domain}")
        except Exception as e:
            logger.error(f"Error following privacy policy link: {str(e)}")
    
    # Step 3: Look for terms of service on the base domain using BeautifulSoup
    tos_url = None
    
    if base_domain:
        try:
            logger.info(f"Searching for terms of service on base domain: {base_domain}")
            base_response = requests.get(base_domain, headers=headers, timeout=15)
            base_soup = BeautifulSoup(base_response.text, 'html.parser')
            
            # Common terms for Terms of Service links
            tos_terms = [
                'terms of service', 'terms of use', 'terms and conditions', 
                'eula', 'end user license agreement', 'legal', 'terms', 
                'conditions of use', 'user agreement'
            ]
            
            # Look for links with these terms
            for term in tos_terms:
                tos_links = base_soup.find_all('a', string=re.compile(term, re.IGNORECASE))
                for link in tos_links:
                    href = link.get('href')
                    if href and 'apple.com/legal' not in href.lower():  # Exclude Apple's generic terms
                        # Make sure it's an absolute URL
                        if not href.startswith(('http://', 'https://')):
                            href = urljoin(base_domain, href)
                        
                        tos_url = href
                        logger.info(f"Found terms of service link on base domain: {tos_url}")
                        break
                
                if tos_url:
                    break
            
            # If found, follow redirects to get the final URL
            if tos_url:
                tos_response = requests.get(tos_url, headers=headers, timeout=15, allow_redirects=True)
                tos_url = tos_response.url
                logger.info(f"Final terms of service URL after redirects: {tos_url}")
                
                return TosResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message=f"Found terms of service for {app_info} on privacy policy domain",
                    method_used="app_store_privacy_domain"
                )
        except Exception as e:
            logger.error(f"Error searching for terms on base domain: {str(e)}")
    
    # Step 4: If BeautifulSoup method failed, try with Playwright
    if not tos_url:
        logger.info(f"Standard method failed, trying with Playwright for {url}")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
                
                page = await context.new_page()
                
                # First try the base domain if we found it from privacy policy
                if base_domain:
                    try:
                        await page.goto(base_domain, wait_until="networkidle", timeout=30000)
                        
                        # Look for terms of service links
                        for term in ['terms of service', 'terms of use', 'terms and conditions', 'eula']:
                            tos_links = await page.query_selector_all(f'a:text-matches("{term}", "i")')
                            for link in tos_links:
                                href = await link.get_attribute('href')
                                if href and 'apple.com/legal' not in href.lower():
                                    # Follow the link to get the final URL
                                    tos_page = await context.new_page()
                                    await tos_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                                    final_url = tos_page.url
                                    await tos_page.close()
                                    
                                    await browser.close()
                                    
                                    return TosResponse(
                                        url=url,
                                        tos_url=final_url,
                                        success=True,
                                        message=f"Found terms of service for {app_info} on privacy policy domain using Playwright",
                                        method_used="app_store_playwright_privacy_domain"
                                    )
                    except Exception as e:
                        logger.error(f"Error checking base domain with Playwright: {str(e)}")
                
                # If base domain check failed, try the original app page
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    
                    # Look for terms of service links on the app page
                    for term in ['terms of service', 'terms of use', 'terms and conditions', 'eula', 'legal']:
                        tos_links = await page.query_selector_all(f'a:text-matches("{term}", "i")')
                        for link in tos_links:
                            href = await link.get_attribute('href')
                            if href and 'apple.com/legal' not in href.lower():
                                # Follow the link to get the final URL
                                tos_page = await context.new_page()
                                await tos_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                                final_url = tos_page.url
                                await tos_page.close()
                                
                                await browser.close()
                                
                                return TosResponse(
                                    url=url,
                                    tos_url=final_url,
                                    success=True,
                                    message=f"Found terms of service for {app_info} on app page using Playwright",
                                    method_used="app_store_playwright_app_page"
                                )
                except Exception as e:
                    logger.error(f"Error checking app page with Playwright: {str(e)}")
                
                await browser.close()
        except Exception as e:
            logger.error(f"Error initializing Playwright: {str(e)}")
    
    # Return error if no terms of service found
    return TosResponse(
        url=url,
        success=False,
        message=f"Could not find terms of service for {app_info}. The app may not have a terms of service link on its App Store page.",
        method_used="app_store_failed"
    )


async def handle_play_store_tos(url: str, app_id: str) -> TosResponse:
    """
    Specialized handler for Google Play Store app URLs to extract the app's terms of service.
    First finds the privacy policy, then uses its base URL to look for terms of service.
    """
    logger.info(f"Handling Google Play Store terms of service extraction for app ID: {app_id}")
    
    # Browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    # Try to extract app name for better error reporting
    app_name = None
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for the app name in the page
        app_name_element = soup.select_one('h1[itemprop="name"]')
        if app_name_element:
            app_name = app_name_element.text.strip()
        
        if not app_name:
            title_elem = soup.find('title')
            if title_elem:
                app_name = title_elem.text.strip().split('-')[0].strip()
    except Exception as e:
        logger.error(f"Error extracting app name: {str(e)}")
    
    app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}'}"
    
    # Step 1: First try to find the privacy policy
    pp_url = None
    try:
        # Access the Data Safety page specifically
        data_safety_url = f"https://play.google.com/store/apps/datasafety?id={app_id}"
        logger.info(f"Checking data safety page: {data_safety_url}")
        
        response = requests.get(data_safety_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for privacy policy links on the data safety page
        privacy_links = soup.find_all('a', href=True)
        for link in privacy_links:
            href = link.get('href')
            link_text = link.get_text().lower()
            
            # Skip Google's general privacy policy
            if href and 'privacy' in link_text and not href.startswith('https://policies.google.com/privacy'):
                pp_url = href
                logger.info(f"Found privacy policy link on data safety page: {pp_url}")
                break
    except Exception as e:
        logger.error(f"Error searching for privacy policy on data safety page: {str(e)}")
    
    # Step 2: If privacy policy wasn't found on the data safety page, try the main app page
    if not pp_url:
        try:
            response = requests.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            privacy_links = soup.find_all('a', href=True)
            for link in privacy_links:
                href = link.get('href')
                link_text = link.get_text().lower()
                
                # Skip Google's general privacy policy
                if href and 'privacy' in link_text and not href.startswith('https://policies.google.com/privacy'):
                    pp_url = href
                    logger.info(f"Found privacy policy link on app page: {pp_url}")
                    break
        except Exception as e:
            logger.error(f"Error searching for privacy policy on app page: {str(e)}")
    
    # Step 3: If privacy policy was found, follow redirects to get final URL
    final_pp_url = None
    base_domain = None
    
    if pp_url:
        try:
            logger.info(f"Following privacy policy URL to extract base domain: {pp_url}")
            privacy_response = requests.get(pp_url, headers=headers, timeout=15, allow_redirects=True)
            final_pp_url = privacy_response.url
            
            # Extract base domain from privacy policy URL
            parsed_url = urlparse(final_pp_url)
            base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
            logger.info(f"Extracted base domain from privacy policy: {base_domain}")
        except Exception as e:
            logger.error(f"Error following privacy policy link: {str(e)}")
    
    # Step 4: Look for terms of service on the base domain using BeautifulSoup
    tos_url = None
    
    if base_domain:
        try:
            logger.info(f"Searching for terms of service on base domain: {base_domain}")
            base_response = requests.get(base_domain, headers=headers, timeout=15)
            base_soup = BeautifulSoup(base_response.text, 'html.parser')
            
            # Common terms for Terms of Service links
            tos_terms = [
                'terms of service', 'terms of use', 'terms and conditions', 
                'eula', 'end user license agreement', 'legal', 'terms', 
                'conditions of use', 'user agreement'
            ]
            
            # Look for links with these terms
            for term in tos_terms:
                tos_links = base_soup.find_all('a', string=re.compile(term, re.IGNORECASE))
                for link in tos_links:
                    href = link.get('href')
                    # Skip Google's general terms
                    if href and not href.startswith('https://policies.google.com/terms'):
                        # Make sure it's an absolute URL
                        if not href.startswith(('http://', 'https://')):
                            href = urljoin(base_domain, href)
                        
                        tos_url = href
                        logger.info(f"Found terms of service link on base domain: {tos_url}")
                        break
                
                if tos_url:
                    break
            
            # If found, follow redirects to get the final URL
            if tos_url:
                tos_response = requests.get(tos_url, headers=headers, timeout=15, allow_redirects=True)
                tos_url = tos_response.url
                logger.info(f"Final terms of service URL after redirects: {tos_url}")
                
                return TosResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message=f"Found terms of service for {app_info} on privacy policy domain",
                    method_used="play_store_privacy_domain"
                )
        except Exception as e:
            logger.error(f"Error searching for terms on base domain: {str(e)}")
    
    # Step 5: If BeautifulSoup method failed, try with Playwright
    if not tos_url:
        logger.info(f"Standard method failed, trying with Playwright for {url}")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
                )
                
                page = await context.new_page()
                
                # First try the base domain if we found it from privacy policy
                if base_domain:
                    try:
                        await page.goto(base_domain, wait_until="domcontentloaded", timeout=20000)
                        
                        # Look for terms of service links
                        for term in ['terms of service', 'terms of use', 'terms and conditions', 'eula']:
                            tos_links = await page.query_selector_all(f'a:text-matches("{term}", "i")')
                            for link in tos_links:
                                href = await link.get_attribute('href')
                                if href and not href.startswith('https://policies.google.com/terms'):
                                    # Follow the link to get the final URL
                                    tos_page = await context.new_page()
                                    await tos_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                                    final_url = tos_page.url
                                    await tos_page.close()
                                    
                                    await browser.close()
                                    
                                    return TosResponse(
                                        url=url,
                                        tos_url=final_url,
                                        success=True,
                                        message=f"Found terms of service for {app_info} on privacy policy domain using Playwright",
                                        method_used="play_store_playwright_privacy_domain"
                                    )
                    except Exception as e:
                        logger.error(f"Error checking base domain with Playwright: {str(e)}")
                
                # If base domain check failed, try the original app page and data safety page
                for check_url in [url, f"https://play.google.com/store/apps/datasafety?id={app_id}"]:
                    try:
                        await page.goto(check_url, wait_until="domcontentloaded", timeout=20000)
                        
                        # Look for terms of service links
                        for term in ['terms of service', 'terms of use', 'terms and conditions', 'eula', 'legal']:
                            tos_links = await page.query_selector_all(f'a:text-matches("{term}", "i")')
                            for link in tos_links:
                                href = await link.get_attribute('href')
                                if href and not href.startswith('https://policies.google.com/terms'):
                                    # Follow the link to get the final URL
                                    tos_page = await context.new_page()
                                    await tos_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                                    final_url = tos_page.url
                                    await tos_page.close()
                                    
                                    await browser.close()
                                    
                                    return TosResponse(
                                        url=url,
                                        tos_url=final_url,
                                        success=True,
                                        message=f"Found terms of service for {app_info} using Playwright",
                                        method_used="play_store_playwright"
                                    )
                    except Exception as e:
                        logger.error(f"Error checking URL with Playwright: {str(e)}")
                
                await browser.close()
        except Exception as e:
            logger.error(f"Error initializing Playwright: {str(e)}")
    
    # Return error if no terms of service found
    return TosResponse(
        url=url,
        success=False,
        message=f"Could not find terms of service for {app_info}. The app may not have a terms of service link on its Google Play Store page.",
        method_used="play_store_failed"
    )


async def standard_tos_finder(variations_to_try, headers, session) -> TosResponse:
    """
    Standard approach to find ToS links using requests and BeautifulSoup.
    """
    for url, url_type in variations_to_try:
        try:
            logger.info(f"Trying URL variation: {url} ({url_type})")
            response = session.get(url, headers=headers, timeout=15)
            
            # Handle redirects - if the URL changed, log it
            if response.url != url:
                logger.info(f"Followed redirect: {url} -> {response.url}")
                url = response.url  # Update the URL to the final one after redirects
            
            # Log the current URL we're processing
            logger.info(f"Fetching content from {url}")
            
            # Check if response is valid
            if response.status_code != 200:
                logger.warning(f"Got status code {response.status_code} for {url} ({url_type})")
                continue
            
            # Parse HTML content
            logger.info(f"Searching for ToS link in {url}")
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find the ToS link
            tos_url = find_tos_link(url, soup)
            
            # If we found a ToS URL, return success
            if tos_url:
                logger.info(f"Found ToS link: {tos_url} in {url} ({url_type})")
                return TosResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message=f"Found Terms of Service link for {url}",
                    method_used="standard"
                )
                
        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({url_type}): {str(e)}")
        except Exception as e:
            logger.error(f"Error processing {url} ({url_type}): {str(e)}")
    
    # If we get here, we didn't find a ToS with the standard approach
    return TosResponse(
        url=variations_to_try[0][0],  # Use the original URL in the response
        success=False,
        message="Could not find Terms of Service link using standard scraping",
        method_used="standard_failed"
    )


async def playwright_tos_finder(url: str) -> TosResponse:
    """
    Use Playwright for JavaScript-heavy sites to find ToS links.
    """
    browser = None
    try:
        logger.info(f"Starting Playwright for {url}")
        async with async_playwright() as p:
            # Use a headless browser with more reasonable timeout
            browser = await p.chromium.launch(headless=True)
            browser_context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
            )
            
            page = await browser_context.new_page()
            
            # Set default timeout to 20 seconds (reduced from 30)
            page.set_default_timeout(20000)
            
            # Handle alerts automatically
            page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
            
            try:
                # Changed from networkidle to domcontentloaded for faster response
                response = await page.goto(url, wait_until="domcontentloaded")
                
                if not response:
                    logger.error(f"Failed to load {url} with Playwright")
                    await browser.close()
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Failed to load page with JavaScript-enabled browser",
                        method_used="playwright_failed"
                    )
                
                # Wait a bit for any post-load JavaScript to execute
                await asyncio.sleep(2)
                
                # First check URL patterns for ToS links
                try:
                    links = await page.query_selector_all('a[href]')
                    for link in links:
                        href = await link.get_attribute('href')
                        
                        if not href or href.startswith(('javascript:', 'mailto:', '#')):
                            continue
                            
                        href_lower = href.lower()
                        
                        # Check for common terms paths in URLs
                        if (href_lower.endswith('/terms-of-service') or 
                            href_lower.endswith('/terms') or 
                            href_lower.endswith('/tos') or
                            '/terms-of-service' in href_lower or
                            '/termsofservice' in href_lower or
                            '/tos' in href_lower):
                            
                            # Skip common platform terms
                            if any(skip in href_lower for skip in [
                                'google.com/terms', 'apple.com/legal', 'microsoft.com/terms', 
                                'twitter.com/tos', 'facebook.com/terms'
                            ]) and skip not in url.lower():
                                continue
                                
                            # Make sure it's an absolute URL
                            if not href.startswith(('http://', 'https://')):
                                href = urljoin(url, href)
                                
                            logger.info(f"Found ToS link by URL pattern with Playwright: {href}")
                            await browser.close()
                            return TosResponse(
                                url=url,
                                tos_url=href,
                                success=True,
                                message=f"Found Terms of Service by URL pattern using JavaScript-enabled browser",
                                method_used="playwright"
                            )
                except Exception as e:
                    logger.error(f"Error checking URL patterns: {str(e)}")
                
                # Now try to find ToS links in various ways
                
                # 1. Look for links with "terms" or similar in the text
                tos_terms = [
                    'terms of service', 'terms of use', 'terms and conditions', 
                    'terms', 'conditions', 'user agreement', 'legal', 'eula'
                ]
                
                for term in tos_terms:
                    try:
                        # Use a more specific selector to avoid timeouts
                        links = await page.query_selector_all(f'a:visible')
                        for link in links:
                            try:
                                text = await link.inner_text()
                                text_lower = text.lower().strip()
                                
                                if term in text_lower:
                                    href = await link.get_attribute('href')
                                    if href and not href.startswith(('javascript:', 'mailto:', '#')):
                                        # Make sure it's an absolute URL
                                        if not href.startswith(('http://', 'https://')):
                                            href = urljoin(url, href)
                                        
                                        # Skip links to generic legal pages of common platforms
                                        if any(skip in href.lower() for skip in [
                                            'google.com/terms', 'apple.com/legal', 'microsoft.com/terms',
                                            'twitter.com/tos', 'facebook.com/terms'
                                        ]):
                                            continue
                                        
                                        logger.info(f"Found ToS link with Playwright: {href}")
                                        await browser.close()
                                        return TosResponse(
                                            url=url,
                                            tos_url=href,
                                            success=True,
                                            message=f"Found Terms of Service using JavaScript-enabled browser",
                                            method_used="playwright"
                                        )
                            except:
                                # Skip any individual link that causes problems
                                continue
                    except:
                        # Skip any term that causes problems
                        continue
                
                # 2. Look specifically in footer elements
                try:
                    footer_elements = await page.query_selector_all('footer, .footer, [id*="footer"], [class*="footer"]')
                    for footer in footer_elements:
                        links = await footer.query_selector_all('a')
                        for link in links:
                            try:
                                text = await link.inner_text()
                                text_lower = text.lower().strip()
                                
                                if any(term in text_lower for term in tos_terms):
                                    href = await link.get_attribute('href')
                                    if href and not href.startswith(('javascript:', 'mailto:', '#')):
                                        # Make sure it's an absolute URL
                                        if not href.startswith(('http://', 'https://')):
                                            href = urljoin(url, href)
                                        
                                        logger.info(f"Found ToS link in footer with Playwright: {href}")
                                        await browser.close()
                                        return TosResponse(
                                            url=url,
                                            tos_url=href,
                                            success=True,
                                            message=f"Found Terms of Service in footer using JavaScript-enabled browser",
                                            method_used="playwright_footer"
                                        )
                            except:
                                continue
                except:
                    # Ignore errors with footer processing
                    pass
                
                # If we get here, no ToS link was found
                await browser.close()
                return TosResponse(
                    url=url,
                    success=False,
                    message="No Terms of Service link found using JavaScript-enabled browser rendering",
                    method_used="playwright_failed"
                )
                
            except Exception as e:
                logger.error(f"Error during Playwright execution for {url}: {str(e)}")
                # Try to gracefully close if browser is available
                if browser:
                    await browser.close()
                    
                # Check if it's a timeout error
                if "Timeout" in str(e):
                    return TosResponse(
                        url=url,
                        success=False,
                        message="Page loading timed out. Site may be slow or blocking automated access.",
                        method_used="playwright_timeout"
                    )
                else:
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Error using JavaScript-enabled browser: {str(e)}",
                        method_used="playwright_error"
                    )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error initializing Playwright for {url}: {error_msg}")
        
        # Provide more specific error messages for common failure cases
        if "Timeout" in error_msg:
            return TosResponse(
                url=url,
                success=False,
                message="Page loading timed out. The site may be slow or blocking automated access.",
                method_used="playwright_timeout"
            )
        elif "Navigation failed" in error_msg:
            return TosResponse(
                url=url,
                success=False,
                message="Navigation failed. The site may be unavailable or blocking automated access.",
                method_used="playwright_navigation_failed"
            )
        else:
            return TosResponse(
                url=url,
                success=False,
                message=f"Error initializing browser environment: {error_msg}",
                method_used="playwright_init_error"
            )


def find_tos_link(url, soup):
    """
    Find and return the Terms of Service link from a webpage.
    """
    # Keywords that might indicate a ToS link
    tos_keywords = [
        'terms of service', 'terms of use', 'terms and conditions', 
        'user agreement', 'terms', 'legal terms', 'eula',
        'end user license agreement', 'conditions', 'legal'
    ]
    
    # Extract the domain from the URL for later comparison
    parsed_url = urlparse(url)
    current_domain = parsed_url.netloc.lower()
    if current_domain.startswith('www.'):
        current_domain = current_domain[4:]
    
    # Get the root domain (like example.com from subdomain.example.com)
    domain_parts = current_domain.split('.')
    if len(domain_parts) > 2:
        root_domain = '.'.join(domain_parts[-2:])
    else:
        root_domain = current_domain
    
    logger.info(f"Current domain: {current_domain}, Root domain: {root_domain}")
    
    # Normalize the base URL for converting relative links
    base_url = url
    
    # Store all candidate links with a score
    candidate_links = []
    
    # Priority 0: Check for common ToS paths first (often direct links like /terms-of-service)
    links = soup.find_all('a')
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        # Direct check for common terms paths in URLs
        href_lower = href.lower()
        
        # Convert relative URL to absolute for domain checking
        full_href = href
        if not full_href.startswith(('http://', 'https://')):
            full_href = urljoin(base_url, href)
            
        href_parsed = urlparse(full_href)
        href_domain = href_parsed.netloc.lower()
        if href_domain.startswith('www.'):
            href_domain = href_domain[4:]
            
        # Check if URL path indicates a ToS link
        path_indicates_tos = (
            href_lower.endswith('/terms-of-service') or 
            href_lower.endswith('/terms') or 
            href_lower.endswith('/tos') or
            '/terms-of-service' in href_lower or
            '/termsofservice' in href_lower or
            '/tos' in href_lower
        )
        
        if path_indicates_tos:
            logger.info(f"Found terms link by path pattern: {href}")
            
            # Check if the domains match or are related
            domain_match = check_domain_relationship(current_domain, root_domain, href_domain)
            
            if domain_match:
                # Add to candidates list with a high score because path indicates ToS
                candidate_links.append((full_href, 9, "url_path_match"))
            else:
                # If domains don't match, add with lower score
                candidate_links.append((full_href, 3, "url_path_external"))
    
    # Priority 1: Look for links with exact ToS terms in link text
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        # Get the link text and normalize it
        link_text = link.get_text().lower().strip()
        
        # Convert relative URL to absolute for domain checking
        full_href = href
        if not full_href.startswith(('http://', 'https://')):
            full_href = urljoin(base_url, href)
            
        href_parsed = urlparse(full_href)
        href_domain = href_parsed.netloc.lower()
        if href_domain.startswith('www.'):
            href_domain = href_domain[4:]
        
        # Check for exact matches of priority terms
        if link_text in ['terms of service', 'terms of use', 'terms and conditions']:
            domain_match = check_domain_relationship(current_domain, root_domain, href_domain)
            
            if domain_match:
                # Add to candidates with high score for exact text match on same domain
                candidate_links.append((full_href, 10, "exact_text_match"))
            else:
                # External domain with exact match still deserves consideration
                candidate_links.append((full_href, 5, "exact_text_external"))
            
    # Priority 1.5: Look for ToS links near privacy policy links
    privacy_keywords = ['privacy', 'privacy policy', 'data policy', 'data protection']
    privacy_link_parents = set()
    
    # First find all privacy policy links
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        link_text = link.get_text().lower().strip()
        href_lower = href.lower()
        
        # Check if this is a privacy link
        is_privacy_link = False
        if any(keyword in link_text for keyword in privacy_keywords):
            is_privacy_link = True
        elif any(term in href_lower for term in ['/privacy', 'privacy-policy', 'privacypolicy']):
            is_privacy_link = True
            
        if is_privacy_link:
            # Store the parent element for this privacy link
            parent = link.parent
            if parent:
                privacy_link_parents.add(parent)
                
                # Also add grandparent to catch cases where both links are in separate divs
                grandparent = parent.parent
                if grandparent:
                    privacy_link_parents.add(grandparent)
    
    # Now look for ToS links in the same container as privacy links
    for parent in privacy_link_parents:
        tos_links = parent.find_all('a')
        for link in tos_links:
            href = link.get('href')
            if not href:
                continue
                
            # Skip empty links, anchors, javascript, and mailto
            if href == '#' or href.startswith(('javascript:', 'mailto:')):
                continue
                
            link_text = link.get_text().lower().strip()
            href_lower = href.lower()
            
            # Convert relative URL to absolute for domain checking
            full_href = href
            if not full_href.startswith(('http://', 'https://')):
                full_href = urljoin(base_url, href)
                
            href_parsed = urlparse(full_href)
            href_domain = href_parsed.netloc.lower()
            if href_domain.startswith('www.'):
                href_domain = href_domain[4:]
            
            # Check if this looks like a ToS link
            is_tos_link = False
            if any(keyword in link_text for keyword in tos_keywords):
                is_tos_link = True
            elif any(term in href_lower for term in ['/terms', '/tos', 'terms-of-service', 'termsofservice']):
                is_tos_link = True
                
            if is_tos_link:
                domain_match = check_domain_relationship(current_domain, root_domain, href_domain)
                
                if domain_match:
                    # Very high score - near privacy and same domain
                    candidate_links.append((full_href, 12, "privacy_container_match"))
                    logger.info(f"Found ToS link near privacy link (same domain): {full_href}")
                else:
                    # Still high score - context is valuable even if external
                    candidate_links.append((full_href, 7, "privacy_container_external"))
                    logger.info(f"Found ToS link near privacy link (external domain): {full_href}")
    
    # Priority 2: Look for links containing 'terms' as part of text
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        link_text = link.get_text().lower().strip()
        
        # Convert relative URL to absolute for domain checking
        full_href = href
        if not full_href.startswith(('http://', 'https://')):
            full_href = urljoin(base_url, href)
            
        href_parsed = urlparse(full_href)
        href_domain = href_parsed.netloc.lower()
        if href_domain.startswith('www.'):
            href_domain = href_domain[4:]
        
        # Check for terms mentioned in text
        for keyword in tos_keywords[:5]:  # Use higher priority keywords first
            if keyword in link_text:
                domain_match = check_domain_relationship(current_domain, root_domain, href_domain)
                
                if domain_match:
                    candidate_links.append((full_href, 8, "partial_text_match"))
                else:
                    candidate_links.append((full_href, 4, "partial_text_external"))
                
                # Only add once per link, so break after first match
                break
    
    # Priority 3: Check URLs for terms indications
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        # Convert relative URL to absolute for domain checking
        full_href = href
        if not full_href.startswith(('http://', 'https://')):
            full_href = urljoin(base_url, href)
            
        href_parsed = urlparse(full_href)
        href_domain = href_parsed.netloc.lower()
        if href_domain.startswith('www.'):
            href_domain = href_domain[4:]
        
        # Check if the URL contains terms indicators
        href_lower = href.lower()
        if any(term in href_lower for term in ['terms', 'tos', 'termsofservice', 'terms-of-service']):
            domain_match = check_domain_relationship(current_domain, root_domain, href_domain)
            
            if domain_match:
                candidate_links.append((full_href, 7, "url_contains_match"))
            else:
                candidate_links.append((full_href, 3, "url_contains_external"))
    
    # Priority 4: Look for links in the page footer
    footer_elem = soup.find('footer')
    if not footer_elem:
        # If no <footer> tag, look for elements with footer in class or id
        footer_elem = soup.find(lambda tag: tag.name and tag.get('class') and 
                              any('footer' in cls.lower() for cls in tag.get('class')))
        if not footer_elem:
            footer_elem = soup.find(lambda tag: tag.name and tag.get('id') and 'footer' in tag.get('id').lower())
    
    if footer_elem:
        footer_links = footer_elem.find_all('a')
        for link in footer_links:
            href = link.get('href')
            if not href:
                continue
                
            # Skip empty links, anchors, javascript, and mailto
            if href == '#' or href.startswith(('javascript:', 'mailto:')):
                continue
                
            link_text = link.get_text().lower().strip()
            
            # Convert relative URL to absolute for domain checking
            full_href = href
            if not full_href.startswith(('http://', 'https://')):
                full_href = urljoin(base_url, href)
                
            href_parsed = urlparse(full_href)
            href_domain = href_parsed.netloc.lower()
            if href_domain.startswith('www.'):
                href_domain = href_domain[4:]
            
            # Check for ToS-related text
            if any(keyword in link_text for keyword in tos_keywords):
                domain_match = check_domain_relationship(current_domain, root_domain, href_domain)
                
                if domain_match:
                    candidate_links.append((full_href, 9, "footer_text_match"))
                else:
                    candidate_links.append((full_href, 5, "footer_text_external"))
    
    # If we have candidates, pick the best one
    if candidate_links:
        # Sort by score (highest first)
        sorted_candidates = sorted(candidate_links, key=lambda x: x[1], reverse=True)
        
        # Log the best candidates
        for i, (url, score, method) in enumerate(sorted_candidates[:3]):
            logger.info(f"ToS candidate {i+1}: {url} (score: {score}, method: {method})")
        
        # Return the highest scoring candidate
        return sorted_candidates[0][0]
    
    # If we get here, no ToS link was found
    return None


def check_domain_relationship(current_domain, root_domain, href_domain):
    """
    Check if two domains are considered related.
    Returns True if domains match, are subdomains of each other, 
    or are otherwise related.
    """
    if not href_domain:
        return True  # Relative URLs are always considered valid
    
    # Exact match
    if current_domain == href_domain:
        return True
    
    # Root domain match (handles subdomains)
    href_domain_parts = href_domain.split('.')
    if len(href_domain_parts) > 2:
        href_root = '.'.join(href_domain_parts[-2:])
        if href_root == root_domain:
            return True
    
    # Check if one is subdomain of the other
    if href_domain.endswith('.' + current_domain) or current_domain.endswith('.' + href_domain):
        return True
    
    # Check if they share a corporate domain structure
    # Example: xyz.example.com vs legal.example.com
    if '.' in current_domain and '.' in href_domain:
        current_parts = current_domain.split('.')
        href_parts = href_domain.split('.')
        
        # If both have at least 3 parts (like something.example.com)
        if len(current_parts) >= 3 and len(href_parts) >= 3:
            current_corporate = '.'.join(current_parts[-2:])
            href_corporate = '.'.join(href_parts[-2:])
            if current_corporate == href_corporate:
                return True
    
    # Special cases for known corporate relationships are now handled automatically
    # For instance:
    # - Legal/policy subdomains (legal.company.com, policies.company.com)
    # - Documentation domains (docs.company.com)
    if any(subdomain in href_domain for subdomain in ['legal.', 'terms.', 'tos.', 'policies.', 'docs.']):
        # Extract the main part after the subdomain
        href_main_domain = href_domain.split('.', 1)[1] if '.' in href_domain else href_domain
        
        # Check if this main part matches or contains the current domain
        if href_main_domain == current_domain or href_main_domain in current_domain or current_domain in href_main_domain:
            return True
    
    return False 