import asyncio
import re
from urllib.parse import urlparse, urljoin
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl
from app.models.privacy import PrivacyRequest, PrivacyResponse
import httpx
from bs4 import BeautifulSoup
import logging
import urllib.parse # Keep for urlparse, urljoin
import random # Added for get_user_agent

# Initialize router and logger
router = APIRouter()
logger = logging.getLogger(__name__)
# Configure basic logging if not configured elsewhere
logging.basicConfig(level=logging.INFO)

# Define consistent user agent
CONSISTENT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def get_random_user_agent():
    """
    Returns a consistent user agent string.
    """
    return CONSISTENT_USER_AGENT


def normalize_url(url: str) -> str:
    """
    Ensure the URL includes a protocol. If missing, prepend https://.
    """
    url = (url or "").strip()
    if not url:
        return ""
    # Check if the URL starts with http:// or https:// (case-insensitive)
    if not url.lower().startswith(("http://", "https://")):
        # Basic check if it looks like a domain name before prepending https://
        if '.' in url and '/' not in url.split('.')[0]: # Avoid prepending to paths
             logger.debug(f"Prepending https:// to {url}")
             return "https://" + url
        else:
             logger.warning(f"URL '{url}' does not start with http/https and doesn't look like a domain. Returning empty.")
             return "" # Return empty if it doesn't look like a standard domain needing a scheme
    return url


def prefer_main_domain(links, main_domain):
    """
    Given a list of candidate links, prioritize those from the main domain.
    
    Args:
        links: List of dictionaries containing URL candidates with their scores
        main_domain: The main domain to prioritize
        
    Returns:
        The sorted list with main domain links prioritized
    """
    # Helper function to check if a URL is from the main domain
    def is_main_domain(url):
        try:
            parsed_url = urlparse(url)
            url_domain = parsed_url.netloc.lower()
            
            # Extract main domain without www. prefix
            if url_domain.startswith('www.'):
                url_domain = url_domain[4:]
                
            # Check if the URL domain is the same as or a subdomain of the main domain
            return url_domain == main_domain or url_domain.endswith('.' + main_domain)
        except Exception as e:
            logger.warning(f"Error checking domain for URL {url}: {e}")
            return False
    
    # Split links into main domain and external domain
    main_domain_links = []
    external_links = []
    
    for link in links:
        if is_main_domain(link["url"]):
            # Boost score for main domain links
            link["score"] += 50
            main_domain_links.append(link)
        else:
            external_links.append(link)
    
    # Sort both lists by score
    main_domain_links.sort(key=lambda x: x["score"], reverse=True)
    external_links.sort(key=lambda x: x["score"], reverse=True)
    
    # Combine lists with main domain links first
    return main_domain_links + external_links


