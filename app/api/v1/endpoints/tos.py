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


def find_tos_link(url: str, soup: BeautifulSoup) -> Optional[str]:
    """
    Find Terms of Service link in the soup object using dynamic pattern matching.
    Uses a hierarchical approach to find the most relevant ToS link.
    """
    # Get the base domain for comparison
    base_domain = urlparse(url).netloc.lower()
    
    def is_same_domain_or_subdomain(href: str) -> bool:
        """Check if a URL is from the same domain or its subdomain."""
        try:
            href_domain = urlparse(href).netloc.lower()
            return href_domain == base_domain or href_domain.endswith('.' + base_domain)
        except:
            return False

    def is_valid_tos_path(path: str) -> bool:
        """Check if a URL path is likely to be a ToS page."""
        path = path.lower()
        valid_patterns = [
            '/terms',
            '/tos',
            '/terms-of-service',
            '/terms-of-use',
            '/terms-and-conditions',
            '/legal/terms',
            '/legal',
            '/user-agreement',
            '/conditions'
        ]
        return any(pattern in path for pattern in valid_patterns)

    def get_link_context_score(link) -> float:
        """Calculate a score based on the link's context."""
        score = 0.0
        
        # Check parent elements
        parent = link.parent
        while parent and parent.name:
            # Check element classes and IDs
            classes = ' '.join(parent.get('class', [])).lower()
            element_id = parent.get('id', '').lower()
            
            # Score based on containing elements
            if parent.name in ['footer', 'nav']:
                score += 1.0
            if any(term in classes or term in element_id for term in ['footer', 'legal', 'bottom', 'terms']):
                score += 1.0
            if any(term in classes or term in element_id for term in ['menu', 'nav', 'links']):
                score += 0.5
                
            # Look for nearby privacy/legal links
            siblings = parent.find_all(['a'], recursive=False)
            for sib in siblings:
                sib_text = sib.get_text().lower()
                if 'privacy' in sib_text or 'legal' in sib_text:
                    score += 0.5
                    break
            
            parent = parent.parent
        
        return score

    # First pass: Look for exact matches in common ToS paths
    for link in soup.find_all('a', href=True):
        href = link.get('href', '').strip()
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
            
        try:
            absolute_url = urljoin(url, href)
            parsed_url = urlparse(absolute_url)
            
            # Check if it's a valid ToS path on the same domain
            if is_same_domain_or_subdomain(absolute_url) and is_valid_tos_path(parsed_url.path):
                link_text = link.get_text().strip().lower()
                if any(term in link_text for term in ['terms', 'tos', 'conditions']):
                    return absolute_url
        except:
            continue

    # Second pass: Score-based approach for less obvious matches
    candidates = []
    
    for link in soup.find_all('a', href=True):
        href = link.get('href', '').strip()
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
            
        try:
            absolute_url = urljoin(url, href)
            if not is_same_domain_or_subdomain(absolute_url):
                continue
                
            score = 0.0
            link_text = link.get_text().strip().lower()
            
            # Score based on link text
            if 'terms of service' in link_text or 'terms of use' in link_text:
                score += 3.0
            elif 'terms and conditions' in link_text:
                score += 2.5
            elif 'terms' in link_text.split() or 'tos' in link_text.split():
                score += 2.0
            elif 'legal' in link_text:
                score += 1.0
                
            # Score based on URL path
            path = urlparse(absolute_url).path.lower()
            if '/terms-of-service' in path or '/terms-of-use' in path:
                score += 3.0
            elif '/terms-and-conditions' in path:
                score += 2.5
            elif '/terms' in path or '/tos' in path:
                score += 2.0
            elif '/legal' in path:
                score += 1.0
                
            # Add context score
            score += get_link_context_score(link)
            
            # Penalize if the path suggests it's not a ToS page
            penalties = ['/blog/', '/news/', '/article/', '/press/', '/2023/', '/2024/', 
                       '/posts/', '/category/', '/tag/', '/search/', '/product/']
            if any(penalty in path for penalty in penalties):
                score -= 5.0
                
            if score > 2.0:  # Only consider high-scoring candidates
                candidates.append((absolute_url, score))
                
        except:
            continue
    
    # Return the highest scoring candidate
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]
    
    return None


