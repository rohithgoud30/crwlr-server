import random
from urllib.parse import urlparse, urljoin, parse_qs
import re
import traceback
import time
import logging
from fastapi import APIRouter, HTTPException, status, Depends
from playwright.async_api import async_playwright
from typing import Optional, List
import platform
import httpx
from bs4 import BeautifulSoup

from app.models.tos import ToSRequest, ToSResponse
from app.models.privacy import PrivacyRequest, PrivacyResponse
from app.api.v1.endpoints.privacy import find_privacy_policy

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Define consistent user agent
CONSISTENT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# Replace random user agent function with consistent one
def get_user_agent():
    """
    Returns a consistent user agent string.
    """
    return CONSISTENT_USER_AGENT

exactMatchPriorities = {
    "terms of service": 100,
    "terms of use": 95,
    "terms and conditions": 90,
    "user agreement": 85,
    "service agreement": 80,
    "legal agreement": 75,
    "platform agreement": 70,
}

# Priorities for partial match terms
partialMatchPriorities = {
    "platform terms": 60,
    "website terms": 55,
    "full terms": 50,
    "detailed terms": 45,
    "complete terms": 40,
    "legal terms": 35,
    "general terms": 30,
    "service terms": 25,
    "user terms": 20,
}

# Define your strong match terms here
strong_terms_matches = [
    "terms of service",
    "terms of use",
    "terms and conditions",
    "conditions of use",
    "condition of use",
    "user agreement",
    "terms",
    "tos",
    "eula",
    "legal terms",
]

# Define dynamic scoring weights for link evaluation
LINK_EVALUATION_WEIGHTS = {
    "text_match": 0.4,
    "url_structure": 0.3,
    "context": 0.2,
    "position": 0.1,
}

# Common URL path patterns for ToS (for pattern matching, not hardcoded paths)
TOS_PATH_PATTERNS = [
    r"/terms",
    r"/terms-of-service",
    r"/terms-of-use",
    r"/terms-and-conditions",
    r"/tos",
    r"/legal/terms",
    r"/legal",
    r"/terms-conditions",
    r"/user-agreement",
    r"/eula",
]

def normalize_url(url: str) -> str:
    """Normalize URL to handle common variations"""
    if not url:
        return url
    
    # Remove trailing slashes, fragments and normalize to lowercase
    url = url.lower().split('#')[0].rstrip('/')
    
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
        
    return url

def sanitize_url(url: str) -> str:
    """
    Sanitize and validate URLs to ensure they are valid.
    
    If the URL is severely malformed or clearly invalid, returns an empty string
    instead of attempting to fix it.
    """
    if not url:
        logger.debug("Empty URL provided")
        return ""
        
    # Trim whitespace and control characters
    url = url.strip().strip('\r\n\t')
    
    # Log the original URL for debugging
    logger.debug(f"Validating URL: {url}")
    
    try:
        # Fix only the most common minor issues
        # Add protocol if missing
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        
        # Validate the URL structure
        parsed = urlparse(url)
        
        # Check for severely malformed URLs
        if not parsed.netloc or '.' not in parsed.netloc:
            logger.debug(f"Invalid domain in URL: {url}")
            return ""
            
        # Check for nonsensical URL patterns that indicate a malformed URL
        if re.match(r'https?://[a-z]+s?://', url):
            # Invalid patterns like https://ttps://
            logger.debug(f"Malformed URL with invalid protocol pattern: {url}")
            return ""
            
        # Additional validation to ensure domain has a valid TLD
        domain_parts = parsed.netloc.split('.')
        if len(domain_parts) < 2 or len(domain_parts[-1]) < 2:
            logger.debug(f"Domain lacks valid TLD: {url}")
            return ""
            
        logger.debug(f"URL validated: {url}")
        return url
    except Exception as e:
        logger.error(f"Error validating URL {url}: {str(e)}")
        return ""


