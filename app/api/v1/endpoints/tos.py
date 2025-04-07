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
    is_correct_policy_type,
    find_policy_link_prioritized
)
import inspect

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
    # Use the prioritized approach to find ToS links
    return find_policy_link_prioritized(url, soup, 'tos')


def verify_tos_link(session: requests.Session, tos_link: str, headers: dict) -> bool:
    """
    Verify that a candidate ToS link actually points to a terms page.
    This function visits the link and checks the page content for terms-related signals.
    """
    try:
        logger.info(f"Verifying candidate ToS link: {tos_link}")
        
        # Check if it's Apple's general terms when we're looking for app-specific terms
        tos_link_lower = tos_link.lower()
        parsed_url = urlparse(tos_link_lower)
        domain = parsed_url.netloc
        
        # Reject Apple's general terms links when verifying app-specific terms
        if domain == "www.apple.com" and any(pattern in parsed_url.path for pattern in [
            "/legal/terms", "/terms-of-service", "/terms"
        ]):
            # Get the caller function name for context-aware decision
            caller_frame = inspect.currentframe().f_back
            if caller_frame and "app_store_" in caller_frame.f_code.co_name:
                logger.warning(f"Rejecting Apple's general terms {tos_link} when looking for app-specific terms")
                return False
                
        # Check if it's obviously a primary ToS URL - these are highest priority
        path = parsed_url.path
        
        # Specific patterns that indicate a non-primary terms page
        non_primary_patterns = [
            'event', 'partner', 'enterprise', 'service-', 'specific',
            'contest', 'promotion', 'sweepstakes', 'marketplace', 'developer',
            'subscription', 'api-', 'affiliate', 'reseller', 'cookie'
        ]
        
        # Specific patterns that strongly indicate a primary terms page
        primary_tos_patterns = [
            '/legal/terms', '/terms-of-service', '/terms-of-use',
            '/tos', '/terms.html', '/legal/terms-of-service'
        ]
        
        is_likely_primary = any(pattern in path for pattern in primary_tos_patterns)
        is_likely_specific = any(pattern in path for pattern in non_primary_patterns)
        
        # Prioritize links that seem to be primary ToS
        if is_likely_primary and not is_likely_specific:
            logger.info(f"Link appears to be a primary ToS URL based on path: {tos_link}")
            # For these high-confidence URLs, we can skip some checks, but still verify content
        
        # Skip obvious non-ToS URLs or tracking params URLs
        if any(pattern in tos_link_lower for pattern in [
            'utm_', 'utm=', 'source=', 'utm_source', 'campaign=', 'medium=', '?ref=', 
            '&ref=', '/blog/', '/news/', '/search', '/index', '/home', '/user', '/account',
            '/profile', '/dashboard', '/features', '/pricing', '/help', '/support',
            '/about', '/contact', '/signin', '/login', '/download', '/products', '/solutions',
        ]):
            logger.warning(f"Rejecting ToS candidate with tracking/navigation params: {tos_link}")
            return False
            
        # Check for query parameters that suggest this is not a ToS
        if parsed_url.query and not any(term in path for term in ['/terms', '/tos', '/legal']):
            query_params = parsed_url.query.lower()
            # If query has params but path doesn't have terms indicators, this is suspicious
            if any(param in query_params for param in ['utm_', 'ref=', 'source=', 'campaign=']):
                logger.warning(f"Rejecting ToS candidate with suspicious query params: {tos_link}")
                return False
        
        # Make an HTTP request to the page
        response = session.get(tos_link, headers=headers, timeout=15)
        if response.status_code != 200:
            logger.warning(f"ToS verification failed: status code {response.status_code} for {tos_link}")
            return False
            
        # Parse the content
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Check page title
        title_elem = soup.find('title')
        if title_elem:
            title_text = title_elem.get_text().lower()
            
            # Higher priority for general ToS pages vs. specific ones
            if any(keyword in title_text for keyword in ['terms of service', 'terms of use', 'terms and conditions']):
                # High confidence for general terms titles
                if not any(specific in title_text for specific in non_primary_patterns):
                    logger.info(f"Verified primary ToS link by title: {tos_link}")
                    return True
                
                logger.info(f"Verified specific ToS link by title: {tos_link}")
                # For specific ToS pages, continue checking to see if there's a better general ToS
            
            # Check for terms-related keywords in title
            if any(keyword in title_text for keyword in ['terms', 'conditions', 'tos', 'legal', 'agreement']):
                logger.info(f"Verified ToS link by title: {tos_link}")
                # For primary URLs, we can be more confident
                if is_likely_primary:
                    return True
                # Continue with other checks for non-primary URLs
                
            # Reject pages with non-terms titles
            if any(keyword in title_text for keyword in ['learn', 'tutorial', 'course', 'guide', 'start', 'docs']):
                logger.warning(f"Rejecting ToS candidate with educational title: '{title_text}'")
                return False
        
        # Check for primary ToS content indicators
        h1_elements = soup.find_all('h1')
        h1_texts = [h.get_text().lower() for h in h1_elements]
        
        # Strong indicators of a primary ToS document
        primary_tos_indicators = [
            'terms of service', 'terms of use', 'terms and conditions', 
            'user agreement', 'service agreement'
        ]
        
        # Check h1 elements first - these are most reliable
        for h1 in h1_texts:
            if any(indicator in h1 for indicator in primary_tos_indicators):
                logger.info(f"Verified primary ToS link by h1: {tos_link}")
                return True
        
        # Check h2 elements next
        h2_elements = soup.find_all('h2')
        h2_texts = [h.get_text().lower() for h in h2_elements]
        
        for h2 in h2_texts:
            if any(indicator in h2 for indicator in primary_tos_indicators):
                logger.info(f"Verified primary ToS link by h2: {tos_link}")
                return True
        
        # Check for terms-related paragraphs
        paragraphs = soup.find_all('p')
        para_texts = [p.get_text().lower() for p in paragraphs]
        
        terms_patterns = [
            r'\bterms\s+of\s+service\b', 
            r'\bterms\s+of\s+use\b',
            r'\bterms\s+and\s+conditions\b',
            r'\bagreement\b',
            r'\blegal\s+terms\b'
        ]
        
        tos_paragraph_count = 0
        # Check first few paragraphs for terms content
        for para in para_texts[:10]:  # Check first 10 paragraphs
            if any(re.search(pattern, para) for pattern in terms_patterns):
                tos_paragraph_count += 1
                
        if tos_paragraph_count >= 2:
            # If multiple paragraphs contain terms language, it's likely a ToS page
            logger.info(f"Verified ToS link by multiple paragraph content: {tos_link}")
            return True
        elif tos_paragraph_count == 1 and is_likely_primary:
            # For URLs that look like primary ToS from the path, one paragraph is enough
            logger.info(f"Verified primary ToS link by path and paragraph content: {tos_link}")
            return True
            
        # If we've reached this point, check if this is a known primary ToS URL pattern
        if is_likely_primary and not is_likely_specific:
            # For these high-confidence URLs, be more lenient
            logger.info(f"Accepting likely primary ToS URL based on path pattern: {tos_link}")
            return True
                
        # If we've reached this point, we couldn't positively verify this as a ToS page
        logger.warning(f"Could not verify {tos_link} as a ToS page")
        return False
        
    except Exception as e:
        logger.error(f"Error verifying ToS link {tos_link}: {str(e)}")
        return False