def find_privacy_link(soup: BeautifulSoup, base_url: str) -> str:
    """
    Scan anchors in the page for privacy, cookie, and data protection related links
    using extended patterns. Ensures the returned URL is absolute and truly a privacy policy.

    Args:
        soup: BeautifulSoup object representing the parsed HTML of the page.
        base_url: The final URL of the page after any redirects, used for resolving relative links.

    Returns:
        The absolute URL of the most likely privacy policy link found, or an empty string if none is found.
    """
    # Privacy-specific patterns - MUST include at least one of these in text or href
    required_privacy_terms = [
        "privacy policy", "privacy notice", "privacy statement",
        "data privacy", "data protection policy", "privacy",
        "privacy rights", "privacy preferences", "privacy choices",
        "your privacy", "personal data", "personal information",
        "data rights", "privacy settings", "data protection rights",
        # Additional privacy rights related terms
        "right to access", "right to rectification", "right to erasure",
        "right to be forgotten", "right to restriction", "right to data portability",
        "right to object", "rights concerning automated decision making",
        "ccpa rights", "gdpr rights", "cpra rights", "privacy law rights",
        "consumer rights", "data subject rights", "opt-out rights",
        "do not sell my data", "do not share my data"
    ]

    # URL path fragments that strongly indicate a privacy page
    privacy_path_indicators = [
        # Standard privacy paths
        "/privacy", "/privacy-policy", "/privacy_policy", "/privacypolicy",
        "/privacy-notice", "/privacy_notice", "/privacynotice",
        "/privacy-statement", "/privacy_statement", "/privacystatement",
        "/legal/privacy", "/about/privacy", "/your-privacy",
        "/data-privacy", "/data_privacy", "/dataprivacy",
        "/data-protection", "/data_protection", "/dataprotection",
        "/terms-privacy", "/terms_privacy",
        # Additional privacy rights paths
        "/data-rights", "/data_rights", "/your-rights", "/your_rights",
        "/privacy-rights", "/privacy_rights", "/privacyrights",
        "/privacy-choices", "/privacy_choices", "/privacychoices",
        "/your-privacy-rights", "/your_privacy_rights", "/yourprivacyrights",
        "/do-not-sell", "/do_not_sell", "/opt-out", "/opt_out",
        "/ccpa", "/gdpr", "/cpra",

        # Help section privacy paths (common in e-commerce)
        "/help/privacy", "/help/customer/privacy",
        "/gp/help/customer/display.html", # Amazon-style paths
        "/legal/privacy-policy", "/privacyprefs", # More e-commerce patterns

        # Common platform-specific paths
        "/settings/privacy", "/account/privacy", "/profile/privacy",
    ]

    # URL patterns that typically indicate help center or support pages containing privacy info
    help_center_patterns = [
        "/help/", "/support/", "/customer/", "/gp/help/",
        "/customer-service/", "/customer-support/",
        "nodeId=", "helpId=", "topic=privacy", "topic=data" # Query parameters
    ]

    # Content sections clearly not related to privacy - immediate disqualification based on link text
    negative_content_sections = [
        "podcast", "episode", "article", "blog post", "latest news",
        "video", "watch", "listen", "stream", "download",
        "author", "contributor", "journalist", "reporter",
        "editor", "interview", "comment", "opinion", "careers", "jobs", "investor", "press"
    ]

    # URL path fragments that clearly indicate NOT a privacy page
    strong_negative_path_patterns = [
        "/podcast", "/article", "/post", "/blog", "/news",
        "/story", "/video", "/watch", "/listen", "/stream",
        "/search", "/tag", "/category", "/author", "/feed",
        "/comment", "/opinion", "/editorial", "/collection",
        "/report", "/archive", "/topic", "/episode", "/series",
        "/download", "/media", "/show", "/event", "/release",
        "/careers", "/jobs", "/investor", "/press", "/contact", "/about-us", # Added more negative paths
        "/product/", "/shop/", "/cart/", "/checkout/", "/login", "/signup", "/register"
    ]

    # User/customer related terms - HIGH PRIORITY
    user_customer_privacy_terms = [
        "user privacy", "customer privacy",
        "user data protection", "customer data protection",
        "user information", "customer information",
        "user rights", "customer rights",
        "user preferences", "user choices",
        "customer preferences", "customer choices",
        "user consent", "customer consent",
        "user data", "customer data",
    ]

    # Path fragments for user/customer privacy
    user_customer_path_indicators = [
        "/user-privacy", "/user_privacy", "/user/privacy",
        "/customer-privacy", "/customer_privacy", "/customer/privacy",
        "/account/privacy", "/profile/privacy", "/my-privacy"
    ]

    logger.info(f"Scanning for privacy policy links on {base_url}")
    candidate_links = []

    # First pass: Collect and score all potential privacy policy links
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

        # Skip links with non-privacy content words in text
        if any(neg_term in text for neg_term in negative_content_sections):
            logger.debug(f"Skipping link with negative content term in text: '{text}' ({href})")
            continue

        # STRICT requirement: Must contain at least one privacy term or strong path indicator in text or href
        has_privacy_indicator = any(term in text for term in required_privacy_terms) or \
                                any(term in href_lower for term in required_privacy_terms) or \
                                any(indicator in href_lower for indicator in privacy_path_indicators) or \
                                "privacy-policy" in rels or "cookie-policy" in rels

        if not has_privacy_indicator:
            # logger.debug(f"Skipping link lacking privacy indicator: '{text}' ({href})") # Optional: very verbose
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

            # STRICT: Immediate disqualification for non-privacy page paths in the resolved URL
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
            if any(term in text for term in user_customer_privacy_terms): score += 250; is_user_customer_related = True
            if "user privacy notice" in text or "user privacy policy" in text: score += 120
            if "customer privacy notice" in text or "customer privacy policy" in text: score += 100

            # Check URL path/query for user/customer terms
            if any(term in path for term in user_customer_path_indicators): score += 200; is_user_customer_related = True
            if any(term in query for term in ["user", "customer"]) and "privacy" in query: score += 120; is_user_customer_related = True

            # == Standard Privacy Terms Scoring (added if not already high score from user/customer) ==
            # Only apply these if the score isn't already high from user/customer terms, to avoid double counting heavily
            if not is_user_customer_related or score < 150: # Adjusted threshold
                # High-value exact text matches
                if text == "privacy policy" or text == "privacy notice": score += 100
                elif "privacy policy" in text or "privacy notice" in text: score += 80
                elif "privacy statement" in text: score += 70
                elif "privacy rights" in text or "privacy choices" in text: score += 70
                elif "your privacy" in text: score += 70
                elif "personal data" in text or "personal information" in text: score += 65
                elif "privacy" in text and ("data" in text or "information" in text): score += 60
                elif text == "privacy": score += 50 # Exact match "privacy" gets decent score
                elif "privacy" in text: score += 30 # Contains "privacy" is lower score unless context
                elif "data protection" in text: score += 40

            # == URL Path/Query Scoring ==
            # Bonus for standard privacy paths (can stack with user/customer score)
            # Check if path *exactly* matches or *ends with* a known indicator
            path_matched_indicator = False
            for indicator in privacy_path_indicators:
                 if path == indicator or path.endswith(indicator):
                      score += 120 # Increased score for strong path match
                      logger.debug(f"URL path strongly matches privacy indicator '{indicator}': {path}")
                      path_matched_indicator = True
                      break # Stop after first strong match
            # Lower bonus if path just *contains* the indicator (less specific)
            if not path_matched_indicator and any(indicator in path for indicator in privacy_path_indicators):
                score += 60
                logger.debug(f"URL path contains privacy indicator: {path}")


            # Help center privacy pages (can stack) - Check path OR query
            is_help_center_link = any(help_pattern in path or help_pattern in query for help_pattern in help_center_patterns)
            if is_help_center_link:
                # Boost if privacy terms are also present
                if "privacy" in path or "privacy" in query or "privacy" in text:
                    score += 70
                    logger.debug(f"URL appears to be a help center privacy page: {abs_url}")
                else:
                    score += 20 # Small boost just for being a help link, might contain privacy info

            # Special case for parameter-based privacy pages (can stack) - Check query parameters case-insensitively
            query_params_lower = {k.lower(): v for k, v in urllib.parse.parse_qs(parsed_url.query).items()}
            if "nodeid" in query_params_lower or "helpid" in query_params_lower or "privacyprefs" in path:
                score += 60
                logger.debug(f"URL appears to use nodeId/helpId/privacyprefs parameter common in privacy pages: {abs_url}")

            # == Contextual Scoring ==
            # Rel attributes (can stack)
            if "privacy-policy" in rels: score += 100
            if "cookie-policy" in rels: score += 80 # Often linked near privacy policy

            # Footer link bonus (can stack)
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
            # Penalize deep paths (more than 4 segments), less penalty if it's a known help path
            if num_path_segments > 4 and not is_help_center_link:
                penalty = (num_path_segments - 4) * 15 # Slightly increased penalty per extra segment
                score -= penalty
                logger.debug(f"Applying path depth penalty: -{penalty} points for {num_path_segments} segments in {path}")

            # Penalize paths containing digits (potential IDs, less likely for top-level policy)
            # Be less harsh if it's a known help/nodeId pattern
            if contains_digits_in_path and not ("nodeid" in query_params_lower or "helpid" in query_params_lower):
                score -= 25 # Increased penalty
                logger.debug(f"Applying path digit penalty: -25 points for digits in {path}")

            # Penalize non-specific query parameters (but allow known privacy/help ones)
            known_query_params = ["nodeid", "helpid", "topic", "section", "articleid"] # Add more known safe params if needed
            has_unknown_query = query and not any(known_param in query for known_param in known_query_params)
            # Also check for common content ID parameters as penalty trigger
            has_content_id_param = any(key in query_params_lower for key in ["id", "post", "article", "pid", "storyid", "itemid", "pageid"])

            if has_unknown_query or has_content_id_param:
                 # Don't penalize if it's already identified as a help center link (they often have params)
                 if not is_help_center_link:
                      score -= 30 # Increased penalty
                      logger.debug(f"Applying penalty for potentially non-privacy query parameters: {query}")


            # Add to candidates if it scores a minimum threshold
            if score >= 50: # Minimum score threshold to be considered a candidate
                candidate_links.append({
                    "url": abs_url,
                    "score": score,
                    "text": text,
                    "path": path,
                    "query": query,
                    "is_footer": is_footer_link
                })
            # else:
                # logger.debug(f"Candidate score {score} too low, discarding: {abs_url} ('{text}')") # Optional: Verbose

        except Exception as e:
            # Log errors during URL processing but continue with other links
            logger.warning(f"Error processing link href='{href}' (resolved to '{abs_url if 'abs_url' in locals() else 'N/A'}') on base '{base_url}': {e}", exc_info=False) # exc_info=False for less noise
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
        logger.info(f"Found {len(candidate_links)} potential privacy policy link candidates after scoring")
        for i, candidate in enumerate(candidate_links[:5]): # Show top 5
            logger.debug(f"  Candidate #{i+1}: Score={candidate['score']}, URL={candidate['url']}, Text='{candidate['text']}'")

        # Use the highest-scoring candidate that meets a high confidence threshold
        best_candidate = candidate_links[0]

        # Final validation - MUST be a valid absolute URL that starts with http and meets score threshold
        # Increased confidence threshold slightly
        if (best_candidate["score"] >= 70 and # Confidence threshold
            isinstance(best_candidate["url"], str) and
            best_candidate["url"].lower().startswith(("http://", "https://"))):

            logger.info(f"Selected high-confidence privacy link: {best_candidate['url']} (Score: {best_candidate['score']})")

            # Final verification - check URL structure one more time for negative patterns (redundant but safe)
            final_url = best_candidate["url"]
            final_path = best_candidate["path"]

            if any(neg_pattern in final_path for neg_pattern in strong_negative_path_patterns):
                logger.warning(f"Final check rejected suspicious URL path: {final_url}")
                return "" # No suitable link found

            return final_url
        else:
            # Log why the best candidate wasn't selected
            log_reason = ""
            if best_candidate["score"] < 70:
                log_reason = f"score ({best_candidate['score']}) below threshold (70)"
            elif not best_candidate["url"].lower().startswith(("http://", "https://")):
                 log_reason = "not a valid absolute http(s) URL"
            else:
                 log_reason = "failed validation for unknown reason"

            logger.warning(f"Best candidate rejected: {best_candidate['url']} (Score: {best_candidate['score']}). Reason: {log_reason}")
            # Optional: Check second best candidate? For now, just return empty.
            return "" # Return empty if no high-confidence link found
    else:
        logger.info("No privacy link candidates found after filtering and scoring.")
        return "" # Return empty string if no candidates were found


