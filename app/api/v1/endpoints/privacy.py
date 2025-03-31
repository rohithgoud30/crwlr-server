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


class PrivacyRequest(BaseModel):
    url: str  # Using str to allow any input

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


class PrivacyResponse(BaseModel):
    url: str
    pp_url: Optional[str] = None
    success: bool
    message: str
    method_used: str = "standard"  # Indicates which method was used to find the privacy policy


@router.post("/privacy", response_model=PrivacyResponse, responses={
    200: {"description": "Privacy policy found successfully"},
    404: {"description": "Privacy policy not found", "model": PrivacyResponse}
})
async def find_privacy(request: PrivacyRequest, response: Response) -> PrivacyResponse:
    """
    Takes a base URL and returns the Privacy Policy page URL.
    This endpoint accepts partial URLs like 'google.com' and will
    automatically add the 'https://' protocol prefix if needed.
    
    Features a fallback to Playwright for JavaScript-heavy sites.
    """
    original_url = request.url  # The exact URL provided by the user
    
    logger.info(f"Processing privacy request for URL: {original_url}")
    
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
        result = await handle_app_store_privacy(original_url, app_id)
        # Set status code to 404 if privacy policy not found
        if not result.success:
            response.status_code = 404
        return result
    
    # If this is a Google Play Store app URL, handle it specially
    if is_play_store_app and play_app_id:
        result = await handle_play_store_privacy(original_url, play_app_id)
        # Set status code to 404 if privacy policy not found
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
    standard_result = await standard_privacy_finder(variations_to_try, headers, session)
    if standard_result.success:
        logger.info(f"Found privacy link with standard method: {standard_result.pp_url}")
        return standard_result
    
    # If standard method fails, try with Playwright
    logger.info(f"Standard method failed for {original_url}, trying with Playwright")
    
    # First try the specific URL with Playwright
    playwright_result = await playwright_privacy_finder(original_url)
    
    # If that fails and the original URL is different from the base domain, 
    # also try the base domain with Playwright
    if not playwright_result.success and original_url != base_domain:
        logger.info(f"Playwright failed on exact URL, trying base domain: {base_domain}")
        base_playwright_result = await playwright_privacy_finder(base_domain)
        if base_playwright_result.success:
            logger.info(f"Found privacy link with Playwright on base domain: {base_playwright_result.pp_url}")
            return base_playwright_result
    
    # Return the Playwright result if it found something, otherwise return the standard result
    if playwright_result.success:
        logger.info(f"Found privacy link with Playwright: {playwright_result.pp_url}")
        return playwright_result
    
    # If both methods failed, include a message about what was tried
    logger.info(f"No privacy policy link found for {original_url} with any method")
    
    # Set status code to 404 since no privacy policy was found
    response.status_code = 404
    
    return PrivacyResponse(
        url=original_url,
        success=False,
        message=f"No Privacy Policy link found. Tried both standard scraping and JavaScript-enabled browser rendering on both the exact URL and base domain.",
        method_used="both_failed"
    )