async def standard_tos_finder(variations_to_try: List[Tuple[str, str]], headers: dict, session: requests.Session) -> TosResponse:
    """
    Standard approach using requests and BeautifulSoup.
    Prioritizes scanning for ToS links in a specific order:
    1. By class/ID patterns
    2. In footer elements
    3. In header elements
    4. In all links with policy-related text
    """
    
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
                
                # Determine method used for more informative response
                method_used = "standard"
                if "footer" in inspect.currentframe().f_back.f_locals.get('method_info', ''):
                    method_used = "standard_footer"
                elif "header" in inspect.currentframe().f_back.f_locals.get('method_info', ''):
                    method_used = "standard_header"
                elif "class/ID" in inspect.currentframe().f_back.f_locals.get('method_info', ''):
                    method_used = "standard_class_id"
                
                return TosResponse(
                    url=final_url,  # Return the actual URL after redirects
                    tos_url=tos_link,
                    success=True,
                    message=f"Terms of Service link found on final destination page: {final_url}" + 
                            (f" (Found at {variation_type})" if variation_type != "original exact url" else ""),
                    method_used=method_used
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
    
    # If we get here, we tried all variations but didn't find a ToS link
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


def detect_site_platform(soup: BeautifulSoup, url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Detect what platform/framework a site is using by examining the HTML.
    Returns a tuple of (platform_name, platform_domain) if detected, or (None, None) if not.
    """
    try:
        html_content = str(soup)
        
        # Look for common platform indicators in the HTML
        platforms = [
            # (platform name, detection string, legal domain)
            ("Vercel", "vercel.com", "vercel.com"),
            ("Netlify", "netlify.app", "netlify.com"),
            ("Wix", "wix.com", "wix.com"),
            ("Shopify", "shopify.com", "shopify.com"),
            ("WordPress", "wp-content", "wordpress.com"),
            ("Squarespace", "squarespace.com", "squarespace.com"),
            ("GitHub Pages", "github.io", "github.com"),
            ("Webflow", "webflow.com", "webflow.com"),
            ("Cloudflare Pages", "pages.dev", "cloudflare.com"),
            ("Firebase", "firebaseapp.com", "firebase.google.com"),
            ("AWS Amplify", "amplifyapp.com", "aws.amazon.com"),
            ("Heroku", "herokuapp.com", "heroku.com"),
        ]
        
        # Check for platform indicators in the HTML
        for platform_name, detection_string, platform_domain in platforms:
            if detection_string in html_content:
                logger.info(f"Detected platform: {platform_name} based on HTML content")
                return platform_name, platform_domain
        
        # Check for platform-specific meta tags
        generator_tag = soup.find('meta', {'name': 'generator'})
        if generator_tag and generator_tag.get('content'):
            generator_content = generator_tag.get('content').lower()
            
            if 'wordpress' in generator_content:
                return "WordPress", "wordpress.com"
            elif 'wix' in generator_content:
                return "Wix", "wix.com"
            elif 'shopify' in generator_content:
                return "Shopify", "shopify.com"
            elif 'squarespace' in generator_content:
                return "Squarespace", "squarespace.com"
            elif 'webflow' in generator_content:
                return "Webflow", "webflow.com"
            
        # Check for platform in URL
        url_lower = url.lower()
        for platform_name, detection_string, platform_domain in platforms:
            if detection_string in url_lower:
                logger.info(f"Detected platform: {platform_name} based on URL")
                return platform_name, platform_domain
                
        return None, None
    except Exception as e:
        logger.error(f"Error detecting platform: {str(e)}")
        return None, None


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

    # Check if this is an App Store URL
    is_app_store = False
    is_play_store = False
    parsed_url = urlparse(original_url)
    
    if 'apps.apple.com' in parsed_url.netloc or 'itunes.apple.com' in parsed_url.netloc:
        logger.info(f"Detected App Store URL: {original_url}")
        is_app_store = True
        # Handle App Store URL differently
        app_store_result = await handle_app_store_tos(original_url, headers)
        
        # For App Store URLs, we don't fall back to standard approach
        # Instead, if the app-specific handler fails, we return not found
        if not app_store_result.success:
            logger.warning(f"No app-specific Terms of Service found for App Store URL: {original_url}")
            response.status_code = 404
        
        return app_store_result
    
    if 'play.google.com/store/apps' in original_url:
        logger.info(f"Detected Google Play Store URL: {original_url}")
        is_play_store = True
        # Handle Play Store URL differently
        play_store_result = await handle_play_store_tos(original_url, headers)
        if play_store_result.success:
            return play_store_result
        # If special handling fails, fall back to standard approach

    session = requests.Session()
    variations_with_types = [(original_url, "original exact url")]
    
    variations = prepare_url_variations(original_url)
    for idx, var_url in enumerate(variations[1:], 1):
        variations_with_types.append((var_url, f"variation_{idx}"))
    
    logger.info(f"URL variations to try: {variations_with_types}")
    
    # New approach: Try Playwright first for ALL sites to ensure consistent handling
    # This helps with sites like Scribbr that need full JavaScript rendering
    logger.info(f"Starting with Playwright for better detection on all sites")
    playwright_result = await playwright_tos_finder(original_url)
    if playwright_result.success:
        logger.info(f"Found ToS link with Playwright first attempt: {playwright_result.tos_url}")
        return playwright_result
    
    # If Playwright fails, try standard method as fallback
    logger.info(f"Playwright didn't find ToS, trying standard method")
    standard_result = await standard_tos_finder(variations_with_types, headers, session)
    if standard_result.success:
        logger.info(f"Found ToS link with standard method: {standard_result.tos_url}")
        return standard_result
    
    logger.info(f"No ToS link found for {original_url} with any method")
    response.status_code = 404
    return TosResponse(
        url=original_url,
        success=False,
        message="No Terms of Service link found. Tried both JavaScript-enabled browser rendering and standard scraping.",
        method_used="both_failed"
    )


async def handle_app_store_tos(url: str, headers: dict) -> TosResponse:
    """
    Special handling for App Store URLs - first get the privacy policy link,
    then try to find the ToS link on the same domain as the privacy policy.
    """
    try:
        logger.info(f"Using specialized App Store ToS handling for: {url}")
        
        # First, try to get the app name for better logging
        session = requests.Session()
        app_name = None
        app_id = None
        
        # Parse URL to get app ID
        parsed_url = urlparse(url)
        if parsed_url.path:
            id_match = re.search(r'/id(\d+)', parsed_url.path)
            if id_match:
                app_id = id_match.group(1)
                
        try:
            response = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for app name
            title_elem = soup.find('title')
            if title_elem:
                app_name = title_elem.text.strip().split('-')[0].strip()
            
            if not app_name:
                h1_elem = soup.find('h1')
                if h1_elem:
                    app_name = h1_elem.text.strip()
        except Exception as e:
            logger.error(f"Error extracting app name: {str(e)}")
            
        app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}' if app_id else ''}"
        
        # Step 1: Try to find the privacy policy of the app and derive ToS from there
        logger.info(f"Looking for privacy policy link to derive ToS link for {app_info}")
        
        # Import here to avoid circular imports
        from .privacy import find_privacy_link
        
        # First, we find the privacy policy of the app
        try:
            response = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for privacy policy links
            privacy_link = find_privacy_link(url, soup)
            
            if privacy_link:
                logger.info(f"Found privacy policy link for App Store item: {privacy_link}")
                
                # Make sure the link is absolute
                if privacy_link.startswith('/'):
                    privacy_link = urljoin(url, privacy_link)
                
                # Get the base domain of the privacy policy
                pp_parsed = urlparse(privacy_link)
                pp_base_domain = f"{pp_parsed.scheme}://{pp_parsed.netloc}"
                logger.info(f"Extracted base domain from privacy policy: {pp_base_domain}")
                
                # New Step: Try common ToS paths directly on the privacy policy domain first
                # This addresses the issue where we find privacy policy at controlgame.com/privacy/ 
                # and want to directly check controlgame.com/terms without checking the privacy page first
                logger.info(f"Trying common ToS paths directly on privacy policy domain: {pp_base_domain}")
                
                # Extract privacy path components to create matching terms paths
                pp_path = pp_parsed.path
                logger.info(f"Privacy policy path: {pp_path}")
                
                # If the privacy URL contains specific patterns, try corresponding terms patterns
                specific_candidates = []
                
                if "/privacy" in pp_path:
                    # If we have /privacy, try /terms
                    terms_path = pp_path.replace("/privacy", "/terms")
                    specific_candidates.append(terms_path)
                    
                if "/privacy-policy" in pp_path:
                    # If we have /privacy-policy, try /terms-of-service, /terms-of-use, etc.
                    specific_candidates.append(pp_path.replace("/privacy-policy", "/terms-of-service"))
                    specific_candidates.append(pp_path.replace("/privacy-policy", "/terms-of-use"))
                    specific_candidates.append(pp_path.replace("/privacy-policy", "/terms-and-conditions"))
                    
                # Regular common paths
                common_tos_paths = [
                    "/terms", "/tos", "/terms-of-service", "/terms-of-use", 
                    "/terms-and-conditions", "/legal/terms", "/legal", 
                    "/terms.html", "/legal/terms.html", "/eula"
                ]
                
                # Try specific domain-based candidates first
                for path in specific_candidates:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(f"Checking candidate ToS URL based on privacy path: {candidate_tos_url}")
                        
                        # Skip if this is Apple's domain - we want app-specific terms only
                        candidate_parsed = urlparse(candidate_tos_url)
                        if candidate_parsed.netloc == "www.apple.com":
                            logger.warning(f"Skipping Apple's domain for candidate ToS URL: {candidate_tos_url} - we only want app-specific terms")
                            continue

                        tos_check_response = session.get(candidate_tos_url, headers=headers, timeout=15)
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                # Extra check: don't return Apple's general terms
                                if "apple.com/legal/terms" in candidate_tos_url:
                                    logger.warning(f"Rejecting Apple's general terms: {candidate_tos_url}")
                                    continue
                                
                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found at path matching privacy policy path for {app_info}",
                                    method_used="app_store_pp_matching_path"
                                )
                    except Exception as e:
                        logger.error(f"Error checking specific ToS path {path}: {str(e)}")
                
                # Then try common paths
                for path in common_tos_paths:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(f"Checking candidate ToS URL directly: {candidate_tos_url}")
                        
                        # Skip if this is Apple's domain - we want app-specific terms only
                        candidate_parsed = urlparse(candidate_tos_url)
                        if candidate_parsed.netloc == "www.apple.com":
                            logger.warning(f"Skipping Apple's domain for candidate ToS URL: {candidate_tos_url} - we only want app-specific terms")
                            continue
                            
                        tos_check_response = session.get(candidate_tos_url, headers=headers, timeout=15)
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                # Extra check: don't return Apple's general terms
                                if "apple.com/legal/terms" in candidate_tos_url:
                                    logger.warning(f"Rejecting Apple's general terms: {candidate_tos_url}")
                                    continue
                                
                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found directly on privacy policy domain for {app_info}",
                                    method_used="app_store_pp_domain_direct"
                                )
                    except Exception as e:
                        logger.error(f"Error checking ToS path {path}: {str(e)}")
                
                # If direct domain approach failed, try to get the ToS from the privacy page
                try:
                    # First try to visit the privacy page to find ToS links
                    pp_response = session.get(privacy_link, headers=headers, timeout=15)
                    pp_soup = BeautifulSoup(pp_response.text, 'html.parser')
                    
                    # Search for ToS links on the privacy page
                    tos_from_pp = find_tos_link(privacy_link, pp_soup)
                    
                    if tos_from_pp:
                        # Make it absolute if needed
                        if tos_from_pp.startswith('/'):
                            tos_from_pp = urljoin(privacy_link, tos_from_pp)
                        
                        # Skip if this is Apple's domain - we want app-specific terms only
                        tos_parsed = urlparse(tos_from_pp)
                        if tos_parsed.netloc == "www.apple.com":
                            logger.warning(f"Skipping Apple's domain for ToS URL found on privacy page: {tos_from_pp} - we only want app-specific terms")
                            # We don't return anything here, let the function continue to check other methods
                        else:
                            # Extra check: don't return Apple's general terms
                            if "apple.com/legal/terms" in tos_from_pp:
                                logger.warning(f"Rejecting Apple's general terms: {tos_from_pp}")
                            else:
                                # Verify this is actually a ToS link
                                if verify_tos_link(session, tos_from_pp, headers):
                                    return TosResponse(
                                        url=url,
                                        tos_url=tos_from_pp,
                                        success=True,
                                        message=f"Terms of Service found via app's privacy policy page for {app_info}",
                                        method_used="app_store_pp_to_tos"
                                    )
                
                except Exception as e:
                    logger.error(f"Error fetching privacy page: {str(e)}")
        except Exception as e:
            logger.error(f"Error in App Store ToS detection: {str(e)}")
            
        # If we get here and haven't found app-specific terms, return failure
        logger.warning(f"No app-specific Terms of Service found for {app_info}")
        return TosResponse(
            url=url,
            success=False,
            message=f"No app-specific Terms of Service found for {app_info}. Apple's general terms will not be used as a substitute.",
            method_used="app_store_no_specific_terms"
        )
            
    except Exception as e:
        logger.error(f"Error in App Store ToS handler: {str(e)}")
        return TosResponse(
            url=url,
            success=False,
            message=f"Error handling App Store URL for ToS: {str(e)}",
            method_used="app_store_failed"
        )


async def handle_play_store_tos(url: str, headers: dict) -> TosResponse:
    """
    Special handling for Google Play Store URLs - first get the privacy policy link,
    then try to find the ToS link on the same domain as the privacy policy.
    """
    try:
        logger.info(f"Using specialized Play Store ToS handling for: {url}")
        
        # First, try to get the app name and ID for better logging
        session = requests.Session()
        app_name = None
        app_id = None
        
        # Parse URL to get app ID
        parsed_url = urlparse(url)
        query_params = parsed_url.query
        query_dict = {param.split('=')[0]: param.split('=')[1] for param in query_params.split('&') if '=' in param}
        app_id = query_dict.get('id')
                
        try:
            response = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Look for app name
            title_elem = soup.find('title')
            if title_elem:
                app_name = title_elem.text.strip().split('-')[0].strip()
            
            if not app_name:
                h1_elem = soup.find('h1')
                if h1_elem:
                    app_name = h1_elem.text.strip()
        except Exception as e:
            logger.error(f"Error extracting app name: {str(e)}")
            
        app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}' if app_id else ''}"
        
        # Step 1: Try to find the privacy policy link and use that to locate ToS
        logger.info(f"Looking for privacy policy link to derive ToS link for {app_info}")
        
        # Import here to avoid circular imports
        from .privacy import find_privacy_link
        
        # First, we try to see if there's an app data safety page
        data_safety_url = url
        if app_id:
            data_safety_url = f"https://play.google.com/store/apps/datasafety?id={app_id}"
            
        try:
            data_safety_response = session.get(data_safety_url, headers=headers, timeout=15)
            data_safety_soup = BeautifulSoup(data_safety_response.text, 'html.parser')
            privacy_link = find_privacy_link(data_safety_url, data_safety_soup)
            
            if not privacy_link:
                # Try the main app page
                response = session.get(url, headers=headers, timeout=15)
                soup = BeautifulSoup(response.text, 'html.parser')
                privacy_link = find_privacy_link(url, soup)
            
            if privacy_link:
                logger.info(f"Found privacy policy link for Play Store item: {privacy_link}")
                
                # Make sure the link is absolute
                if privacy_link.startswith('/'):
                    privacy_link = urljoin(url, privacy_link)
                
                # Get the base domain of the privacy policy
                pp_parsed = urlparse(privacy_link)
                pp_base_domain = f"{pp_parsed.scheme}://{pp_parsed.netloc}"
                logger.info(f"Extracted base domain from privacy policy: {pp_base_domain}")
                
                # New Step: Try common ToS paths directly on the privacy policy domain first
                # This addresses the issue where we find privacy policy at example.com/privacy/ 
                # and want to directly check example.com/terms without visiting the privacy page first
                logger.info(f"Trying common ToS paths directly on privacy policy domain: {pp_base_domain}")
                
                # Extract privacy path components to create matching terms paths
                pp_path = pp_parsed.path
                logger.info(f"Privacy policy path: {pp_path}")
                
                # If the privacy URL contains specific patterns, try corresponding terms patterns
                specific_candidates = []
                
                if "/privacy" in pp_path:
                    # If we have /privacy, try /terms
                    terms_path = pp_path.replace("/privacy", "/terms")
                    specific_candidates.append(terms_path)
                    
                if "/privacy-policy" in pp_path:
                    # If we have /privacy-policy, try /terms-of-service, /terms-of-use, etc.
                    specific_candidates.append(pp_path.replace("/privacy-policy", "/terms-of-service"))
                    specific_candidates.append(pp_path.replace("/privacy-policy", "/terms-of-use"))
                    specific_candidates.append(pp_path.replace("/privacy-policy", "/terms-and-conditions"))
                    
                # Regular common paths
                common_tos_paths = [
                    "/terms", "/tos", "/terms-of-service", "/terms-of-use", 
                    "/terms-and-conditions", "/legal/terms", "/legal", 
                    "/terms.html", "/legal/terms.html", "/eula"
                ]
                
                # Try specific domain-based candidates first
                for path in specific_candidates:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(f"Checking candidate ToS URL based on privacy path: {candidate_tos_url}")
                        
                        tos_check_response = session.get(candidate_tos_url, headers=headers, timeout=15)
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found at path matching privacy policy path for {app_info}",
                                    method_used="play_store_pp_matching_path"
                                )
                    except Exception as e:
                        logger.error(f"Error checking specific ToS path {path}: {str(e)}")
                
                # Then try common paths
                for path in common_tos_paths:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(f"Checking candidate ToS URL directly: {candidate_tos_url}")
                        
                        tos_check_response = session.get(candidate_tos_url, headers=headers, timeout=15)
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found directly on privacy policy domain for {app_info}",
                                    method_used="play_store_pp_domain_direct"
                                )
                    except Exception as e:
                        logger.error(f"Error checking ToS path {path}: {str(e)}")
                
                # If direct domain approach failed, try to get the ToS from the privacy page
                try:
                    # First try to visit the privacy page to find ToS links
                    pp_response = session.get(privacy_link, headers=headers, timeout=15)
                    pp_soup = BeautifulSoup(pp_response.text, 'html.parser')
                    
                    # Search for ToS links on the privacy page
                    tos_from_pp = find_tos_link(privacy_link, pp_soup)
                    
                    if tos_from_pp:
                        # Make it absolute if needed
                        if tos_from_pp.startswith('/'):
                            tos_from_pp = urljoin(privacy_link, tos_from_pp)
                        
                        # Skip if this is Apple's domain - we want app-specific terms only
                        tos_parsed = urlparse(tos_from_pp)
                        if tos_parsed.netloc == "www.apple.com":
                            logger.warning(f"Skipping Apple's domain for ToS URL found on privacy page: {tos_from_pp} - we only want app-specific terms")
                            # We don't return anything here, let the function continue to check other methods
                        else:
                            # Extra check: don't return Apple's general terms
                            if "apple.com/legal/terms" in tos_from_pp:
                                logger.warning(f"Rejecting Apple's general terms: {tos_from_pp}")
                            else:
                                # Verify this is actually a ToS link
                                if verify_tos_link(session, tos_from_pp, headers):
                                    return TosResponse(
                                        url=url,
                                        tos_url=tos_from_pp,
                                        success=True,
                                        message=f"Terms of Service found via app's privacy policy page for {app_info}",
                                        method_used="play_store_pp_to_tos"
                                    )
                except Exception as e:
                    logger.error(f"Error fetching privacy page: {str(e)}")
            
        except Exception as e:
            logger.error(f"Error in Play Store ToS detection: {str(e)}")
            
        # Step 2: If we couldn't find developer ToS through the privacy policy, try Google's standard ToS
        logger.info(f"Developer-specific ToS not found, trying Google's standard ToS for {app_info}")
        google_standard_tos_url = "https://play.google.com/about/play-terms/index.html"
        
        # Verify that Google's ToS URL is valid
        try:
            tos_response = session.get(google_standard_tos_url, headers=headers, timeout=15)
            if tos_response.status_code == 200:
                # Verify this is actually a ToS page
                if verify_tos_link(session, google_standard_tos_url, headers):
                    logger.info(f"Verified Google standard ToS link: {google_standard_tos_url}")
                    return TosResponse(
                        url=url,
                        tos_url=google_standard_tos_url,
                        success=True,
                        message=f"Play Store standard Terms of Service found for {app_info}",
                        method_used="play_store_standard_tos"
                    )
        except Exception as e:
            logger.error(f"Error checking Google standard ToS URL: {str(e)}")
        
        # Step 3: Try to find alternative Google ToS URLs
        google_alternative_tos_urls = [
            "https://policies.google.com/terms",
            "https://www.google.com/policies/terms/",
            "https://play.google.com/intl/en-us_us/about/play-terms.html",
        ]
        
        for google_tos_url in google_alternative_tos_urls:
            try:
                tos_response = session.get(google_tos_url, headers=headers, timeout=15)
                if tos_response.status_code == 200:
                    # Verify this is actually a ToS page
                    if verify_tos_link(session, google_tos_url, headers):
                        logger.info(f"Verified Google alternative ToS link: {google_tos_url}")
                        return TosResponse(
                            url=url,
                            tos_url=google_tos_url,
                            success=True,
                            message=f"Play Store alternative Terms of Service found for {app_info}",
                            method_used="play_store_alternative_tos"
                        )
            except Exception as e:
                logger.error(f"Error checking Google alternative ToS URL {google_tos_url}: {str(e)}")
        
        # If we couldn't find developer ToS, fall back to Google's ToS
        logger.info(f"Falling back to Google's general ToS for {app_info}")
        return TosResponse(
            url=url,
            tos_url="https://policies.google.com/terms",
            success=True,
            message=f"Fallback to Google's general Terms of Service for {app_info} - no developer-specific ToS found",
            method_used="play_store_google_fallback"
        )
            
    except Exception as e:
        logger.error(f"Error in Play Store ToS handler: {str(e)}")
        return TosResponse(
            url=url,
            success=False,
            message=f"Error handling Play Store URL for ToS: {str(e)}",
            method_used="play_store_failed"
        )


async def playwright_tos_finder(url: str) -> TosResponse:
    """
    Find Terms of Service links using Playwright for JavaScript-rendered content.
    This function will:
    1. Render the page with JavaScript
    2. Look for ToS links in headers, footers and general content
    3. Score and verify candidates by following links and checking content
    4. Return the best verified ToS link
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
                logger.info(f"Loading page with Playwright: {url}")
                await page.goto(url, wait_until="networkidle", timeout=60000)
                final_url = page.url
                logger.info(f"Page loaded: {final_url}")

                # Try to handle cookie consent or popup banners that might be blocking content
                try:
                    consent_buttons = await page.query_selector_all('button:text-matches("(accept|agree|got it|cookie|consent|allow)", "i")')
                    for button in consent_buttons:
                        try:
                            await button.click()
                            await page.wait_for_timeout(1000)
                        except Exception as e:
                            logger.debug(f"Could not click consent button: {str(e)}")
                except Exception as e:
                    logger.debug(f"Error handling consent buttons: {str(e)}")
                
                # List to store all potential ToS candidates for comparison
                tos_candidates = []
                
                # First check: scan the initial page content for ToS links
                logger.info("Scanning initial page content for ToS links")
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                initial_tos_link = find_tos_link(final_url, soup)
                
                if initial_tos_link:
                    # Calculate a confidence score for this link
                    if not is_likely_false_positive(initial_tos_link, 'tos'):
                        # Get the link text for scoring
                        link_text = ""
                        for link in soup.find_all('a', href=True):
                            href = link.get('href', '').strip()
                            if href and urljoin(final_url, href) == initial_tos_link:
                                link_text = link.get_text().strip()
                                break
                                
                        score = get_policy_score(link_text, initial_tos_link, 'tos')
                        tos_candidates.append((initial_tos_link, score, "initial_scan"))
                        logger.info(f"Found initial ToS candidate: {initial_tos_link} with score {score}")
                
                # Second check: scroll to the bottom of the page to reveal footer content
                logger.info("Scrolling to the bottom of the page to reveal potential footer links")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)  # Wait for any lazy-loaded content
                
                # Get updated content after scrolling
                content_after_scroll = await page.content()
                soup_after_scroll = BeautifulSoup(content_after_scroll, 'html.parser')
                scroll_tos_link = find_tos_link(final_url, soup_after_scroll)
                
                if scroll_tos_link and scroll_tos_link != initial_tos_link:
                    # Calculate a confidence score for this link
                    if not is_likely_false_positive(scroll_tos_link, 'tos'):
                        # Get the link text for scoring
                        link_text = ""
                        for link in soup_after_scroll.find_all('a', href=True):
                            href = link.get('href', '').strip()
                            if href and urljoin(final_url, href) == scroll_tos_link:
                                link_text = link.get_text().strip()
                                break
                                
                        # Give a small boost to footer links
                        score = get_policy_score(link_text, scroll_tos_link, 'tos') + 5.0
                        tos_candidates.append((scroll_tos_link, score, "scroll_scan"))
                        logger.info(f"Found additional ToS candidate after scrolling: {scroll_tos_link} with score {score}")
                
                # Look for potential ToS-related links directly
                logger.info("Looking for potential ToS-related links")
                
                # Define patterns for ToS detection
                potential_tos_indicators = [
                    "terms", "tos", "conditions", "legal", "agreement", "general terms"
                ]
                
                # First locate links in footer areas - these are more likely to contain ToS
                footer_selectors = [
                    'footer', 
                    'div[class*="footer"]', 'div[id*="footer"]',
                    'div[class*="bottom"]', 'div[id*="bottom"]',
                    'div[class*="legal"]', 'div[id*="legal"]'
                ]
                
                # Try each footer selector
                for selector in footer_selectors:
                    footer_elements = await page.query_selector_all(selector)
                    for footer in footer_elements:
                        footer_links = await footer.query_selector_all('a')
                        for link in footer_links:
                            try:
                                # Get the text and href of the link
                                link_text = await link.text_content()
                                href = await link.get_attribute('href')
                                
                                if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                                    continue

                                link_text_lower = link_text.lower().strip()
                                
                                # Check if the link might be a ToS link
                                if any(indicator in link_text_lower for indicator in potential_tos_indicators) and not any(
                                    non_tos in link_text_lower for non_tos in ['community', 'copyright', 'guidelines', 'citation']
                                ):
                                    try:
                                        absolute_url = urljoin(final_url, href)
                                        
                                        # Skip if already in our candidates list
                                        if any(candidate[0] == absolute_url for candidate in tos_candidates):
                                            continue

                                        # Check if this is likely to be a real ToS link
                                        if is_likely_false_positive(absolute_url, 'tos'):
                                            continue
                                            
                                        # Calculate score based on text and URL
                                        score = get_policy_score(link_text, absolute_url, 'tos') + 10.0  # Boost for footer links
                                        tos_candidates.append((absolute_url, score, "footer_link"))
                                        logger.info(f"Found potential ToS link in footer: {link_text} ({absolute_url}) with score {score}")
                                    except Exception as e:
                                        logger.debug(f"Error processing footer link {href}: {str(e)}")
                            except Exception as e:
                                logger.debug(f"Error with footer link: {str(e)}")

                # Get all links on the page as a fallback
                all_links = await page.query_selector_all('a')
                
                for link in all_links:
                    try:
                        # Get the text and href of the link
                        link_text = await link.text_content()
                        href = await link.get_attribute('href')
                        
                        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                            continue
                        
                        link_text_lower = link_text.lower().strip()
                        absolute_url = urljoin(final_url, href)
                        
                        # Skip if already in our candidates list
                        if any(candidate[0] == absolute_url for candidate in tos_candidates):
                            continue

                        # Check if the link might be a ToS link - use more strict criteria for general links
                        if any(indicator == link_text_lower.strip() for indicator in ["terms", "terms of service", "terms of use"]):
                            if not is_likely_false_positive(absolute_url, 'tos'):
                                # Higher score for exact matches
                                score = get_policy_score(link_text, absolute_url, 'tos') + 15.0
                                tos_candidates.append((absolute_url, score, "exact_match"))
                                logger.info(f"Found exact ToS link match: {link_text} ({absolute_url}) with score {score}")
                                
                        # For links with terms mentioned but not exact match, check with lower score
                        elif ('terms' in link_text_lower or 'tos' in link_text_lower) and not any(
                            non_tos in link_text_lower for non_tos in ['community', 'copyright', 'guidelines']
                        ):
                            if not is_likely_false_positive(absolute_url, 'tos'):
                                score = get_policy_score(link_text, absolute_url, 'tos')
                                tos_candidates.append((absolute_url, score, "potential_match"))
                                logger.info(f"Found potential ToS link: {link_text} ({absolute_url}) with score {score}")
                    except Exception as e:
                        logger.debug(f"Error processing link: {str(e)}")
                
                # If we have candidates, sort by score and verify them
                if tos_candidates:
                    tos_candidates.sort(key=lambda x: x[1], reverse=True)
                    logger.info(f"Found {len(tos_candidates)} ToS candidates, best is: {tos_candidates[0][0]} with score {tos_candidates[0][1]}")
                    
                    # Verify content of top candidates by following links
                    verified_candidates = []
                    
                    # Try to verify the top 3 candidates (or fewer if we don't have that many)
                    for i in range(min(3, len(tos_candidates))):
                        candidate_url = tos_candidates[i][0]
                        candidate_score = tos_candidates[i][1]
                        method_info = tos_candidates[i][2]
                        
                        logger.info(f"Verifying candidate {i+1}: {candidate_url}")
                        
                        try:
                            # Create a new page to visit the candidate URL
                            verification_page = await context.new_page()
                            await verification_page.goto(candidate_url, wait_until="networkidle", timeout=30000)
                            
                            # Get the page content
                            verify_content = await verification_page.content()
                            verify_soup = BeautifulSoup(verify_content, 'html.parser')
                            
                            # Check page content for ToS indicators
                            title_elem = verify_soup.find('title')
                            h1_elems = verify_soup.find_all('h1')
                            h2_elems = verify_soup.find_all('h2')
                            paragraphs = verify_soup.find_all('p')
                            
                            title_text = title_elem.get_text().lower() if title_elem else ""
                            h1_texts = [h.get_text().lower() for h in h1_elems]
                            h2_texts = [h.get_text().lower() for h in h2_elems]
                            para_texts = [p.get_text().lower() for p in paragraphs[:10]]  # Check first 10 paragraphs
                            
                            # Define strong indicators of ToS content
                            tos_indicators = [
                                "terms of service", "terms of use", "terms and conditions",
                                "user agreement", "legal terms", "conditions of use"
                            ]
                            
                            # Define non-ToS indicators that suggest a false positive
                            non_tos_indicators = [
                                "community guidelines", "copyright", "citation", 
                                "guidelines", "trademark"
                            ]
                            
                            # Calculate a verification score based on page content
                            verification_score = 0
                            
                            # Check title
                            if any(indicator in title_text for indicator in tos_indicators):
                                verification_score += 30
                                logger.info(f"Found ToS indicator in title: {title_text}")
                            elif any(indicator in title_text for indicator in non_tos_indicators):
                                verification_score -= 20
                                logger.info(f"Found non-ToS indicator in title: {title_text}")
                                
                            # Check h1 elements (strongest signal)
                            for h1 in h1_texts:
                                if any(indicator in h1 for indicator in tos_indicators):
                                    verification_score += 50
                                    logger.info(f"Found ToS indicator in h1: {h1}")
                                    break
                                elif any(indicator in h1 for indicator in non_tos_indicators):
                                    verification_score -= 30
                                    logger.info(f"Found non-ToS indicator in h1: {h1}")
                                    
                            # Check h2 elements
                            for h2 in h2_texts:
                                if any(indicator in h2 for indicator in tos_indicators):
                                    verification_score += 30
                                    logger.info(f"Found ToS indicator in h2: {h2}")
                                    break
                                elif any(indicator in h2 for indicator in non_tos_indicators):
                                    verification_score -= 20
                                    logger.info(f"Found non-ToS indicator in h2: {h2}")
                            
                            # Check paragraphs
                            tos_paragraph_count = 0
                            for para in para_texts:
                                if any(indicator in para for indicator in tos_indicators):
                                    tos_paragraph_count += 1
                            
                            if tos_paragraph_count >= 2:
                                verification_score += 40
                                logger.info(f"Found multiple ToS paragraphs: {tos_paragraph_count}")
                            elif tos_paragraph_count == 1:
                                verification_score += 20
                                logger.info(f"Found one ToS paragraph")
                                
                            # Check URL patterns
                            url_lower = candidate_url.lower()
                            if any(pattern in url_lower for pattern in ['/terms', '/tos', 'terms-of-service', 'terms-of-use']):
                                verification_score += 25
                                logger.info(f"URL pattern indicates ToS: {url_lower}")
                                
                            # Final decision
                            logger.info(f"Verification score for {candidate_url}: {verification_score}")
                            if verification_score >= 30:  # Threshold for acceptance
                                # Combine original score with verification score
                                final_score = candidate_score + (verification_score / 10)  # Scale verification score
                                verified_candidates.append((candidate_url, final_score, method_info))
                                logger.info(f"Verified {candidate_url} as ToS with final score {final_score}")
                            else:
                                logger.info(f"Rejected {candidate_url} as ToS with low verification score {verification_score}")
                                
                            # Close the verification page
                            await verification_page.close()
                            
                        except Exception as e:
                            logger.warning(f"Error verifying candidate {candidate_url}: {str(e)}")
                            try:
                                await verification_page.close()
                            except:
                                pass
                    
                    # After verification, choose the best verified candidate
                    if verified_candidates:
                        verified_candidates.sort(key=lambda x: x[1], reverse=True)
                        best_candidate = verified_candidates[0][0]
                        method_info = verified_candidates[0][2]
                        logger.info(f"Best verified ToS link: {best_candidate}")
                        
                        # Ensure the link is absolute
                        if best_candidate.startswith('/'):
                            parsed_final_url = urlparse(final_url)
                            base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                            best_candidate = urljoin(base_url, best_candidate)
                            logger.info(f"Converted relative URL to absolute URL: {best_candidate}")
                        
                        await browser.close()
                        return TosResponse(
                            url=final_url,
                            tos_url=best_candidate,
                            success=True,
                            message=f"Terms of Service link found and verified using JavaScript-enabled browser rendering.",
                            method_used=f"playwright_verified_{method_info}"
                        )
                    else:
                        # If no candidates were verified, fall back to the highest scoring candidate
                        tos_link = tos_candidates[0][0]
                        method_info = tos_candidates[0][2]
                        
                        # Ensure the link is absolute
                        if tos_link.startswith('/'):
                            parsed_final_url = urlparse(final_url)
                            base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                            tos_link = urljoin(base_url, tos_link)
                            logger.info(f"Converted relative URL to absolute URL: {tos_link}")
                        
                        await browser.close()
                        return TosResponse(
                            url=final_url,
                            tos_url=tos_link,
                            success=True,
                            message=f"Terms of Service link found using JavaScript-enabled browser rendering (not verified).",
                            method_used=f"playwright_{method_info}"
                        )
                else:
                    logger.info(f"No ToS candidates found for {url}")
                    await browser.close()
                    return TosResponse(
                        url=final_url,
                        success=False,
                        message=f"No Terms of Service link found even with JavaScript-enabled browser rendering.",
                        method_used="playwright_failed"
                    )
            except Exception as e:
                try:
                    await browser.close()
                except:
                    pass
                
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