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
                        if href and not href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
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


def get_domain_score(href: str, base_domain: str) -> float:
    """Calculate domain relevance score with less dependency on known domains."""
    try:
        href_domain = urlparse(href).netloc.lower()
        if not href_domain:
            return 0.0
            
        # Same domain gets highest score
        if href_domain == base_domain:
            return 2.0
            
        # Subdomain relationship
        if href_domain.endswith('.' + base_domain) or base_domain.endswith('.' + href_domain):
            return 1.5
            
        # Check if the domains share a common root
        href_parts = href_domain.split('.')
        base_parts = base_domain.split('.')
        
        if len(href_parts) >= 2 and len(base_parts) >= 2:
            href_root = '.'.join(href_parts[-2:])
            base_root = '.'.join(base_parts[-2:])
            if href_root == base_root:
                return 1.0
                
        # For external domains, check if they look like legitimate policy hosts
        if any(term in href_domain for term in ['privacy', 'legal', 'policy', 'terms']):
            return 0.5
            
        # Don't heavily penalize external domains
        return 0.0
    except Exception:
        return -1.0

def get_footer_score(link) -> float:
    """Calculate a score based on whether the link is in a footer or similar bottom section."""
    score = 0.0
    
    # Check if the link itself is in a footer-like element
    parent = link.parent
    depth = 0
    max_depth = 5  # Don't go too far up the tree
    
    while parent and parent.name and depth < max_depth:
        # Check element name
        if parent.name in ['footer', 'tfoot']:
            score += 3.0
            break
            
        # Check classes and IDs
        classes = ' '.join(parent.get('class', [])).lower()
        element_id = parent.get('id', '').lower()
        
        # Strong footer indicators
        if any(term in classes or term in element_id for term in ['footer', 'bottom', 'btm']):
            score += 3.0
            break
            
        # Secondary footer indicators
        if any(term in classes or term in element_id for term in ['legal', 'copyright', 'links']):
            score += 1.5
        
        parent = parent.parent
        depth += 1
    
    return score

def find_privacy_link(url, soup):
    """Find and return the Privacy Policy link from a webpage."""
    base_domain = urlparse(url).netloc.lower()
    
    # Exact match patterns (highest priority)
    exact_patterns = [
        r'\bprivacy[-\s]policy\b',
        r'\bdata[-\s]protection\b',
        r'\bprivacy[-\s]notice\b',
        r'\bdata[-\s]policy\b',
        r'\bcookie[-\s]policy\b',
        r'\bprivacy[-\s]statement\b',
        r'\bgdpr\b'
    ]
    
    # Strong URL patterns
    strong_url_patterns = [
        '/privacy-policy/',
        '/privacy/',
        '/data-protection/',
        '/data-privacy/',
        '/privacy-notice/',
        '/cookie-policy/',
        '/gdpr/',
        '/data-policy/'
    ]
    
    # Strong penalties for likely non-privacy content
    penalties = [
        ('/blog/', -5.0),
        ('/news/', -5.0),
        ('/article/', -5.0),
        ('/press/', -5.0),
        ('/2023/', -5.0),
        ('/2024/', -5.0),
        ('/posts/', -5.0),
        ('/category/', -5.0),
        ('/tag/', -5.0),
        ('/search/', -5.0),
        ('/product/', -5.0),
        ('/services/', -5.0),
        ('/solutions/', -5.0),
        ('/ai/', -5.0),
        ('/cloud/', -5.0),
        ('/digital/', -5.0),
        ('/enterprise/', -5.0),
        ('/platform/', -5.0),
        ('/technology/', -5.0),
        ('/consulting/', -5.0)
    ]
    
    # Process all links
    candidates = []
    
    for link in soup.find_all('a', href=True):
        href = link.get('href', '').strip()
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
            
        try:
            absolute_url = urljoin(url, href)
            link_text = ' '.join([
                link.get_text().strip(),
                link.get('title', '').strip(),
                link.get('aria-label', '').strip()
            ]).lower()
            
            # Skip empty or very short link text
            if len(link_text.strip()) < 3:
                continue
            
            # Calculate base score
            score = 0.0
            
            # Get footer score first (highest priority)
            footer_score = get_footer_score(link)
            
            # Domain score with less weight
            domain_score = get_domain_score(absolute_url, base_domain)
            if domain_score < 0:  # Skip invalid URLs
                continue
            
            # Check for exact matches in text (high priority)
            if any(re.search(pattern, link_text) for pattern in exact_patterns):
                score += 6.0  # Increased weight for exact matches
            
            # Check URL patterns
            href_lower = absolute_url.lower()
            if any(pattern in href_lower for pattern in strong_url_patterns):
                score += 4.0  # Increased weight for strong URL patterns
            elif '/privacy' in href_lower or '/data' in href_lower or '/gdpr' in href_lower:
                score += 3.0
                
            # Check link text for partial matches
            if 'privacy' in link_text.split() or 'gdpr' in link_text.split():
                score += 3.0
            elif 'data' in link_text or 'cookie' in link_text:
                score += 2.0
                
            # Check for privacy-specific terms in URL path
            path = urlparse(absolute_url).path.lower()
            if any(term in path for term in ['privacy', 'data-protection', 'gdpr']):
                score += 2.0
                
            # Apply penalties
            for pattern, penalty in penalties:
                if pattern in href_lower:
                    score += penalty
            
            # Calculate final score with footer priority
            final_score = (score * 2.0) + (footer_score * 3.0) + (domain_score * 1.0)
            
            # Adjust threshold based on strong indicators
            threshold = 5.0
            if footer_score > 0:
                threshold = 4.0  # Lower threshold for footer links
            if any(re.search(pattern, link_text) for pattern in exact_patterns):
                threshold = 4.0  # Lower threshold for exact matches
            
            if final_score > threshold:
                candidates.append((absolute_url, final_score))
                
        except Exception:
            continue
    
    # Return the highest scoring candidate
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]
    
    return None

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