async def standard_tos_finder(variations_to_try: List[Tuple[str, str]], headers: dict, session: requests.Session) -> TosResponse:
    """
    Try to find ToS link using standard requests + BeautifulSoup method.
    """
    for url, variation_type in variations_to_try:
        try:
            # Make the request with a longer timeout
            response = session.get(url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            # Get the final URL after any redirects
            final_url = response.url
            
            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Try to find ToS link
            tos_url = find_tos_link(str(final_url), soup)
            
            if tos_url:
                return TosResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message=f"Terms of Service link found on final destination page: {final_url} (Found at {variation_type})",
                    method_used="standard"
                )
                
        except Exception as e:
            logger.error(f"Error processing {url}: {str(e)}")
            continue
    
    return TosResponse(
        url=variations_to_try[0][0],  # Use the original URL
        success=False,
        message="No Terms of Service link found with standard method",
        method_used="standard_failed"
    )


@router.post("/tos", response_model=TosResponse, responses={
    200: {"description": "Terms of Service found successfully"},
    404: {"description": "Terms of Service not found", "model": TosResponse}
})
async def find_tos(request: TosRequest, response: Response) -> TosResponse:
    """
    Takes a base URL and returns the Terms of Service page URL.
    This endpoint accepts partial URLs like 'google.com' and will
    automatically add the 'https://' protocol prefix if needed.
    """
    original_url = request.url  # The exact URL provided by the user, already normalized
    logger.info(f"Processing ToS request for URL: {original_url}")
    
    # Enhanced browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }

    # Create a session to maintain cookies across requests
    session = requests.Session()
    
    # Get URL variations to try
    variations_with_types = []
    variations_with_types.append((original_url, "original exact url"))
    
    # Get base domain variations for fallback
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
    playwright_result = await playwright_tos_finder(original_url)
    
    if playwright_result.success:
        logger.info(f"Found ToS link with Playwright: {playwright_result.tos_url}")
        return playwright_result
    
    # If both methods failed, return 404
    logger.info(f"No ToS link found for {original_url} with any method")
    response.status_code = 404
    
    return TosResponse(
        url=original_url,
        success=False,
        message=f"No Terms of Service link found. Tried both standard scraping and JavaScript-enabled browser rendering.",
        method_used="both_failed"
    )


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
            page.set_default_timeout(45000)  # 45 seconds
            
            try:
                # Navigate to the URL with a longer timeout
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Get the final URL after any redirects
                final_url = page.url
                
                # Get the content
                content = await page.content()
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # Find links using our existing function
                tos_link = find_tos_link(final_url, soup)
                
                # If The Verge and no ToS link found, try additional strategies before giving up
                if 'theverge.com' in final_url and not tos_link:
                    logger.info("Extra processing for The Verge with Playwright")
                    
                    # Try scrolling to bottom where footer links usually are
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)  # Wait for any lazy-loaded content
                    
                    # Get updated content after scrolling
                    updated_content = await page.content()
                    updated_soup = BeautifulSoup(updated_content, 'html.parser')
                    
                    # Try to find 'Vox Media' links that might lead to ToS
                    vox_links = await page.query_selector_all('a:text-matches("Vox Media", "i")')
                    for link in vox_links:
                        try:
                            # Try clicking Vox Media link to see if it reveals more links
                            await link.click()
                            await page.wait_for_timeout(2000)
                            
                            # Check for ToS links after clicking
                            post_click_content = await page.content()
                            post_click_soup = BeautifulSoup(post_click_content, 'html.parser')
                            tos_link = find_tos_link(final_url, post_click_soup)
                            if tos_link:
                                break
                        except:
                            continue
                    
                    # If still not found, look for footer links directly
                    if not tos_link:
                        footers = updated_soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower()))
                        for footer in footers:
                            for link in footer.find_all('a', href=True):
                                href = link.get('href')
                                if href and "voxmedia.com" in href and any(term in href.lower() for term in ['/legal', '/terms', '/tos']):
                                    tos_link = href
                                    break
                            if tos_link:
                                break
                
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

# Rest of the file stays the same