def sanitize_url(url: str) -> str:
    """
    Sanitize and validate URLs.
    If the URL is severely malformed or clearly invalid, returns an empty string.
    Ensures the URL starts with http:// or https://.
    """
    if not url:
        logger.warning("Sanitize URL: Empty URL provided")
        return ""

    # Remove leading/trailing whitespace and control characters
    url = url.strip().strip('\r\n\t')
    if not url: # Check again after stripping
        logger.warning("Sanitize URL: URL became empty after stripping")
        return ""
    logger.debug(f"Sanitizing URL: {url}")

    try:
        # Ensure scheme is present
        if not re.match(r'^https?://', url, re.IGNORECASE):
             # Check if it looks like a domain name is missing the scheme
             # Avoid adding scheme to things starting with / or having no dots
             if '.' in url and not url.startswith('/'):
                 logger.debug(f"Prepending https:// to {url}")
                 url = 'https://' + url
             else:
                 # If it doesn't look like a domain (e.g., relative path), treat as invalid for this context
                 logger.warning(f"Sanitize URL: URL '{url}' lacks scheme and doesn't look like a domain. Treating as invalid.")
                 return "" # Treat as invalid if no scheme and not domain-like

        # Parse the URL
        parsed = urlparse(url)

        # Check for essential components: scheme and netloc (domain)
        # Scheme check is somewhat redundant due to logic above, but safe
        if not parsed.scheme or not parsed.netloc:
            logger.warning(f"Sanitize URL: Invalid scheme or netloc (domain) after parsing: {url}")
            return ""

        # Basic check for a valid-looking domain name (contains at least one dot)
        if '.' not in parsed.netloc:
             logger.warning(f"Sanitize URL: Netloc (domain) '{parsed.netloc}' lacks a dot: {url}")
             return ""

        # Check TLD (Top-Level Domain) basic validity (at least 2 chars) - allows flexibility
        domain_parts = parsed.netloc.split('.')
        if len(domain_parts) < 2 or len(domain_parts[-1]) < 1: # Allow single char TLDs just in case
              logger.warning(f"Sanitize URL: Domain '{parsed.netloc}' appears to lack a valid TLD structure: {url}")
              # Allow it to pass, as validation can be complex (e.g., localhost, IPs)
              # return ""

        logger.debug(f"Sanitized URL appears valid: {url}")
        return url
    except ValueError as e:
         # Catch specific parsing errors like invalid IPv6 addresses
         logger.error(f"Sanitize URL: Error parsing URL '{url}': {str(e)}")
         return ""
    except Exception as e:
        # Catch any other unexpected parsing errors
        logger.error(f"Sanitize URL: Unexpected error validating URL '{url}': {str(e)}")
        return ""


