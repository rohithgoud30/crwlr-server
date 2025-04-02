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
    is_likely_article_link,
    get_root_domain,
    get_policy_patterns,
    get_policy_score,
    find_policy_by_class_id,
    is_likely_false_positive,
    is_correct_policy_type
)

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
    """Find Terms of Service link in the HTML soup."""
    # First try the high-priority class/ID based approach
    class_id_result = find_policy_by_class_id(soup, 'tos')
    if class_id_result:
        return class_id_result
        
    # If not found, proceed with the existing approach
    base_domain = urlparse(url).netloc.lower()
    is_legal_page = is_on_policy_page(url, 'tos')
    exact_patterns, strong_url_patterns = get_policy_patterns('tos')
    candidates = []
    
    # Get current domain info for domain-matching rules
    current_domain = urlparse(url).netloc.lower()
    
    # Iterate through all links to find the ToS
    for link in soup.find_all('a', href=True):
        href = link.get('href', '').strip()
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
            
        try:
            absolute_url = urljoin(url, href)
            
            # Skip likely false positives
            if is_likely_false_positive(absolute_url, 'tos'):
                continue
            
            # Extra check - skip privacy policy URLs when looking for ToS
            url_lower = absolute_url.lower()
            if '/privacy' in url_lower or 'privacy-policy' in url_lower or '/gdpr' in url_lower:
                logger.warning(f"Skipping privacy policy URL in ToS search: {absolute_url}")
                continue
                
            # General cross-domain policy handling
            # Check if this is a different domain from the original site
            target_domain = urlparse(absolute_url).netloc.lower()
            is_cross_domain = current_domain != target_domain
            
            if is_cross_domain:
                # For cross-domain links, be stricter - require explicit terms references
                if not any(term in url_lower for term in ['/terms', '/tos', '/legal/terms']):
                    logger.warning(f"Skipping cross-domain non-terms URL: {absolute_url}")
                    continue
                
            # Ensure this is not a Privacy URL
            if not is_correct_policy_type(absolute_url, 'tos'):
                continue
            
            link_text = ' '.join([
                link.get_text().strip(),
                link.get('title', '').strip(),
                link.get('aria-label', '').strip()
            ]).lower()
            
            # Skip links that are explicitly privacy policies
            if 'privacy' in link_text and not any(term in link_text for term in ['terms', 'tos', 'conditions']):
                logger.warning(f"Skipping privacy link text in ToS search: {link_text}")
                continue
                
            if len(link_text.strip()) < 3:
                continue
    
            score = 0.0
            footer_score = get_footer_score(link)
            domain_score = get_domain_score(absolute_url, base_domain)
            
            if domain_score < 0:
                continue
            
            href_domain = urlparse(absolute_url).netloc.lower()
            
            # Domain-specific scoring adjustments
            if href_domain == current_domain:
                # Strongly prefer same-domain links
                score += 15.0
                logger.info(f"Applied same-domain bonus for {absolute_url}")
            elif is_cross_domain:
                # Apply penalty for cross-domain links
                score -= 8.0
                logger.info(f"Applied cross-domain penalty for {absolute_url}")
            
            if is_legal_page and href_domain != base_domain:
                continue
            
            if any(re.search(pattern, link_text) for pattern in exact_patterns):
                score += 6.0
            
            href_lower = absolute_url.lower()
            if any(pattern in href_lower for pattern in strong_url_patterns):
                score += 4.0
            
            score += get_policy_score(link_text, absolute_url, 'tos')
            
            for pattern, penalty in get_common_penalties():
                if pattern in href_lower:
                    score += penalty
                    
            # Apply additional penalty for URLs with privacy terms
            if 'privacy' in url_lower:
                score -= 10.0
            
            final_score = (score * 2.0) + (footer_score * 3.0) + (domain_score * 1.0)
            
            threshold = 5.0
            if footer_score > 0:
                threshold = 4.0
            if any(re.search(pattern, link_text) for pattern in exact_patterns):
                threshold = 4.0
            
            if is_legal_page and href_domain != base_domain:
                threshold += 3.0
            
            if final_score > threshold:
                candidates.append((absolute_url, final_score))
        
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            continue
    
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"Sorted ToS candidates: {candidates}")
        return candidates[0][0]
    
    return None


async def standard_tos_finder(variations_to_try: List[Tuple[str, str]], headers: dict, session: requests.Session) -> TosResponse:
    """
    Try to find ToS link using standard requests + BeautifulSoup method.
    """
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
            
            # Find the ToS link from the page
            logger.info(f"Searching for ToS link in {final_url}")
            tos_link = find_tos_link(final_url, soup)
            
            if tos_link:
                # Additional check for false positives
                if is_likely_false_positive(tos_link, 'tos'):
                    logger.warning(f"Found link {tos_link} appears to be a false positive, skipping")
                    continue
                    
                # Check if this is a correct policy type
                if not is_correct_policy_type(tos_link, 'tos'):
                    logger.warning(f"Found link {tos_link} appears to be a privacy policy, not ToS, skipping")
                    continue
                
                # Ensure the link is absolute
                if tos_link.startswith('/'):
                    parsed_final_url = urlparse(final_url)
                    base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                    tos_link = urljoin(base_url, tos_link)
                    logger.info(f"Converted relative URL to absolute URL: {tos_link}")
                    
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
            continue
        except Exception as e:
            logger.error(f"Exception for {url} ({variation_type}): {str(e)}")
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
                
                if not tos_link:
                    # Try to find and click buttons that might reveal ToS content
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
                    # Additional check for false positives
                    if is_likely_false_positive(tos_link, 'tos'):
                        logger.warning(f"Found link {tos_link} appears to be a false positive, skipping")
                        return TosResponse(
                            url=final_url,
                            success=False,
                            message=f"Found ToS link was a false positive: {tos_link}",
                            method_used="playwright_false_positive"
                        )
                        
                    # Check if this is a correct policy type
                    if not is_correct_policy_type(tos_link, 'tos'):
                        logger.warning(f"Found link {tos_link} appears to be a privacy policy, not ToS")
                        return TosResponse(
                            url=final_url,
                            success=False,
                            message=f"Found link appears to be a privacy policy, not Terms of Service: {tos_link}",
                            method_used="playwright_wrong_policy_type"
                        )
                        
                    # Ensure the link is absolute
                    if tos_link.startswith('/'):
                        parsed_final_url = urlparse(final_url)
                        base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                        tos_link = urljoin(base_url, tos_link)
                        logger.info(f"Converted relative URL to absolute URL: {tos_link}")
                    
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