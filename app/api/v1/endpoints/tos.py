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
    
    def get_domain_score(href: str) -> float:
        """Calculate domain relevance score."""
        try:
            href_domain = urlparse(href).netloc.lower()
            if href_domain == base_domain:
                return 2.0  # Highest score for exact domain match
            elif href_domain.endswith('.' + base_domain) or base_domain.endswith('.' + href_domain):
                return 1.5  # Good score for subdomain relationship
            elif any(known_domain in href_domain for known_domain in [
                'voxmedia.com',  # The Verge and other Vox Media sites
                'wordpress.com',  # WordPress hosted sites
                'squarespace.com',  # Squarespace hosted sites
                'wixsite.com',  # Wix hosted sites
                'shopify.com',  # Shopify stores
                'zendesk.com',  # Common help center host
                'helpscoutdocs.com',  # Help Scout hosted docs
                'google.com',  # Google services
                'facebook.com',  # Facebook services
                'apple.com',  # Apple services
                'amazon.com',  # Amazon services
                'github.com',  # GitHub services
                'microsoft.com'  # Microsoft services
            ]):
                return 1.0  # Known trusted domains
            return 0.0  # Unknown external domain
        except Exception:
            return -1.0  # Invalid URL

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

    def get_tos_relevance_score(href: str, link_text: str) -> float:
        """Calculate how likely a link is to be a ToS link based on its text and URL."""
        score = 0.0
        href_lower = href.lower()
        link_text_lower = link_text.lower()
        
        # Exact match patterns (highest priority)
        exact_patterns = [
            r'\bterms[-\s]of[-\s]service\b',
            r'\bterms[-\s]of[-\s]use\b',
            r'\bterms[-\s]and[-\s]conditions\b',
            r'\buser[-\s]agreement\b',
            r'\blegal[-\s]terms\b',
            r'\btos\b'
        ]
        
        # Strong URL patterns
        strong_url_patterns = [
            '/terms-of-service/',
            '/terms-of-use/',
            '/terms-and-conditions/',
            '/legal/terms/',
            '/tos/',
            '/terms/'
        ]
        
        # Check for exact matches in text (highest priority)
        if any(re.search(pattern, link_text_lower) for pattern in exact_patterns):
            score += 5.0
        
        # Check URL patterns
        if any(pattern in href_lower for pattern in strong_url_patterns):
            score += 3.0
        elif '/legal' in href_lower or '/terms' in href_lower or '/tos' in href_lower:
            score += 2.0
            
        # Check link text for partial matches
        if 'terms' in link_text_lower.split() or 'tos' in link_text_lower.split():
            score += 2.0
        elif 'legal' in link_text_lower:
            score += 1.0
            
        # Strong penalties for likely non-ToS content
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
            ('/consulting/', -5.0),
            ('/about/', -3.0),
            ('/contact/', -3.0)
        ]
        
        for pattern, penalty in penalties:
            if pattern in href_lower:
                score += penalty
        
        # Additional penalties for service/product pages
        service_indicators = ['service', 'product', 'solution', 'platform', 'technology', 'consulting', 'ai', 'cloud', 'digital']
        if any(indicator in link_text_lower for indicator in service_indicators):
            score -= 5.0
            
        # Require minimum length for link text to avoid false positives
        if len(link_text_lower.strip()) < 3:
            score -= 3.0
            
        return score

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
            ])
            
            # Skip empty or very short link text
            if len(link_text.strip()) < 3:
                continue
                
            # Calculate scores
            domain_score = get_domain_score(absolute_url)
            if domain_score < 0:  # Skip invalid URLs
                continue
                
            tos_score = get_tos_relevance_score(absolute_url, link_text)
            context_score = get_link_context_score(link)
            
            # Calculate final score with adjusted weights
            final_score = (tos_score * 2.0) + (context_score * 1.0) + (domain_score * 1.5)
            
            # Higher threshold for acceptance
            if final_score > 5.0:  # Increased from 3.0 to 5.0
                candidates.append((absolute_url, final_score))
                
        except Exception:
            continue
    
    # Return the highest scoring candidate
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        highest_score = candidates[0][1]
        
        # Only return if the score is significantly high
        if highest_score > 7.0:  # Added minimum threshold
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
    original_url = request.url
    logger.info(f"Processing ToS request for URL: {original_url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'max-age=0',
    }

    session = requests.Session()
    variations_with_types = [(original_url, "original exact url")]
    
    variations = prepare_url_variations(original_url)
    for idx, var_url in enumerate(variations[1:], 1):
        variations_with_types.append((var_url, f"variation_{idx}"))
    
    logger.info(f"URL variations to try: {variations_with_types}")
    
    standard_result = await standard_tos_finder(variations_with_types, headers, session)
    if standard_result.success:
        logger.info(f"Found ToS link with standard method: {standard_result.tos_url}")
        return standard_result
    
    logger.info(f"Standard method failed for {original_url}, trying with Playwright")
    playwright_result = await playwright_tos_finder(original_url)
    
    if playwright_result.success:
        logger.info(f"Found ToS link with Playwright: {playwright_result.tos_url}")
        return playwright_result
    
    logger.info(f"No ToS link found for {original_url} with any method")
    response.status_code = 404
    return TosResponse(
        url=original_url,
        success=False,
        message="No Terms of Service link found. Tried both standard scraping and JavaScript-enabled browser rendering.",
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
            page.set_default_timeout(45000)  # 45 seconds
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                final_url = page.url
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                tos_link = find_tos_link(final_url, soup)
                
                if 'theverge.com' in final_url and not tos_link:
                    logger.info("Extra processing for The Verge with Playwright")
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)
                    
                    updated_content = await page.content()
                    updated_soup = BeautifulSoup(updated_content, 'html.parser')
                    
                    vox_links = await page.query_selector_all('a:text-matches("Vox Media", "i")')
                    for link in vox_links:
                        try:
                            await link.click()
                            await page.wait_for_timeout(2000)
                            post_click_content = await page.content()
                            post_click_soup = BeautifulSoup(post_click_content, 'html.parser')
                            tos_link = find_tos_link(final_url, post_click_soup)
                            if tos_link:
                                break
                        except:
                            continue
                    
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
                
                if not tos_link:
                    tos_keywords = ["terms", "terms of service", "terms of use", "tos", "terms and conditions", "legal terms"]
                    for keyword in tos_keywords:
                        links = await page.query_selector_all(f'a:text-matches("{keyword}", "i")')
                        for link in links:
                            href = await link.get_attribute('href')
                            if href and not href.startswith('javascript:') and href != '#':
                                if not is_likely_article_link(href.lower(), urljoin(final_url, href)):
                                    tos_link = urljoin(final_url, href)
                                    break
                        if tos_link:
                            break
                    
                    if not tos_link:
                        consent_buttons = await page.query_selector_all('button:text-matches("(accept|agree|got it|cookie|consent)", "i")')
                        for button in consent_buttons:
                            try:
                                await button.click()
                                await page.wait_for_timeout(1000)
                                content_after_click = await page.content()
                                soup_after_click = BeautifulSoup(content_after_click, 'html.parser')
                                tos_link = find_tos_link(final_url, soup_after_click)
                                if tos_link:
                                    break
                            except:
                                continue
                
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