def normalize_domain(url):
    """
    Normalize domain variations (with or without www prefix)

    Args:
        url: The URL to normalize

    Returns:
        Normalized URL with consistent domain format
    """
    if not url:
        return url

    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Handle "example.com" vs "www.example.com" variations
        if (
            domain
            and not domain.startswith("www.")
            and "." in domain
            and not domain.startswith("m.")
        ):
            # For non-www domains, add www if it's a standard domain (not subdomains)
            # This only applies to domains that don't already have a subdomain
            if domain.count(".") == 1:  # Only one dot indicates a likely main domain
                return url.replace(domain, f"www.{domain}")

        return url
    except Exception as e:
        print(f"Error normalizing domain: {e}")
        return url  # Return original URL if parsing fails


def is_likely_user_generated_content(url):
    """
    Check if a URL is likely to be user-generated content like a forum post, discussion, comment, etc.
    
    Args:
        url: The URL to check
        
    Returns:
        bool: True if the URL is likely user-generated content, False otherwise
    """
    if not url:
        return False
        
    try:
        parsed = urlparse(url)
        path = parsed.path.lower()
        hostname = parsed.netloc.lower()
        
        # Check if the URL path contains elements that suggest user content
        user_content_indicators = ['post', 'thread', 'topic', 'discussion', 'comment', 
                                  'forum', 'question', 'answer', 'review', 'article', 
                                  'profile', 'user', 'member', 'status', 'tweet']
        
        # Legal content indicators that override user content detection
        legal_content_indicators = ['terms', 'tos', 'service', 'legal', 'privacy', 
                                    'policy', 'agreement', 'condition']
        
        # Check if path contains legal indicators
        for indicator in legal_content_indicators:
            if indicator in path:
                # This might be a legal document path
                return False
        
        # Check if path contains user content indicators
        for indicator in user_content_indicators:
            if indicator in path:
                return True
                
        # Check for typical IDs in user content
        # Common alphanumeric ID patterns
        if (re.search(r'/[a-z0-9]{8,}/?$', path) or 
            re.search(r'/[a-z0-9]{6,}\-[a-z0-9]{6,}/?$', path) or
            re.search(r'/\d{5,}/?$', path)):
            return True
            
        # Check for query parameters that indicate a discussion
        query_params = parsed.query.lower()
        discussion_params = ['threadid', 'postid', 'commentid', 'forumid', 'topicid', 'discussionid']
        
        for param in discussion_params:
            if param in query_params:
                return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking if URL is user content: {e}")
        return False

def prefer_main_domain(links, main_domain):
    """
    Prioritize links that are from the main domain rather than third-party sites.
    
    Args:
        links: List of link dictionaries with URL info
        main_domain: The main domain to prioritize
    
    Returns:
        Filtered list with main domain links first
    """
    # Filter links to separate main domain and other domains
    main_domain_links = []
    other_domain_links = []
    
    for link in links:
        link_url = link["url"]
        try:
            parsed_url = urlparse(link_url)
            link_domain = parsed_url.netloc.lower()
            
            # Remove 'www.' prefix for comparison if present
            if link_domain.startswith('www.'):
                link_domain = link_domain[4:]
                
            # Check if this is the main domain or a subdomain of it
            if link_domain == main_domain or link_domain.endswith('.' + main_domain):
                # Boost score for main domain links
                link["score"] += 50
                main_domain_links.append(link)
            else:
                other_domain_links.append(link)
        except Exception:
            # If parsing fails, just keep the link in the order it was
            other_domain_links.append(link)
    
    # Sort both lists by score
    main_domain_links.sort(key=lambda x: x["score"], reverse=True)
    other_domain_links.sort(key=lambda x: x["score"], reverse=True)
    
    # Return main domain links first, followed by other domain links
    return main_domain_links + other_domain_links