# --- FastAPI Route Example ---
# This route uses the functions above to find a privacy policy link for a given URL.

@router.post("/privacy",
             response_model=PrivacyResponse,
             summary="Find Privacy Policy Link",
             description="Fetches a given URL, parses its HTML, and attempts to find the privacy policy link.")
async def get_privacy_policy(request: PrivacyRequest):
    """
    Endpoint to find the privacy policy URL for a given website URL.

    - **request**: Contains the URL of the website to check.
    """
    # 1. Normalize and Sanitize Input URL
    # Use normalize first to add scheme if missing, then sanitize to validate fully
    normalized_url = normalize_url(request.url)
    if not normalized_url:
         logger.error(f"Input URL '{request.url}' could not be normalized.")
         raise HTTPException(status_code=400, detail="Invalid URL provided (failed normalization).")

    target_url = sanitize_url(normalized_url)
    if not target_url:
         logger.error(f"Normalized URL '{normalized_url}' failed sanitization.")
         raise HTTPException(status_code=400, detail="Invalid URL provided (failed sanitization).")

    # 2. Prepare HTTP Request
    # Use a randomly selected browser-like user agent
    headers = {
        "User-Agent": get_random_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    privacy_policy_url = ""
    error_message = None
    final_url_fetched = target_url # Keep track of the URL after redirects

    # 3. Fetch and Parse URL Content
    try:
        # Use httpx.AsyncClient for async requests
        # Follow redirects, set a reasonable timeout, pass headers
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
                 return PrivacyResponse(
                     url=request.url,
                     pp_url=None,
                     success=False,
                     message=error_message,
                     method_used="HTML parsing"
                 )

            # Use BeautifulSoup to parse the HTML content
            # Use 'lxml' for potentially faster parsing if installed, fallback to 'html.parser'
            try:
                soup = BeautifulSoup(response.text, 'lxml')
            except ImportError:
                logger.warning("lxml not found, falling back to html.parser.")
                soup = BeautifulSoup(response.text, 'html.parser')

            # 4. Find the privacy link using the dedicated function
            # Pass the final URL (after redirects) as the base URL for resolving relative links
            privacy_policy_url = find_privacy_link(soup, final_url_fetched)

            if not privacy_policy_url:
                logger.warning(f"Could not find a privacy policy link directly on {final_url_fetched}")
                # If no link found by parsing, set appropriate error message
                error_message = "Could not find a privacy policy link on the page."
            else:
                 # Validate the found URL before returning
                 validated_url = sanitize_url(privacy_policy_url)
                 if not validated_url:
                      logger.warning(f"Found privacy link '{privacy_policy_url}' failed sanitization. Discarding.")
                      error_message = "Found a potential link, but it failed validation."
                      privacy_policy_url = "" # Reset if invalid
                 else:
                      privacy_policy_url = validated_url # Use the sanitized version


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
        logger.error(f"An unexpected error occurred for {target_url}: {e}", exc_info=True) # Log traceback for unexpected errors
        error_message = f"An unexpected error occurred: {type(e).__name__}"

    # 5. Prepare and Return Response
    # If a link was found and validated, error_message should be None
    # If no link was found, error_message will be set accordingly
    # If an exception occurred, error_message will be set

    if not privacy_policy_url and not error_message:
         # This case might happen if find_privacy_link returns "" but no exception occurred
         error_message = "Privacy policy link not found after scanning the page."

    # Return the found URL or an error indication using the Pydantic response model
    return PrivacyResponse(
         url=request.url,  # Return the user's original input URL
         pp_url=privacy_policy_url or None,  # Return None if empty string
         success=bool(privacy_policy_url),  # True if we found a privacy policy URL
         message=error_message or "Privacy policy URL found successfully" if privacy_policy_url else "No privacy policy URL found",
         method_used="HTML parsing"
     )

# Export get_privacy_policy as find_privacy_policy for backward compatibility
find_privacy_policy = get_privacy_policy