async def handle_app_store_privacy(url: str, app_id: str) -> PrivacyResponse:
    """
    Specialized handler for App Store app URLs to extract the app's privacy policy.
    """
    logger.info(f"Handling App Store app privacy policy extraction for app ID: {app_id}")
    
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
    
    # Method 1: Look for privacy policy in the app-privacy section
    pp_url = None
    try:
        response = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look directly for the app-privacy section
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
        
        # If found, follow redirects to get the final URL
        if pp_url:
            try:
                privacy_response = requests.get(pp_url, headers=headers, timeout=15, allow_redirects=True)
                pp_url = privacy_response.url
                logger.info(f"Final privacy policy URL after redirects: {pp_url}")
                
                return PrivacyResponse(
                    url=url,
                    pp_url=pp_url,
                    success=True,
                    message=f"Found privacy policy for {app_info} in App Store privacy section",
                    method_used="app_store_privacy_section"
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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Look for app-privacy section with Playwright
                app_privacy_section = await page.query_selector('section.app-privacy')
                if app_privacy_section:
                    privacy_links = await app_privacy_section.query_selector_all('a[href]')
                    
                    for link in privacy_links:
                        href = await link.get_attribute('href')
                        text = await link.text_content()
                        text_lower = text.lower()
                        
                        if href and ('privacy' in text_lower or 'privacy' in href.lower()):
                            pp_url = href
                            logger.info(f"Found privacy policy link with Playwright in app-privacy section: {pp_url}")
                            
                            # Follow the link to get the final URL
                            privacy_page = await context.new_page()
                            await privacy_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                            final_url = privacy_page.url
                            await privacy_page.close()
                            
                            await browser.close()
                            
                            return PrivacyResponse(
                                url=url,
                                pp_url=final_url,
                                success=True,
                                message=f"Found privacy policy for {app_info} in App Store privacy section using Playwright",
                                method_used="app_store_playwright_section"
                            )
                
                # If not found in privacy section, try looking for any privacy policy links on the page
                privacy_links = await page.query_selector_all('a:text-matches("privacy policy", "i")')
                for link in privacy_links:
                    href = await link.get_attribute('href')
                    if href:
                        logger.info(f"Found general privacy policy link with Playwright: {href}")
                        
                        # Follow the link to get the final URL
                        privacy_page = await context.new_page()
                        await privacy_page.goto(href, wait_until="domcontentloaded", timeout=20000)
                        final_url = privacy_page.url
                        await privacy_page.close()
                        
                        await browser.close()
                        
                        return PrivacyResponse(
                            url=url,
                            pp_url=final_url,
                            success=True,
                            message=f"Found privacy policy for {app_info} using Playwright",
                            method_used="app_store_playwright_general"
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
        method_used="app_store_failed"
    )


async def handle_play_store_privacy(url: str, app_id: str) -> PrivacyResponse:
    """
    Specialized handler for Google Play Store app URLs to extract the app's privacy policy.
    """
    logger.info(f"Handling Google Play Store app privacy policy extraction for app ID: {app_id}")
    
    # Enhanced browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    # Try to extract app name for better error reporting
    app_name = None
    try:
        response = requests.get(url, headers=headers, timeout=10)
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
    
    # Direct method: Visit the data safety page URL directly
    data_safety_url = f"https://play.google.com/store/apps/datasafety?id={app_id}"
    logger.info(f"Directly accessing data safety page: {data_safety_url}")
    
    # Try with requests first to see if it's accessible without JavaScript
    pp_url = None
    try:
        response = requests.get(data_safety_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for privacy policy links, but skip Google's general privacy policy
        privacy_links = soup.find_all('a', text=re.compile('privacy policy', re.IGNORECASE))
        app_specific_privacy_links = []
        
        for link in privacy_links:
            href = link.get('href')
            # Skip Google's general privacy policy
            if href and not href.startswith('https://policies.google.com/privacy'):
                app_specific_privacy_links.append(link)
                logger.info(f"Found app-specific privacy policy link: {href}")
        
        # If we found app-specific links, use the first one
        if app_specific_privacy_links:
            privacy_href = app_specific_privacy_links[0].get('href')
            logger.info(f"Found app-specific privacy policy link with requests: {privacy_href}")
            
            # Follow redirects to get the final URL
            privacy_response = requests.get(privacy_href, headers=headers, timeout=15, allow_redirects=True)
            pp_url = privacy_response.url
            logger.info(f"Final privacy policy URL after redirects: {pp_url}")
            
            return PrivacyResponse(
                url=url,
                pp_url=pp_url,
                success=True,
                message=f"Found privacy policy for {app_info} on data safety page",
                method_used="play_store_data_safety_direct"
            )
    except Exception as e:
        logger.warning(f"Simple request method failed, trying with Playwright: {str(e)}")
    
    # If simple request method didn't work, we need Playwright
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            try:
                # Go directly to the data safety page
                await page.goto(data_safety_url, wait_until="networkidle", timeout=30000)
                
                # Look for privacy policy links on the data safety page
                privacy_links = await page.query_selector_all("a:text-matches('privacy policy', 'i')")
                app_specific_privacy_elements = []
                
                # Filter out Google's general privacy policy
                for link in privacy_links:
                    href = await link.get_attribute("href")
                    # Skip Google's general privacy policy
                    if href and not href.startswith('https://policies.google.com/privacy'):
                        app_specific_privacy_elements.append(link)
                        logger.info(f"Found app-specific privacy policy link with Playwright: {href}")
                
                if app_specific_privacy_elements:
                    privacy_url = await app_specific_privacy_elements[0].get_attribute("href")
                    logger.info(f"Found app-specific privacy policy link on data safety page: {privacy_url}")
                    
                    # Follow the link to get the final URL after any redirects
                    privacy_page = await context.new_page()
                    await privacy_page.goto(privacy_url, wait_until="domcontentloaded", timeout=30000)
                    pp_url = privacy_page.url
                    logger.info(f"Final privacy policy URL after redirects: {pp_url}")
                    
                    await privacy_page.close()
                    await browser.close()
                    
                    return PrivacyResponse(
                        url=url,
                        pp_url=pp_url,
                        success=True,
                        message=f"Found privacy policy for {app_info} on data safety page",
                        method_used="play_store_data_safety"
                    )
                else:
                    # No direct link found, check for text with URLs
                    info_element = await page.query_selector("text=/For more information.*privacy policy/i")
                    if info_element:
                        # Get the text and try to extract the link using regex
                        info_text = await info_element.text_content()
                        # Look for URL-like patterns in the text
                        url_match = re.search(r'https?://[^\s"\']+', info_text)
                        if url_match:
                            privacy_url = url_match.group(0)
                            # Skip Google's general privacy policy
                            if not privacy_url.startswith('https://policies.google.com/privacy'):
                                logger.info(f"Extracted app-specific privacy policy URL from text: {privacy_url}")
                                
                                # Follow the link to get the final URL after any redirects
                                privacy_page = await context.new_page()
                                await privacy_page.goto(privacy_url, wait_until="domcontentloaded", timeout=30000)
                                pp_url = privacy_page.url
                                logger.info(f"Final privacy policy URL after redirects: {pp_url}")
                                
                                await privacy_page.close()
                                await browser.close()
                                
                                return PrivacyResponse(
                                    url=url,
                                    pp_url=pp_url,
                                    success=True,
                                    message=f"Found privacy policy for {app_info} in text description",
                                    method_used="play_store_data_safety_text"
                                )
                
                # If not found on data safety page, try the main app page
                logger.info(f"No app-specific privacy policy found on data safety page, checking app page")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Look for privacy policy links on the app page
                privacy_links = await page.query_selector_all('a:text-matches("privacy policy", "i")')
                for link in privacy_links:
                    href = await link.get_attribute('href')
                    if href and not href.startswith('https://play.google.com/intl') and not href.startswith('https://policies.google.com/privacy'):
                        # Skip Google's own privacy policy
                        logger.info(f"Found app-specific privacy policy link directly on app page: {href}")
                        
                        # Follow the link to get the final URL after any redirects
                        privacy_page = await context.new_page()
                        await privacy_page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        pp_url = privacy_page.url
                        logger.info(f"Final privacy policy URL after redirects: {pp_url}")
                        
                        await privacy_page.close()
                        await browser.close()
                        
                        return PrivacyResponse(
                            url=url,
                            pp_url=pp_url,
                            success=True,
                            message=f"Found privacy policy for {app_info} on app page",
                            method_used="play_store_app_page"
                        )
                
                # Try to find the developer website
                developer_links = await page.query_selector_all('a:has-text("Visit website")')
                if developer_links and len(developer_links) > 0:
                    developer_site = await developer_links[0].get_attribute('href')
                    logger.info(f"Found developer website: {developer_site}")
                    
                    # Check the developer website for privacy policy
                    try:
                        dev_page = await context.new_page()
                        await dev_page.goto(developer_site, wait_until="domcontentloaded", timeout=30000)
                        
                        # Look for privacy policy links
                        pp_links = await dev_page.query_selector_all('a:text-matches("privacy policy", "i")')
                        for link in pp_links:
                            href = await link.get_attribute('href')
                            if href and not href.startswith('https://policies.google.com/privacy'):
                                # Make sure it's an absolute URL
                                if not href.startswith(('http://', 'https://')):
                                    href = urljoin(developer_site, href)
                                
                                # Follow the link to get the final URL after any redirects
                                privacy_page = await context.new_page()
                                await privacy_page.goto(href, wait_until="domcontentloaded", timeout=30000)
                                pp_url = privacy_page.url
                                logger.info(f"Final privacy policy URL after redirects: {pp_url}")
                                
                                await privacy_page.close()
                                await dev_page.close()
                                await browser.close()
                                
                                return PrivacyResponse(
                                    url=url,
                                    pp_url=pp_url,
                                    success=True,
                                    message=f"Found privacy policy for {app_info} on developer website",
                                    method_used="play_store_developer_site"
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
    logger.warning(f"No app-specific privacy policy found for Google Play Store {app_info}")
    return PrivacyResponse(
        url=url,
        success=False,
        message=f"Could not find app-specific privacy policy for {app_info}. Please check the app's page manually.",
        method_used="play_store_failed"
    )


async def standard_privacy_finder(variations_to_try, headers, session) -> PrivacyResponse:
    """Standard approach using requests and BeautifulSoup."""
    
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
            
            # Find the Privacy Policy link from the page
            logger.info(f"Searching for privacy link in {final_url}")
            privacy_link = find_privacy_link(final_url, soup)
            
            if privacy_link:
                logger.info(f"Found privacy link: {privacy_link} in {final_url} ({variation_type})")
                return PrivacyResponse(
                    url=final_url,  # Return the actual URL after redirects
                    pp_url=privacy_link,
                    success=True,
                    message=f"Privacy Policy link found on final destination page: {final_url}" + 
                            (f" (Found at {variation_type})" if variation_type != "original exact url" else ""),
                    method_used="standard"
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
        head_response = session.head(base_url, headers=headers, timeout=10, allow_redirects=True)
        final_url = head_response.url
        
        response = session.get(final_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        return PrivacyResponse(
            url=final_url,  # Return the final URL after redirects
            success=False,
            message=f"No Privacy Policy link found with standard method on the final destination page: {final_url}.",
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
        return PrivacyResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed"
        )
    except Exception as e:
        error_msg = f"Error processing URL {base_url} with standard method: {str(e)}"
        
        # Return the error in the response
        return PrivacyResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed"
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
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
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
            soup = BeautifulSoup(content, 'html.parser')
            
            # Find links using our existing function
            privacy_link = find_privacy_link(final_url, soup)
            
            # If standard approach didn't find links, try using Playwright's own selectors
            if not privacy_link:
                # Try to find links with text containing privacy terms
                privacy_keywords = ["privacy", "privacy policy", "data protection", "cookie", "gdpr"]
                
                for keyword in privacy_keywords:
                    # Use case-insensitive search for links with text containing the keyword
                    links = await page.query_selector_all(f'a:text-matches("{keyword}", "i")')
                    
                    for link in links:
                        href = await link.get_attribute('href')
                        if href and not href.startswith('javascript:') and href != '#':
                            privacy_link = urljoin(final_url, href)
                            break
                    
                    if privacy_link:
                        break
                
                # If still not found, try clicking "I agree" or cookie consent buttons to reveal privacy links
                if not privacy_link:
                    # Try to find and click buttons that might reveal privacy content
                    consent_buttons = await page.query_selector_all('button:text-matches("(accept|agree|got it|cookie|consent)", "i")')
                    
                    for button in consent_buttons:
                        try:
                            await button.click()
                            await page.wait_for_timeout(1000)  # Wait for any changes to take effect
                            
                            # Check for new links after clicking
                            content_after_click = await page.content()
                            soup_after_click = BeautifulSoup(content_after_click, 'html.parser')
                            privacy_link = find_privacy_link(final_url, soup_after_click)
                            
                            if privacy_link:
                                break
                        except:
                            continue
            
            # Close the browser
            await browser.close()
            
            if privacy_link:
                return PrivacyResponse(
                    url=final_url,
                    pp_url=privacy_link,
                    success=True,
                    message=f"Privacy Policy link found using JavaScript-enabled browser rendering on page: {final_url}",
                    method_used="playwright"
                )
            else:
                return PrivacyResponse(
                    url=final_url,
                    success=False,
                    message=f"No Privacy Policy link found even with JavaScript-enabled browser rendering on page: {final_url}",
                    method_used="playwright_failed"
                )
    
    except Exception as e:
        error_msg = f"Error using Playwright to process URL {url}: {str(e)}"
        logger.error(error_msg)
        
        return PrivacyResponse(
            url=url,
            success=False,
            message=error_msg,
            method_used="playwright_failed"
        )


def find_privacy_link(url, soup):
    """Find and return the Privacy Policy link from a webpage."""
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
            
            # Special case for GitHub repository root - look for policy files in repo
            if len(path_parts) == 2:
                logger.info("GitHub repository root page - checking for policy files in repo")
                
                # Look for common privacy policy files in repo
                common_privacy_filenames = [
                    'privacy', 'privacy.md', 'privacy-policy', 'privacy-policy.md', 
                    'privacy_policy.md', 'data-policy', 'data-policy.md', 'data-protection'
                ]
                
                # Check repository files listing
                repo_files = soup.find_all('a', class_='js-navigation-open')
                for file_link in repo_files:
                    href = file_link['href'].lower()
                    text = file_link.text.lower().strip()
                    
                    logger.info(f"Repository file: {text} ({href})")
                    
                    # Check for privacy policy files by name
                    if any(filename in text for filename in common_privacy_filenames) or "privacy" in text:
                        policy_link = urljoin(url, href)
                        logger.info(f"Found privacy policy file in repository: {policy_link}")
                        return policy_link
                
                # Also check the README.md for privacy policy links or sections
                readme_link = soup.find('a', string=lambda s: s and s.lower() == 'readme.md')
                if readme_link:
                    logger.info("Found README.md, checking for privacy links")
                    # We've found the README, now check if it has a privacy section
                    readme_content = soup.find('article', class_='markdown-body')
                    if readme_content:
                        # Look for headers about privacy
                        headers = readme_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                        for header in headers:
                            header_text = header.get_text().lower()
                            if 'privacy' in header_text or 'data' in header_text:
                                logger.info(f"Found privacy section in README: {header_text}")
                                # If the README itself has a privacy section, return the README URL
                                return urljoin(url, readme_link['href'])
                        
                        # Also check for links to privacy policy files
                        for link in readme_content.find_all('a', href=True):
                            href = link['href'].lower()
                            text = link.text.lower().strip()
                            
                            if 'privacy' in text or 'privacy' in href:
                                policy_link = urljoin(url, link['href'])
                                logger.info(f"Found privacy policy link in README: {policy_link}")
                                return policy_link
            
            # For specific GitHub paths like security/policy
            if len(path_parts) >= 4 and path_parts[2] == 'security' and path_parts[3] == 'policy':
                logger.info(f"GitHub security policy page detected")
                
                # Special case for nodejs/node repository
                if path_parts[0].lower() == 'nodejs' and path_parts[1].lower() == 'node':
                    logger.info("Special case: NodeJS security policy page")
                    
                    # For NodeJS security policy, look for specific content patterns
                    # Including the page content itself as a privacy document if it mentions data handling
                    page_text = soup.get_text().lower()
                    if 'security' in page_text and any(term in page_text for term in 
                                               ['personal data', 'data handling', 'privacy', 'confidential']):
                        logger.info(f"NodeJS security policy contains privacy information - treating page itself as privacy content")
                        return url  # Return the current URL as it contains privacy policy info
                
                # For security policy pages, try to find specific content
                # Look for links containing 'security' or 'report' in the content
                security_keywords = ['vulnerability', 'report', 'security', 'disclose', 'disclosure', 'security policy']
                
                # First check if the page itself contains privacy information
                page_text = soup.get_text().lower()
                
                if any(term in page_text for term in ['personal data', 'data handling', 'privacy', 'confidential']):
                    # If the security policy itself mentions privacy, return the URL
                    logger.info(f"Security policy page contains privacy information - treating as privacy content")
                    return url
                
                # Otherwise look for links to privacy policy or guidance
                for link in soup.find_all('a', href=True):
                    href = link['href'].lower()
                    text = link.text.lower().strip()
                    
                    if 'privacy' in text or 'privacy' in href:
                        policy_link = urljoin(url, link['href'])
                        logger.info(f"Found privacy link in security policy: {policy_link}")
                        return policy_link
            
            # For GitHub repository SECURITY.md file
            if len(path_parts) == 4 and path_parts[2] == 'blob' and path_parts[3].lower() == 'main':
                logger.info(f"Checking if this is a SECURITY.md file")
                
                file_name_elem = soup.find('strong', {'data-testid': 'file-name'})
                if file_name_elem and file_name_elem.text.lower() == 'security.md':
                    logger.info(f"SECURITY.md file detected")
                    
                    # Check if it contains privacy information
                    page_text = soup.get_text().lower()
                    if any(term in page_text for term in ['personal data', 'data handling', 'privacy', 'confidential']):
                        logger.info(f"SECURITY.md contains privacy information - treating as privacy content")
                        return url
            
            # Special case for repository "Settings" pages
            if len(path_parts) >= 3 and path_parts[2] == 'settings':
                logger.info(f"GitHub repository settings page detected")
                
                # Look for specific sections in settings
                headings = soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
                for heading in headings:
                    heading_text = heading.get_text().lower()
                    if 'security' in heading_text or 'privacy' in heading_text:
                        logger.info(f"Found heading related to security or privacy: {heading_text}")
                        
                        # Check surrounding links
                        for link in heading.parent.find_all('a', href=True) if heading.parent else []:
                            href = link['href'].lower()
                            text = link.text.lower().strip()
                            
                            if 'privacy' in text or 'privacy' in href:
                                policy_link = urljoin(url, link['href'])
                                logger.info(f"Found privacy link in settings: {policy_link}")
                                return policy_link
            
            # Try to find repository-specific containers (like .Layout-sidebar)
            repo_containers = soup.select('.Layout-sidebar')
            
            logger.info(f"Found {len(repo_containers)} repository-specific containers")
            
            for container in repo_containers:
                for link in container.find_all('a', href=True):
                    href = link['href'].lower()
                    text = link.text.lower().strip()
                    
                    # Log all links in repo containers for debugging
                    logger.info(f"Repository container link: text='{text}', href='{href}'")
                    
                    # Check for privacy-related keywords
                    if ('privacy' in text or 'privacy' in href or 'data protection' in text) and not href.startswith('https://docs.github.com/'):
                        github_repo_specific_link = urljoin(url, link['href'])
                        logger.info(f"Found repository-specific privacy link: {github_repo_specific_link}")
                        # If we find a repo-specific link, use it immediately
                        return github_repo_specific_link

    # Common terms for Privacy Policy links with more specific matching
    privacy_terms = [
        'privacy', 'privacy policy', 'data policy', 'data protection', 
        'privacy notice', 'cookie policy', 'personal data', 'data processing',
        'privacy statement', 'data protection policy', 'privacy & cookies',
        'privacy and cookies', 'cookie notice', 'gdpr', 'confidentiality',
        'information security', 'privacy preferences', 'privacy settings',
        'private policy', 'privacy rights', 'privacy choices'
    ]

    # Common CSS selectors for footers and legal sections
    footer_selectors = [
        'footer', '.footer', '#footer', '.site-footer', '.global-footer',
        '.legal-footer', '.bottom', '.bottom-links', '.legal-links',
        '.legal-section', '.copyright-section', 'div[role="contentinfo"]',
        '.nav__legal', '.links', '.legal', '.legal-links', '.legal-container',
        '.legal-wrapper', '.copyright', '.copyright-section', '.copyrights',
        'nav', '.nav', 'menu', '.menu', '.social-links', '.meta-links',
        '.bottom-nav', '.nav-links', '#privacy-footer', '.footer-links',
        'div.footer', 'div.foot', 'div[class*="footer"]', 'div[class*="foot"]',
        'div[id*="footer"]', 'div[id*="foot"]'
    ]
    
    # Collect all candidate links
    candidate_links = []
    
    # First check for standalone "Privacy" links in the page
    for link in soup.find_all('a', href=True):
        if link.text.lower().strip() == 'privacy':
            # Check if this is a GitHub global privacy link
            if is_github_repo and 'docs.github.com' in link['href']:
                # Store but don't immediately return GitHub's global privacy policy for repos
                candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
            else:
                privacy_link = urljoin(url, link['href'])
                candidate_links.append((privacy_link, 10, "exact_match"))
    
    # Check specific "your privacy" or "data" phrases pattern - look for text, then nearby links
    privacy_elements = []
    
    # Find all elements with variations of privacy-related text
    for element in soup.find_all(text=lambda text: text and ('privacy' in text.lower() or 'data protection' in text.lower())):
        privacy_elements.append(element.parent)
        # Also check parent elements
        if element.parent:
            privacy_elements.append(element.parent)
            if element.parent.parent:
                privacy_elements.append(element.parent.parent)
    
    # For each element containing privacy text, check nearby links
    for element in privacy_elements:
        # Check direct child links
        for link in element.find_all('a', href=True):
            href = link['href'].lower()
            text = link.text.lower().strip()
            
            # Look for privacy-related links
            if 'privacy' in text or any(term in href for term in privacy_terms):
                # Check if this is a GitHub global privacy link
                if is_github_repo and 'docs.github.com' in link['href']:
                    candidate_links.append((urljoin(url, link['href']), 2, "global_privacy"))
                else:
                    privacy_link = urljoin(url, link['href'])
                    candidate_links.append((privacy_link, 9, "contextual_link"))
                    
        # Check sibling elements for links
        if element.next_sibling:
            for link in element.next_sibling.find_all('a', href=True) if hasattr(element.next_sibling, 'find_all') else []:
                href = link['href'].lower()
                text = link.text.lower().strip()
                
                if 'privacy' in text or any(term in href for term in privacy_terms):
                    if is_github_repo and 'docs.github.com' in link['href']:
                        candidate_links.append((urljoin(url, link['href']), 2, "global_privacy"))
                    else:
                        candidate_links.append((urljoin(url, link['href']), 8, "sibling_link"))
    
    # Check footers for "your privacy" pattern
    footers = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in str(c).lower() or 'bottom' in str(c).lower()))
    for footer in footers:
        footer_text = footer.get_text().lower()
        if 'privacy' in footer_text:
            # Look for a link with "privacy" text
            for link in footer.find_all('a', href=True):
                if link.text.lower().strip() == 'privacy':
                    if is_github_repo and 'docs.github.com' in link['href']:
                        candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
                    else:
                        candidate_links.append((urljoin(url, link['href']), 7, "footer_link"))
    
    # Check the absolute bottom of the page (last few elements) where legal links often appear
    all_elements = list(soup.find_all())
    bottom_elements = all_elements[-30:] if len(all_elements) > 30 else all_elements
    
    for element in bottom_elements:
        for link in element.find_all('a', href=True) if hasattr(element, 'find_all') else []:
            href = link['href'].lower()
            text = link.text.lower().strip()
            
            if text == 'privacy' or (any(term == text for term in privacy_terms)) or any(term in href for term in privacy_terms):
                if is_github_repo and 'docs.github.com' in link['href']:
                    candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
                else:
                    candidate_links.append((urljoin(url, link['href']), 6, "bottom_link"))
    
    # Look for form elements with "agree to" or "data" phrases, and nearby links
    for form in soup.find_all('form'):
        form_text = form.get_text().lower()
        if ('privacy' in form_text or 'data' in form_text) and 'agree' in form_text:
            for link in form.find_all('a', href=True):
                if 'privacy' in link.text.lower() or any(term in link['href'].lower() for term in privacy_terms):
                    candidate_links.append((urljoin(url, link['href']), 8, "form_link"))
    
    # First priority: Look for links with exact matches in text or href
    for link in soup.find_all('a', href=True):
        href = link['href'].lower()
        text = link.text.lower().strip()
        
        # Skip empty links or javascript links
        if not href or href.startswith('javascript:') or href == '#':
            continue
            
        # Check for exact matches in text
        if any(text == term for term in privacy_terms):
            if is_github_repo and 'docs.github.com' in link['href']:
                candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
            else:
                candidate_links.append((urljoin(url, link['href']), 7, "exact_text_match"))
            
        # Check for exact matches in href
        if any(term in href.split('/') for term in privacy_terms) or any(term in href.split('-') for term in privacy_terms):
            if is_github_repo and 'docs.github.com' in link['href']:
                candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
            else:
                candidate_links.append((urljoin(url, link['href']), 6, "href_match"))
    
    # Second priority: Look for partial matches if no exact match found
    for link in soup.find_all('a', href=True):
        href = link['href'].lower()
        text = link.text.lower().strip()
        
        # Skip empty links or javascript links
        if not href or href.startswith('javascript:') or href == '#':
            continue
            
        # Check for Privacy terms in text or href
        if any(term in text for term in privacy_terms) or any(term in href for term in privacy_terms):
            # Avoid false positives like "financial privacy" or "private repositories"
            if not any(false_term in href for false_term in ['financial privacy', 'private repo']):
                if is_github_repo and 'docs.github.com' in link['href']:
                    candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
                else:
                    candidate_links.append((urljoin(url, link['href']), 5, "partial_match"))
    
    # Third priority: Look specifically in footer and legal sections
    for selector in footer_selectors:
        try:
            footer_elements = soup.select(selector)
            for footer in footer_elements:
                for link in footer.find_all('a', href=True):
                    href = link['href'].lower()
                    text = link.text.lower().strip()
                    
                    if any(term in text for term in privacy_terms) or any(term in href for term in privacy_terms):
                        if is_github_repo and 'docs.github.com' in link['href']:
                            candidate_links.append((urljoin(url, link['href']), 1, "global_footer"))
                        else:
                            candidate_links.append((urljoin(url, link['href']), 4, "footer_area"))
        except:
            # Skip any CSS selector errors
            continue
    
    # Fourth priority: Look for links near "Terms of Service" mentions
    tos_links = []
    for link in soup.find_all('a', href=True):
        text = link.text.lower().strip()
        href = link['href'].lower()
        if 'terms' in text or 'terms' in href or 'tos' in href:
            tos_links.append(link)
    
    # If we found terms links, check for privacy links near them (siblings or within same parent)
    for tos_link in tos_links:
        # Check siblings
        for sibling in tos_link.parent.find_all('a', href=True):
            if sibling == tos_link:
                continue
            text = sibling.text.lower().strip()
            href = sibling['href'].lower()
            
            if (any(term == text for term in privacy_terms) or 
                any(term in href for term in privacy_terms)):
                if is_github_repo and 'docs.github.com' in sibling['href']:
                    candidate_links.append((urljoin(url, sibling['href']), 2, "global_near_tos"))
                else:
                    candidate_links.append((urljoin(url, sibling['href']), 6, "near_tos"))
            
        # Check parent's siblings (for multi-level structures)
        try:
            for parent_sibling in tos_link.parent.parent.find_all('a', href=True):
                if parent_sibling == tos_link:
                    continue
                text = parent_sibling.text.lower().strip()
                href = parent_sibling['href'].lower()
                
                if (any(term == text for term in privacy_terms) or 
                    any(term in href for term in privacy_terms)):
                    if is_github_repo and 'docs.github.com' in parent_sibling['href']:
                        candidate_links.append((urljoin(url, parent_sibling['href']), 2, "global_near_tos"))
                    else:
                        candidate_links.append((urljoin(url, parent_sibling['href']), 5, "parent_sibling_tos"))
        except:
            pass
            
        # Check in surrounding elements
        try:
            parent_element = tos_link.parent
            
            # Check previous and next elements
            for element in [parent_element.previous_sibling, parent_element.next_sibling]:
                if element and hasattr(element, 'find_all'):
                    for link in element.find_all('a', href=True):
                        text = link.text.lower().strip()
                        href = link['href'].lower()
                        
                        if (any(term == text for term in privacy_terms) or 
                            any(term in href for term in privacy_terms)):
                            if is_github_repo and 'docs.github.com' in link['href']:
                                candidate_links.append((urljoin(url, link['href']), 2, "global_near_tos"))
                            else:
                                candidate_links.append((urljoin(url, link['href']), 5, "surrounding_tos"))
        except:
            pass
    
    # Fifth priority: Look for typical links that contain words related to privacy or GDPR
    for link in soup.find_all('a', href=True):
        href = link['href'].lower()
        
        if re.search(r'/(privacy|data|cookies|confidential|gdpr)(/|$)', href):
            if is_github_repo and 'docs.github.com' in link['href']:
                candidate_links.append((urljoin(url, link['href']), 1, "global_regex"))
            else:
                candidate_links.append((urljoin(url, link['href']), 3, "regex_match"))
    
    # NEW: Filter out links that are likely news articles or blog posts rather than privacy policies
    filtered_candidates = []
    for link_url, score, method in candidate_links:
        parsed_link = urlparse(link_url)
        path = parsed_link.path.lower()
        
        # Check for date patterns in URL path (common in news/blog URLs)
        has_date_pattern = re.search(r'/20\d\d/\d\d?/|/\d{4}-\d{2}-\d{2}|/news/\d+/', path)
        
        # Check for news/article indicators in URL
        news_indicators = ['article', 'post', 'blog', 'news', '/story/', '/stories/', 
                          'press-release', '/report/', '/posts/', '/tag/']
        is_likely_news = any(indicator in path for indicator in news_indicators)
        
        # If it's a news/blog URL, significantly reduce its score
        if has_date_pattern or is_likely_news:
            # Still include it, but with a much lower score
            filtered_candidates.append((link_url, score - 8, f"{method}_filtered"))
            logger.info(f"Filtered likely news/blog URL: {link_url}")
        else:
            # Not a news URL, keep the original score
            filtered_candidates.append((link_url, score, method))
    
    # Sort the candidates by score (highest first)
    sorted_candidates = sorted(filtered_candidates, key=lambda x: x[1], reverse=True)
    
    # For debugging, log the top candidates
    for i, (candidate_url, score, method) in enumerate(sorted_candidates[:5]):
        logger.info(f"Candidate {i+1}: {candidate_url} (score: {score}, method: {method})")
    
    # Choose the highest scoring candidate
    privacy_link = sorted_candidates[0][0] if sorted_candidates else None
    
    # Validate the URL
    if privacy_link:
        # Make sure it's not pointing to a different domain (unless it's a privacy subdomain)
        privacy_domain = urlparse(privacy_link).netloc
        original_domain = urlparse(original_url).netloc
        
        if privacy_domain and original_domain:
            base_domain = original_domain
            if base_domain.startswith('www.'):
                base_domain = base_domain[4:]
            
            root_domain = get_root_domain(original_domain)
            privacy_root_domain = get_root_domain(privacy_domain)
            
            # Allow certain scenarios:
            # 1. Same domain
            # 2. Privacy subdomain (privacy.example.com)
            # 3. Legal subdomain (legal.example.com)
            # 4. Policies subdomain (policies.example.com)
            # 5. Same root domain (different subdomains but same base)
            # 6. Special case for GitHub to allow docs.github.com from github.com
            # 7. Domain likely for privacy policies based on path structure
            # 8. If the link text explicitly mentions the site (suggesting it's legitimate)
            
            allowed = False
            
            if privacy_domain == original_domain:
                allowed = True
                logger.info(f"Privacy link on same domain: {privacy_domain}")
            elif privacy_domain.startswith('privacy.') and original_domain.endswith(privacy_domain[8:]):
                allowed = True
                logger.info(f"Privacy link on privacy subdomain: {privacy_domain}")
            elif privacy_domain.startswith('legal.') and original_domain.endswith(privacy_domain[6:]):
                allowed = True
                logger.info(f"Privacy link on legal subdomain: {privacy_domain}")
            elif privacy_domain.startswith('policies.') and original_domain.endswith(privacy_domain[9:]):
                allowed = True
                logger.info(f"Privacy link on policies subdomain: {privacy_domain}")
            elif root_domain and privacy_root_domain and root_domain == privacy_root_domain:
                allowed = True
                logger.info(f"Privacy link on same root domain: {root_domain}")
            # Special case for GitHub
            elif 'github.com' in original_domain and privacy_domain == 'docs.github.com':
                allowed = True
                logger.info(f"GitHub special case: docs.github.com")
            # Check for conventional privacy policy paths
            elif re.search(r'/(privacy|legal|terms|privacypolicy|privacy-policy|policies)(/|$)', urlparse(privacy_link).path.lower()):
                allowed = True
                logger.info(f"Privacy link follows conventional privacy policy URL structure: {privacy_link}")
            
            # If the link is still not allowed but looks like a corporate domain for a parent company, allow it
            if not allowed:
                # Count link occurrences to see if it's likely an official corporate domain
                link_count = 0
                corporate_indicators = ['terms', 'legal', 'about', 'contact', 'policy']
                
                for link in soup.find_all('a', href=True):
                    href = link.get('href')
                    if href and privacy_domain in href:
                        link_count += 1
                        # If there are many links to this domain, it's likely legitimate
                        if link_count >= 3:
                            allowed = True
                            logger.info(f"Privacy link domain appears multiple times in page, likely legitimate: {privacy_domain}")
                            break
                    
                    # Check if any link text suggests this is a parent company
                    link_text = link.text.lower().strip()
                    if 'vox media' in link_text or 'parent company' in link_text or 'our company' in link_text:
                        if re.search(privacy_domain, href, re.IGNORECASE):
                            allowed = True
                            logger.info(f"Privacy link domain matched text suggesting a parent company: {privacy_domain}")
                            break
                
                # Check if domain appears to be corporate/company domain
                if not allowed and re.search(r'(media|inc|corp|company|digital|group|holdings)', privacy_domain):
                    # Look for other corporate indicators in the URL
                    if any(indicator in urlparse(privacy_link).path.lower() for indicator in corporate_indicators):
                        allowed = True
                        logger.info(f"Privacy link appears to be on a corporate domain with policy indicators: {privacy_domain}")
            
            if not allowed:
                logger.warning(f"Rejecting privacy link with different domain: {privacy_domain} (original: {original_domain})")
                privacy_link = None  # Reject links to unrelated domains
            else:
                logger.info(f"Accepted privacy link: {privacy_link}")
    
    return privacy_link

def get_root_domain(domain: str) -> str:
    """Extract root domain from a domain name."""
    if not domain:
        return ""
        
    # Remove www prefix if present
    if domain.startswith('www.'):
        domain = domain[4:]
        
    parts = domain.split('.')
    
    # Handle common TLDs like .co.uk
    if len(parts) > 2:
        if parts[-2] in ['co', 'com', 'org', 'net', 'gov', 'edu'] and parts[-1] in ['uk', 'au', 'br', 'jp', 'in']:
            return '.'.join(parts[-3:])
    
    # For most domains, return the last two parts
    if len(parts) >= 2:
        return '.'.join(parts[-2:])
    
    return domain 