def find_terms_link(soup: BeautifulSoup, base_url: str) -> str:
    """
    Scan anchors in the page for terms of service, terms of use, and other legal agreement links
    using extended patterns. Ensures the returned URL is absolute and truly a terms document.

    Args:
        soup: BeautifulSoup object representing the parsed HTML of the page.
        base_url: The final URL of the page after any redirects, used for resolving relative links.

    Returns:
        The absolute URL of the most likely terms document link found, or an empty string if none is found.
    """
    # Required terms patterns - MUST include at least one of these in text or href
    # Keep existing strong_terms_matches
    required_terms_matches = strong_terms_matches + [
        "legal agreement",
        "platform agreement",
        "service agreement",
        "legal notice",
        "legal terms",
        "website agreement",
        "site terms",
        "website terms",
        "site usage",
        "website usage",
        "legal information"
    ]

    # URL path fragments that strongly indicate a terms page
    # Use existing TOS_PATH_PATTERNS plus add more
    terms_path_indicators = TOS_PATH_PATTERNS + [
        "/legal/terms-of-service",
        "/legal/terms-of-use",
        "/legal/user-agreement",
        "/policies/terms",
        "/policies/terms-of-service",
        "/policies/terms-of-use",
        "/about/terms",
        "/about/legal",
        "/info/terms",
    ]
    
    # Convert path patterns from regex format to simple strings for easier matching
    terms_path_indicators = [pattern.replace(r"/", "/") for pattern in terms_path_indicators]

    # URL path fragments that clearly indicate NOT a terms page
    strong_negative_path_patterns = [
        "/podcast", "/article", "/post", "/blog", "/news",
        "/story", "/video", "/watch", "/listen", "/stream",
        "/search", "/tag", "/category", "/author", "/feed",
        "/comment", "/opinion", "/editorial", "/collection",
        "/report", "/archive", "/topic", "/episode", "/series",
        "/download", "/media", "/show", "/event", "/release",
        "/careers", "/jobs", "/investor", "/press", "/contact", "/about-us",
        "/product/", "/shop/", "/cart/", "/checkout/", "/login", "/signup", "/register"
    ]

    # User/customer related terms - HIGH PRIORITY
    user_customer_terms = [
        "user terms", "customer terms",
        "user agreement", "customer agreement",
        "user conditions", "customer conditions",
        "user legal", "customer legal",
        "user rights", "customer rights",
    ]

    # Content sections clearly not related to terms - immediate disqualification based on link text
    negative_content_sections = [
        "podcast", "episode", "article", "blog post", "latest news",
        "video", "watch", "listen", "stream", "download",
        "author", "contributor", "journalist", "reporter",
        "editor", "interview", "comment", "opinion", "careers", "jobs", "investor", "press"
    ]

    logger.info(f"Scanning for terms of service links on {base_url}")
    candidate_links = []

    # First pass: Collect and score all potential terms links
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        # Skip empty or non-web links (mailto, tel, javascript, anchors, file, data URIs)
        if not href or href.lower().startswith(("mailto:", "tel:", "javascript:", "#", "file:", "data:")):
                    continue

        # Get text and other attributes
        text = (anchor.get_text() or "").strip().lower()
        href_lower = href.lower()
        rels = anchor.get("rel", []) or [] # Ensure rels is always a list
        rels = [r.lower() for r in rels if isinstance(r, str)] # Ensure elements are strings

        # Skip links with non-terms content words in text
        if any(neg_term in text for neg_term in negative_content_sections):
            logger.debug(f"Skipping link with negative content term in text: '{text}' ({href})")
            continue
                    
        # STRICT requirement: Must contain at least one terms term or strong path indicator in text or href
        has_terms_indicator = any(term in text for term in required_terms_matches) or \
                             any(term in href_lower for term in required_terms_matches) or \
                             any(indicator in href_lower for indicator in terms_path_indicators) or \
                             "terms" in rels or "tos" in rels

        if not has_terms_indicator:
                    continue
            
        # Attempt to resolve to absolute URL
        try:
            # First check if the relative URL itself looks suspicious (e.g., blog post path)
            if any(neg_pattern in href_lower for neg_pattern in strong_negative_path_patterns):
                logger.debug(f"Skipping link with negative path pattern in relative href: {href}")
                continue

            # Resolve to absolute URL - CRUCIAL STEP
            abs_url = urljoin(base_url, href)

            # Parse the URL to examine components
            parsed_url = urlparse(abs_url)

            # STRICT: MUST be HTTP/HTTPS
            if not parsed_url.scheme.lower().startswith("http"):
                logger.debug(f"Skipping non-http(s) URL: {abs_url}")
                continue
                
            # Examine path component
            path = parsed_url.path.lower()
            # Ensure path_segments calculation handles empty paths correctly and filters empty strings
            path_segments = [seg for seg in path.strip("/").split("/") if seg]
            num_path_segments = len(path_segments)
            query = parsed_url.query.lower() if parsed_url.query else ""

            # STRICT: Immediate disqualification for non-terms page paths in the resolved URL
            if any(neg_pattern in path for neg_pattern in strong_negative_path_patterns):
                logger.debug(f"Absolute URL path '{path}' contains negative pattern. Skipping: {abs_url}")
                continue
            
            # Calculate confidence score
            score = 0
            is_user_customer_related = False

            # Check if any segment is purely digits or if the path contains digits (excluding slashes)
            contains_digits_in_path = any(segment.isdigit() for segment in path_segments) or \
                                      any(char.isdigit() for char in path if char != '/')

            # == HIGHEST PRIORITY: User/Customer Terms (Increased Boost) ==
            # Check text for user/customer terms
            if any(term in text for term in user_customer_terms): 
                score += 250
                is_user_customer_related = True
            
            # == Exact match priorities based on exactMatchPriorities ==
            for exact_term, priority in exactMatchPriorities.items():
                if text == exact_term:
                    score += priority
                    break
            
            # == Partial match priorities based on partialMatchPriorities ==
            for partial_term, priority in partialMatchPriorities.items():
                if partial_term in text:
                    score += priority
                    break

            # == URL Path/Query Scoring ==
            # Bonus for standard terms paths
            # Check if path *exactly* matches or *ends with* a known indicator
            path_matched_indicator = False
            for indicator in terms_path_indicators:
                if path == indicator or path.endswith(indicator):
                    score += 120 # High score for strong path match
                    logger.debug(f"URL path strongly matches terms indicator '{indicator}': {path}")
                    path_matched_indicator = True
                    break # Stop after first strong match
            
            # Lower bonus if path just *contains* the indicator (less specific)
            if not path_matched_indicator and any(indicator in path for indicator in terms_path_indicators):
                score += 60
                logger.debug(f"URL path contains terms indicator: {path}")

            # Special case for parameter-based terms pages - Check query parameters
            query_params_lower = {k.lower(): v for k, v in parse_qs(parsed_url.query).items()}
            if "nodeid" in query_params_lower or "helpid" in query_params_lower or "legal" in path:
                score += 60
                logger.debug(f"URL appears to use nodeId/helpId/legal parameter common in terms pages: {abs_url}")

            # == Contextual Scoring ==
            # Rel attributes
            if "terms" in rels: score += 100
            if "tos" in rels: score += 100
            if "legal" in rels: score += 80

            # Footer link bonus
            is_footer_link = False
            parent = anchor.parent
            # Check up to 3 levels up for footer indicators (tag name or class)
            for _ in range(3):
                if parent:
                    # Use getattr for safe access to attributes
                    parent_name = getattr(parent, 'name', '').lower()
                    # Safe way to get classes, handle non-string elements
                    parent_classes_raw = getattr(parent, 'get', lambda k, d=[]: d)('class', [])
                    parent_classes = [str(cls).lower() for cls in parent_classes_raw] # Convert all to lower string

                    footer_classes = ["footer", "bottom", "legal", "site-footer", "page-footer", "siteinfo", "legallinks"]
                    if parent_name == "footer" or \
                       any(cls in parent_classes for cls in footer_classes):
                        is_footer_link = True
                        break
                    parent = getattr(parent, 'parent', None) # Move up to the next parent
                else:
                    break # Stop if no more parents

            if is_footer_link:
                score += 30
                logger.debug(f"Link appears to be in footer: {abs_url}")

            # == Penalties ==
            # Penalize deep paths (more than 4 segments)
            if num_path_segments > 4:
                penalty = (num_path_segments - 4) * 15
                score -= penalty
                logger.debug(f"Applying path depth penalty: -{penalty} points for {num_path_segments} segments in {path}")

            # Penalize paths containing digits (potential IDs, less likely for top-level policy)
            if contains_digits_in_path:
                score -= 25
                logger.debug(f"Applying path digit penalty: -25 points for digits in {path}")

            # Penalize URLs likely to be user-generated content
            if is_likely_user_generated_content(abs_url):
                score -= 100
                logger.debug(f"Applying heavy penalty for likely user-generated content: {abs_url}")

            # Additional scoring for path structure characteristics  
            if "/tos" in path.lower() or path.lower().endswith("/tos"):
                score += 25
                logger.debug(f"Added bonus for /tos in path: {path}")
            elif "/terms" in path.lower() or path.lower().endswith("/terms"):
                score += 20
                logger.debug(f"Added bonus for /terms in path: {path}")
                
            # Special case for Amazon's Terms of Service URL pattern
            if ("nodeId=508088" in query or 
                "/help/customer/display.html" in path.lower() and "cou" in query.lower()):
                score += 20
                logger.debug(f"Added bonus for Amazon-specific ToS pattern: {abs_url}")
                
            # Special cases for other common e-commerce ToS patterns
            if ("/help/legal/conditions-of-use" in path.lower() or  # Amazon alt
                "/ReturnPolicy" in path or                         # Walmart
                "/terms-of-service/shopping" in path.lower() or    # Target
                "/customer-service/policies" in path.lower() or    # Best Buy
                "/en-us/help/terms-of-use" in path.lower() or      # Microsoft
                "/en/legal/terms-of-service" in path.lower() or    # Apple
                "/legal/internet/terms_of_use.html" in path.lower()): # IBM
                score += 25
                logger.debug(f"Added bonus for known e-commerce ToS pattern: {abs_url}")
                
            # Check for queries that suggest terms doc
            if "tos" in query.lower() or "terms" in query.lower() or "conditions" in query.lower():
                score += 15
                logger.debug(f"Added bonus for tos/terms/conditions in query params: {query}")

            # Log the final score for better debugging
            logger.debug(f"Final score for link '{text}' ({abs_url}): {score}")

            # Add URL as a candidate if score is high enough
            if score >= 50: # Minimum score threshold to be considered a candidate
                candidate_links.append({
                    "url": abs_url,
                    "score": score,
                    "text": text,
                    "path": path,
                    "query": query,
                    "is_footer": is_footer_link
                })
            else:
                logger.debug(f"Link score too low ({score}), ignoring: '{text}' ({abs_url})")

        except Exception as e:
            # Log errors during URL processing but continue with other links
            logger.warning(f"Error processing link href='{href}' on base '{base_url}': {str(e)}")
            continue

    # Sort candidates by score (highest first)
    candidate_links.sort(key=lambda x: x["score"], reverse=True)

    # Get the base domain to prioritize main domain links
    try:
        parsed_base_url = urlparse(base_url)
        main_domain = parsed_base_url.netloc.lower()
        # Remove www. prefix if present
        if main_domain.startswith('www.'):
            main_domain = main_domain[4:]
        
        # Apply main domain preference
        if candidate_links:
            candidate_links = prefer_main_domain(candidate_links, main_domain)
    except Exception as e:
        logger.warning(f"Error extracting main domain from {base_url}: {e}")
        # Continue with original candidate links order if domain extraction fails

    # Log top candidates for debugging
    if candidate_links:
        logger.info(f"Found {len(candidate_links)} potential terms of service link candidates after scoring")
        for i, candidate in enumerate(candidate_links[:5]): # Show top 5
            logger.debug(f"  Candidate #{i+1}: Score={candidate['score']}, URL={candidate['url']}, Text='{candidate['text']}'")

        # Use the highest-scoring candidate that meets a high confidence threshold
        best_candidate = candidate_links[0]

        # Final validation - MUST be a valid absolute URL that starts with http and meets score threshold
        if (best_candidate["score"] >= 50 and # Confidence threshold
            isinstance(best_candidate["url"], str) and
            best_candidate["url"].lower().startswith(("http://", "https://"))):

            logger.info(f"Selected high-confidence terms link: {best_candidate['url']} (Score: {best_candidate['score']})")

            # Final verification - check URL structure one more time for negative patterns
            final_url = best_candidate["url"]
            final_path = best_candidate["path"]

            if any(neg_pattern in final_path for neg_pattern in strong_negative_path_patterns):
                logger.warning(f"Final check rejected suspicious URL path: {final_url}")
                return "" # No suitable link found

            return final_url
        else:
            # Log why the best candidate wasn't selected
            log_reason = ""
            if best_candidate["score"] < 50:
                log_reason = f"score ({best_candidate['score']}) below threshold (50)"
            elif not best_candidate["url"].lower().startswith(("http://", "https://")):
                log_reason = "not a valid absolute http(s) URL"
            else:
                log_reason = "failed validation for unknown reason"

            logger.warning(f"Best candidate rejected: {best_candidate['url']} (Score: {best_candidate['score']}). Reason: {log_reason}")
            return "" # Return empty if no high-confidence link found
    else:
        logger.info("No terms link candidates found after filtering and scoring.")
        return "" # Return empty string if no candidates were found

