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
    
    # Additional patterns to filter out false positives
    false_positive_patterns = [
        '/learn', '/tutorial', '/course', '/education', '/docs', 
        '/documentation', '/guide', '/start', '/getting-started',
        '/examples', '/showcase'
    ]
    
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
            
            # Extra check - filter out known false positive patterns
            if any(pattern in absolute_url.lower() for pattern in false_positive_patterns):
                logger.warning(f"Skipping likely false positive URL: {absolute_url}")
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
                
            # Skip educational links with terms like "learn" 
            if any(edu_term in link_text for edu_term in ['learn', 'tutorial', 'course', 'guide', 'start', 'example']):
                if not any(term in link_text for term in ['terms', 'legal', 'conditions']):
                    logger.warning(f"Skipping educational link in ToS search: {link_text}")
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
                
            # Apply strong penalty for educational content
            if any(pattern in url_lower for pattern in false_positive_patterns):
                score -= 20.0
            
            final_score = (score * 2.0) + (footer_score * 3.0) + (domain_score * 1.0)
            
            # Big bonus for footer links containing "terms" text
            if footer_score > 0 and ('terms' in link_text or 'conditions' in link_text):
                final_score += 10.0
            
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
                else:
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
    Try to find ToS link using standard requests + BeautifulSoup method.
    With fallback to privacy policy page if direct terms detection isn't reliable.
    """
    all_potential_tos_links = []
    
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
                
                # Verify that this is actually a ToS page by visiting it
                if not verify_tos_link(session, tos_link, headers):
                    logger.warning(f"Candidate ToS link failed verification: {tos_link}")
                    # Add to potential links for further inspection
                    all_potential_tos_links.append((tos_link, final_url, variation_type, 'failed_verification'))
                    continue
                
                # If we make it here, we have a verified ToS link
                logger.info(f"Verified ToS link: {tos_link} in {final_url} ({variation_type})")
                return TosResponse(
                    url=final_url,  # Return the actual URL after redirects
                    tos_url=tos_link,
                    success=True,
                    message=f"Verified Terms of Service link found on final destination page: {final_url}" + 
                            (f" (Found at {variation_type})" if variation_type != "original exact url" else ""),
                    method_used="standard_verified"
                )
            else:
                logger.info(f"No ToS link found in {final_url} ({variation_type})")
                
                # If direct ToS detection failed, try to find a privacy policy link
                from .privacy import find_privacy_link
                logger.info(f"Trying to find privacy policy link in {final_url}")
                privacy_link = find_privacy_link(final_url, soup)
                
                if privacy_link:
                    # Make it absolute if needed
                    if privacy_link.startswith('/'):
                        parsed_final_url = urlparse(final_url)
                        base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                        privacy_link = urljoin(base_url, privacy_link)
                    
                    logger.info(f"Found privacy link: {privacy_link}, will check this page for ToS")
                    try:
                        # Visit the privacy policy page to find ToS link there
                        privacy_response = session.get(privacy_link, headers=headers, timeout=15)
                        privacy_soup = BeautifulSoup(privacy_response.text, 'html.parser')
                        
                        # Now look for terms links in the privacy page
                        terms_from_privacy = find_tos_link(privacy_link, privacy_soup)
                        
                        if terms_from_privacy:
                            # Make it absolute if needed
                            if terms_from_privacy.startswith('/'):
                                parsed_privacy_url = urlparse(privacy_link)
                                base_url = f"{parsed_privacy_url.scheme}://{parsed_privacy_url.netloc}"
                                terms_from_privacy = urljoin(base_url, terms_from_privacy)
                            
                            # Verify this ToS link
                            if verify_tos_link(session, terms_from_privacy, headers):
                                logger.info(f"Verified ToS link from privacy page: {terms_from_privacy}")
                                return TosResponse(
                                    url=final_url,
                                    tos_url=terms_from_privacy,
                                    success=True,
                                    message=f"Verified Terms of Service link found via privacy policy page from: {final_url}",
                                    method_used="privacy_page_to_terms_verified"
                                )
                            else:
                                logger.warning(f"ToS link from privacy page failed verification: {terms_from_privacy}")
                    except Exception as e:
                        logger.error(f"Error finding ToS via privacy page: {str(e)}")
                    
        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({variation_type}): {str(e)}")
            continue
        except Exception as e:
            logger.error(f"Exception for {url} ({variation_type}): {str(e)}")
            continue
    
    # If we get here and have collected potential links, check them in order
    if all_potential_tos_links:
        logger.info(f"Checking {len(all_potential_tos_links)} potential ToS links")
        # Check any links that were classified as potential "learn" links
        for tos_link, final_url, variation_type, reason in all_potential_tos_links:
            # For each potential link, try to determine if it's a valid terms by looking at its content
            try:
                logger.info(f"Double-checking potential ToS link: {tos_link}")
                link_response = session.get(tos_link, headers=headers, timeout=15)
                link_soup = BeautifulSoup(link_response.text, 'html.parser')
                
                # Check page title and headers for terms-related keywords
                title = link_soup.find('title')
                title_text = title.get_text().lower() if title else ""
                
                h1_elements = link_soup.find_all('h1')
                h1_texts = [h1.get_text().lower() for h1 in h1_elements]
                
                # If the page has terms-related keywords in title or headings
                terms_keywords = ['terms', 'conditions', 'tos', 'terms of service', 'terms of use', 'legal', 'agreement']
                
                if any(keyword in title_text for keyword in terms_keywords) or any(any(keyword in h1 for keyword in terms_keywords) for h1 in h1_texts):
                    logger.info(f"Verified ToS link based on page content: {tos_link}")
                    return TosResponse(
                        url=final_url,
                        tos_url=tos_link,
                        success=True,
                        message=f"Terms of Service link verified by content analysis: {tos_link}",
                        method_used="content_verification"
                    )
            except Exception as e:
                logger.error(f"Error verifying potential ToS link {tos_link}: {str(e)}")
    
    # Try one more fallback - look for terms in the footer of the main page
    try:
        original_url = variations_to_try[0][0]
        response = session.get(original_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look specifically for footer links with terms-related text
        footer_elements = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower() if c else False))
        footer_elements += soup.find_all(['footer', 'div'], id=lambda i: i and ('footer' in i.lower() if i else False))
        
        for footer in footer_elements:
            for link in footer.find_all('a', href=True):
                href = link.get('href', '').strip()
                if not href:
                    continue
                    
                link_text = link.get_text().lower().strip()
                if any(term in link_text for term in ['terms', 'conditions', 'legal']):
                    # This looks like a terms link in the footer
                    absolute_url = urljoin(original_url, href)
                    logger.info(f"Found potential terms link in footer: {absolute_url}")
                    
                    # Skip learn pages and other likely false positives
                    if '/learn' in absolute_url.lower() or is_likely_false_positive(absolute_url, 'tos'):
                        continue
                    
                    # Verify this footer ToS link
                    if verify_tos_link(session, absolute_url, headers):
                        logger.info(f"Verified terms link from footer: {absolute_url}")
                        return TosResponse(
                            url=original_url,
                            tos_url=absolute_url,
                            success=True,
                            message=f"Verified Terms of Service link found in footer: {absolute_url}",
                            method_used="footer_search_verified"
                        )
                    else:
                        logger.warning(f"Footer terms link failed verification: {absolute_url}")
        
        # Try to extract provider platform information
        site_host = urlparse(original_url).netloc.lower()
        platform_info = detect_site_platform(soup, original_url)
        
        if platform_info:
            platform_name, platform_domain = platform_info
            logger.info(f"Detected site platform: {platform_name} ({platform_domain})")
            
            # If the site is using a platform, try the platform's legal pages
            if platform_domain and platform_domain != site_host:
                # First try direct access to the main ToS page - this is most reliable
                primary_tos_url = f"https://{platform_domain}/legal/terms"
                try:
                    primary_tos_response = session.get(primary_tos_url, headers=headers, timeout=15)
                    if primary_tos_response.status_code == 200:
                        # Check if this is actually a ToS page
                        if verify_tos_link(session, primary_tos_url, headers):
                            logger.info(f"Found primary ToS link for platform: {primary_tos_url}")
                            return TosResponse(
                                url=original_url,
                                tos_url=primary_tos_url,
                                success=True,
                                message=f"Terms of Service found via platform ({platform_name}): {primary_tos_url}",
                                method_used="platform_primary_tos"
                            )
                except Exception as e:
                    logger.error(f"Error checking primary ToS URL: {str(e)}")
                
                # Then try the legal hub page to find other ToS links
                platform_legal = f"https://{platform_domain}/legal"
                try:
                    platform_response = session.get(platform_legal, headers=headers, timeout=15)
                    if platform_response.status_code == 200:
                        platform_soup = BeautifulSoup(platform_response.text, 'html.parser')
                        platform_links = platform_soup.find_all('a', href=True)
                        
                        # Prioritize links by importance
                        primary_terms_links = []
                        secondary_terms_links = []
                        
                        for link in platform_links:
                            href = link.get('href', '').strip()
                            if not href:
                                continue
                                
                            link_text = link.get_text().lower().strip()
                            absolute_url = urljoin(platform_legal, href)
                            
                            # Skip non-terms links
                            if not any(term in link_text for term in ['terms', 'conditions']):
                                continue
                                
                            # Categorize by importance
                            href_lower = absolute_url.lower()
                            
                            # Primary ToS links - general Terms of Service/Use
                            if (('/terms' in href_lower or '/tos' in href_lower) and 
                                not any(specific in href_lower for specific in 
                                       ['/event-', '/partner-', '/enterprise-', '/services-', '/specific-'])):
                                primary_terms_links.append((absolute_url, link_text))
                            # Secondary ToS links - specific terms
                            else:
                                secondary_terms_links.append((absolute_url, link_text))
                        
                        # First check primary links
                        for absolute_url, link_text in primary_terms_links:
                            logger.info(f"Checking primary terms link: {absolute_url}")
                            if verify_tos_link(session, absolute_url, headers):
                                return TosResponse(
                                    url=original_url,
                                    tos_url=absolute_url,
                                    success=True,
                                    message=f"Terms of Service found via platform ({platform_name}) legal page: {absolute_url}",
                                    method_used="platform_legal_primary"
                                )
                        
                        # Then check secondary links only if no primary links work
                        for absolute_url, link_text in secondary_terms_links:
                            logger.info(f"Checking secondary terms link: {absolute_url}")
                            if verify_tos_link(session, absolute_url, headers):
                                return TosResponse(
                                    url=original_url,
                                    tos_url=absolute_url,
                                    success=True,
                                    message=f"Specific Terms of Service found via platform ({platform_name}) legal page: {absolute_url}",
                                    method_used="platform_legal_secondary"
                                )
                except Exception as e:
                    logger.error(f"Error with platform legal fallback: {str(e)}")
                    
    except Exception as e:
        logger.error(f"Error in footer fallback search: {str(e)}")
    
    return TosResponse(
        url=variations_to_try[0][0],  # Use the original URL
        success=False,
        message="No Terms of Service link found with standard method",
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