@router.post("/tos", response_model=ToSResponse, status_code=status.HTTP_200_OK)
async def find_tos(request: ToSRequest) -> ToSResponse:
    """
    Find the Terms of Service URL for a given website.
    """
    # 1. Normalize and sanitize input URL
    normalized_url = normalize_url(request.url)
    if not normalized_url:
        logger.error(f"Input URL '{request.url}' could not be normalized.")
        return ToSResponse(
            url=request.url,
            success=False,
            tos_url=None, 
            message="Invalid URL provided (failed normalization).",
            method_used="html_parsing"
        )

    target_url = sanitize_url(normalized_url)
    if not target_url:
        logger.error(f"Normalized URL '{normalized_url}' failed sanitization.")
        return ToSResponse(
            url=request.url,
            success=False,
            tos_url=None, 
            message="Invalid URL provided (failed sanitization).",
            method_used="html_parsing"
        )

    # 2. Prepare HTTP request
    headers = {
        "User-Agent": get_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    tos_url = ""
    error_message = None
    final_url_fetched = target_url  # Keep track of the URL after redirects

    # 3. Fetch and parse URL content
    try:
        # Use httpx.AsyncClient for async requests
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers=headers) as client:
            logger.info(f"Attempting to fetch content from: {target_url}")
            response = await client.get(target_url)

            # Store the final URL after potential redirects
            final_url_fetched = str(response.url)
            logger.info(f"Successfully fetched. Final URL: {final_url_fetched} (Status: {response.status_code})")

            # Raise exception for 4xx (client errors) or 5xx (server errors) status codes
            response.raise_for_status()

            # Check content type - proceed only if HTML
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                logger.warning(f"Content-Type is not HTML ('{content_type}'). Skipping parsing for {final_url_fetched}.")
                error_message = f"Content-Type ('{content_type}') is not HTML. Cannot parse for links."
                # Return early as we can't parse non-HTML
                return ToSResponse(
                    url=request.url,
                    success=False,
                    tos_url=None,
                    message=error_message,
                    method_used="html_parsing"
                )

            # Use BeautifulSoup to parse the HTML content
            try:
                soup = BeautifulSoup(response.text, 'lxml')
            except ImportError:
                logger.warning("lxml not found, falling back to html.parser.")
                soup = BeautifulSoup(response.text, 'html.parser')

            # 4. Find the terms link using the dedicated function
            logger.info(f"Searching for Terms of Service on page: {final_url_fetched}")
            tos_url = find_terms_link(soup, final_url_fetched)

            if not tos_url:
                logger.warning(f"Could not find a terms of service link directly on {final_url_fetched}")
                # If no link found by parsing, set appropriate error message
                error_message = "Could not find a terms of service link on the page."
            else:
                logger.info(f"Found potential ToS URL: {tos_url}")
                # Validate the found URL before returning
                validated_url = sanitize_url(tos_url)
                if not validated_url:
                    logger.warning(f"Found terms link '{tos_url}' failed sanitization. Discarding.")
                    error_message = "Found a potential link, but it failed validation."
                    tos_url = ""  # Reset if invalid
                else:
                    logger.info(f"Validated ToS URL: {validated_url}")
                    tos_url = validated_url  # Use the sanitized version

    except httpx.TimeoutException as exc:
        logger.error(f"Timeout error fetching {target_url}: {exc}")
        error_message = f"The request timed out while trying to reach {exc.request.url!r}."
    except httpx.RequestError as exc:
        # Catches DNS errors, connection errors, etc.
        logger.error(f"HTTP request error fetching {target_url}: {exc}")
        error_message = f"An error occurred while requesting {exc.request.url!r}: {type(exc).__name__}"
    except httpx.HTTPStatusError as exc:
        # Catches 4xx/5xx responses after raise_for_status()
        logger.error(f"HTTP status error {exc.response.status_code} while fetching {target_url}: {exc}")
        error_message = f"Error response {exc.response.status_code} while requesting {exc.request.url!r}."
    except Exception as e:
        # Catch-all for any other unexpected errors during the process
        logger.error(f"An unexpected error occurred for {target_url}: {e}", exc_info=True)
        error_message = f"An unexpected error occurred: {type(e).__name__}"

    # 5. Prepare and return response
    if not tos_url and not error_message:
        # This case might happen if find_terms_link returns "" but no exception occurred
        error_message = "Terms of service link not found after scanning the page."

    # Return the found URL or an error indication using the Pydantic response model
    return ToSResponse(
        url=request.url,  # Return the user's original input URL
        tos_url=tos_url or None,  # Return None if empty string
        success=bool(tos_url),  # True if we found a terms of service URL
        message=error_message or ("Terms of service URL found successfully" if tos_url else "No terms of service URL found"),
        method_used="html_parsing"  # Add the required method_used field
    )

def is_app_store_url(url: str) -> bool:
    """Check if the URL is from Apple App Store."""
    return "apps.apple.com" in url or "itunes.apple.com" in url

def is_play_store_url(url: str) -> bool:
    """Check if the URL is from Google Play Store."""
    return "play.google.com" in url or "play.app.goo.gl" in url

async def find_tos_via_html_inspection(url: str) -> str:
    """
    Find ToS page by inspecting HTML content directly through DOM analysis.
    This is a simplified version that uses direct HTML parsing instead of
    multiple search engine fallbacks.
    
    Args:
        url: URL of the website to inspect
    
    Returns:
        URL to ToS page if found, empty string otherwise
    """
    try:
        # Normalize and sanitize URL
        if not url.startswith(('http://', 'https://')):
            url = f"https://{url}"
            
        # Use a simple direct HTTP request approach similar to the main ToS finder
        headers = {
            "User-Agent": get_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers=headers) as client:
            logger.info(f"HTML inspection: Attempting to fetch {url}")
            response = await client.get(url)
            response.raise_for_status()
            
            # Use BeautifulSoup to parse HTML
            try:
                soup = BeautifulSoup(response.text, 'lxml')
            except ImportError:
                soup = BeautifulSoup(response.text, 'html.parser')
                
            # Find ToS links using the same function as the primary method
            tos_url = find_terms_link(soup, str(response.url))
            
            if tos_url:
                logger.info(f"HTML inspection found ToS URL: {tos_url}")
                return tos_url
            else:
                logger.info("HTML inspection could not find ToS URL")
                return ""
    except Exception as e:
        logger.error(f"Error during HTML inspection: {str(e)}")
        return ""
