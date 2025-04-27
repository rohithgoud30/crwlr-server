import random
from urllib.parse import urlparse, urljoin, parse_qs
import re
import traceback
import time
import logging
from fastapi import APIRouter, HTTPException, status, Depends
from playwright.async_api import async_playwright
from typing import Optional, List
from fake_useragent import UserAgent
import platform

from app.models.tos import ToSRequest, ToSResponse
from app.models.privacy import PrivacyRequest, PrivacyResponse
from app.api.v1.endpoints.privacy import find_privacy_policy

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize UserAgent for random browser User-Agent strings
ua_generator = UserAgent()

# Function to get a random user agent
def get_random_user_agent():
    """
    Returns a random, realistic user agent string from the fake-useragent library.
    Falls back to a default value if the API fails.
    """
    try:
        return ua_generator.random
    except Exception as e:
        # Fallback user agents in case the API fails
        fallback_user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        ]
        logger.error(f"Error getting random user agent: {e}. Using fallback.")
        return random.choice(fallback_user_agents)

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

def sanitize_url(url: str) -> str:
    """
    Sanitize and validate URLs to ensure they are valid.
    
    If the URL is severely malformed or clearly invalid, returns an empty string
    instead of attempting to fix it.
    """
    if not url:
        print("Empty URL provided")
        return ""
        
    # Trim whitespace and control characters
    url = url.strip().strip('\r\n\t')
    
    # Log the original URL for debugging
    print(f"Validating URL: {url}")
    
    try:
        # Fix only the most common minor issues
        # Add protocol if missing
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        
        # Validate the URL structure
        parsed = urlparse(url)
        
        # Check for severely malformed URLs
        if not parsed.netloc or '.' not in parsed.netloc:
            print(f"Invalid domain in URL: {url}")
            return ""
            
        # Check for nonsensical URL patterns that indicate a malformed URL
        if re.match(r'https?://[a-z]+s?://', url):
            # Invalid patterns like https://ttps://
            print(f"Malformed URL with invalid protocol pattern: {url}")
            return ""
            
        # Additional validation to ensure domain has a valid TLD
        domain_parts = parsed.netloc.split('.')
        if len(domain_parts) < 2 or len(domain_parts[-1]) < 2:
            print(f"Domain lacks valid TLD: {url}")
            return ""
            
        print(f"URL validated: {url}")
        return url
    except Exception as e:
        print(f"Error validating URL {url}: {str(e)}")
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

@router.post("/tos", response_model=ToSResponse, status_code=status.HTTP_200_OK)
async def find_tos(request: ToSRequest) -> ToSResponse:
    """
    Find the Terms of Service URL for a given website.
    """
    logger.info(f"Finding ToS for URL: {request.url}")
    url = sanitize_url(request.url)
    
    # Fast validation
    if not url:
        logger.warning(f"Invalid URL in request: {request.url}")
        return ToSResponse(url=request.url, success=False, tos_url=None, error="Invalid URL")
    
    # Normalize the domain for later comparisons
    try:
        domain = normalize_domain(url)
        logger.info(f"Normalized domain: {domain}")
    except Exception as e:
        domain = None
        logger.warning(f"Error normalizing domain: {e}")
    
    # Check if it's an app store URL
    is_app = is_app_store_url(url) or is_play_store_url(url)
    if is_app:
        logger.info(f"Detected App/Play Store URL: {url}")
        
        # First use privacy endpoint to get privacy policy URL
        privacy_request = PrivacyRequest(url=url)
        privacy_response = await find_privacy_policy(privacy_request)
        
        if privacy_response and privacy_response.pp_url:
            # Extract base domain from privacy URL
            logger.info(f"Found privacy policy from store: {privacy_response.pp_url}")
            parsed_url = urlparse(privacy_response.pp_url)
            
            # Construct the base URL (scheme + domain)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            logger.info(f"Extracted base URL from privacy policy: {base_url}")
            
            # Create a new request with the base URL
            base_request = ToSRequest(url=base_url)
            logger.info(f"Making recursive call to find_tos with base URL: {base_url}")
            
            # Make recursive call to find_tos with the base URL - no need for from_store parameter
            base_url_response = await find_tos(base_request)
            
            if base_url_response and base_url_response.tos_url:
                # Update the original URL in the response
                base_url_response.url = url
                base_url_response.method_used = f"store_base_url_{base_url_response.method_used}"
                base_url_response.message = f"Terms of Service found via App/Play Store domain: {base_url}"
                logger.info(f"Found Terms of Service via base URL: {base_url_response.tos_url}")
                return base_url_response
            else:
                # If base URL approach failed, return clear failure for app store URLs
                logger.warning(f"Could not find Terms of Service using base URL: {base_url}")
                return ToSResponse(
                    url=url,
                    tos_url=None,
                    success=False,
                    message=f"Could not find Terms of Service for {base_url}",
                    method_used="store_base_url_failed"
                )
        else:
            # No privacy policy found for app store URL, return failure
            logger.warning(f"No privacy policy found for App/Play Store URL: {url}")
            return ToSResponse(
                url=url,
                tos_url=None,
                success=False,
                message="Could not find Privacy Policy for App/Play Store URL",
                method_used="app_store_no_privacy_policy"
            )
    
    playwright = None
    browser = None
    browser_context = None
    page = None
    
    try:
        # Try to setup browser if needed for subsequent operations
        playwright = await async_playwright().start()
        browser, browser_context, page, _ = await setup_browser(playwright)
        
        # Navigate to the URL - notice we don't exit but continue with recovery methods
        success, _, _ = await navigate_with_retry(page, url)
        if not success:
            logger.warning(f"Main site navigation had issues, but trying to analyze current page...")
            
            # We don't exit here with failure, we instead try other methods
            # Try JS scanning approaches, then search engine fallbacks
            
            # First check for anti-bot patterns to log the reason
            anti_bot_detected = await detect_anti_bot_patterns(page)
            if anti_bot_detected:
                logger.warning(f"Anti-bot protection detected. Will attempt search fallbacks.")
            
            # Try our search engine fallbacks instead of directly returning failure
            unverified_result = None  # Will hold our best guess if we find one
            
            # Initialize a fresh page if possible for search fallbacks
            try:
                await page.close()
                page = await browser_context.new_page()
            except Exception as e:
                logger.warning(f"Error creating new page for search fallbacks: {e}")
            
            # Domain-focused search approach
            try:
                # Extract clean domain name for search
                domain = normalize_domain(url)
                domain_name = domain.replace("www.", "")
                
                # Construct a proper search query
                search_query = f"{domain_name} terms of service, terms of use, user agreement, legal terms"
                logger.info(f"Using search query: {search_query}")
                
                # Try duckduckgo search first
                logger.info(f"Trying DuckDuckGo search fallback for {domain_name}")
                duck_result = await duckduckgo_search_fallback(search_query, page)
                
                if duck_result:
                    logger.info(f"Found terms of service via DuckDuckGo search: {duck_result}")
                    return ToSResponse(
                        url=url,
                        tos_url=duck_result,
                        success=True,
                        message="Terms of Service found via DuckDuckGo search (after navigation issues)",
                        method_used="duckduckgo_search_fallback"
                    )
                
                # Try Yahoo search next
                logger.info(f"Trying Yahoo search fallback for {domain_name}")
                yahoo_result = await yahoo_search_fallback(search_query, page)
                
                if yahoo_result:
                    logger.info(f"Found terms of service via Yahoo search: {yahoo_result}")
                    return ToSResponse(
                        url=url,
                        tos_url=yahoo_result,
                        success=True,
                        message="Terms of Service found via Yahoo search (after navigation issues)",
                        method_used="yahoo_search_fallback"
                    )
                
                # Try Bing search as last resort
                logger.info(f"Trying Bing search fallback for {domain_name}")
                bing_result = await bing_search_fallback(search_query, page)
                
                if bing_result:
                    logger.info(f"Found terms of service via Bing search: {bing_result}")
                    return ToSResponse(
                        url=url,
                        tos_url=bing_result,
                        success=True,
                        message="Terms of Service found via Bing search (after navigation issues)",
                        method_used="bing_search_fallback"
                    )
                
                # All search methods failed, return failure
                logger.warning(f"All search fallbacks failed for {domain_name}")
                return ToSResponse(
                    url=url,
                    tos_url=None,
                    success=False,
                    message="Navigation failed and all search fallbacks exhausted",
                    method_used="all_search_failed"
                )
                
            except Exception as e:
                logger.error(f"Error during search fallbacks: {e}")
                return ToSResponse(
                    url=url,
                    tos_url=None,
                    success=False,
                    message=f"Navigation failed and search fallbacks encountered error: {str(e)}",
                    method_used="search_fallback_error"
                )
                
        # Successfully navigated to the page, now analyze it for ToS links
        logger.info("Successfully navigated to page, analyzing...")
        
        # Check if there are "user" or "customer" specific terms links that we should prioritize
        try:
            user_agreement_url = await find_user_customer_terms_links(page)
            if user_agreement_url:
                if is_likely_user_generated_content(user_agreement_url):
                    logger.warning(f"Found user agreement URL appears to be user-generated content: {user_agreement_url}")
                    # Don't return this result, continue to other methods
                else:
                    logger.info(f"Found high-priority user/customer terms: {user_agreement_url}")
                    # Return immediately if found - priority link
                    return ToSResponse(
                        url=url,
                        tos_url=user_agreement_url,
                        success=True,
                        message="User agreement/terms found with high priority",
                        method_used="user_customer_terms"
                    )
        except Exception as e:
            logger.error(f"Error finding user/customer terms: {e}")
        
        # Try more comprehensive link scanning
        logger.info("Scanning for ToS links...")
        unverified_result = None
        
        # Step a: Try JS-based scanning first (key change - prioritize JS methods)
        try:
            tos_url = await find_all_links_js(page, browser_context)
            if tos_url:
                if is_likely_user_generated_content(tos_url):
                    logger.warning(f"Found ToS URL appears to be user-generated content: {tos_url}")
                    # Save as unverified but continue looking for better results
                    unverified_result = tos_url
                else:
                    logger.info(f"Found ToS via JS-based link scanning: {tos_url}")
                    # Return as final result without further validation
                    return ToSResponse(
                        url=url,
                        tos_url=tos_url,
                        success=True,
                        message="Terms of Service found via JS-based link scanning",
                        method_used="js_link_scanning"
                    )
        except Exception as e:
            logger.error(f"Error during JS-based link scanning: {e}")

        # Step b: Try matching links based on text
        try:
            tos_url = await find_matching_link(page, browser_context, unverified_result)
            if tos_url:
                if is_likely_user_generated_content(tos_url):
                    logger.warning(f"Found ToS URL appears to be user-generated content: {tos_url}")
                    # Save as unverified but continue looking for better results
                    if not unverified_result:
                        unverified_result = tos_url
                else:
                    logger.info(f"Found ToS via text matching: {tos_url}")
                    # Return as final result without further validation
                    return ToSResponse(
                        url=url,
                        tos_url=tos_url,
                        success=True,
                        message="Terms of Service found via text match scanning",
                        method_used="text_matching"
                    )
        except Exception as e:
            logger.error(f"Error during text matching: {e}")
        
        # If navigation failed or no TOS found with direct methods, try search fallbacks
        domain = normalize_domain(url)
        logger.info(f"Trying search engine fallbacks for domain: {domain}")
        
        # Construct a proper search query
        search_query = f"{domain} terms of service, terms of use, user agreement, legal terms"
        logger.info(f"Using search query: {search_query}")
        
        # Use the standard search approach across search engines
        try:
            logger.info("Running standard search using search engines...")
            tos_url, search_results, consensus_links = await standard_search_fallback(search_query, page)
            
            if tos_url:
                # Validate the ToS URL isn't a user content page
                if is_likely_user_generated_content(tos_url):
                    logger.warning(f"Search result appears to be user-generated content: {tos_url}")
                    # Continue to other methods
                else:
                    # Get engines used for better logging
                    engines_found = list(search_results.keys())
                    engines_msg = f"Found by search engines: {', '.join(engines_found)}" if engines_found else ""
                    
                    logger.info(f"Found ToS via search: {tos_url} {engines_msg}")
                    # Return as final result without further validation
                    return ToSResponse(
                        url=url,
                        tos_url=tos_url,
                        success=True,
                        message=f"Terms of Service found via search {engines_msg}",
                        method_used="search_engine"
                    )
        except Exception as e:
            logger.warning(f"Search failed: {e}")
        
        # Step 3: Try analysis of the landing page itself
        try:
            tos_url = await analyze_landing_page(page, browser_context, unverified_result)
            if tos_url:
                if is_likely_user_generated_content(tos_url):
                    logger.warning(f"Found ToS URL appears to be user-generated content: {tos_url}")
                    # Save as unverified but continue looking for better results
                    if not unverified_result:
                        unverified_result = tos_url
                else:
                    logger.info(f"Found ToS via landing page analysis: {tos_url}")
                    return ToSResponse(
                        url=url,
                        tos_url=tos_url,
                        success=True,
                        message="Terms of Service found via landing page analysis",
                        method_used="landing_page_analysis"
                    )
        except Exception as e:
            logger.error(f"Error during landing page analysis: {e}")
        
        # Step 4: If a verified privacy policy page exists, try to find ToS from there
        try:
            privacy_request = PrivacyRequest(url=url)
            privacy_response = await find_privacy_policy(privacy_request)
            
            if privacy_response and privacy_response.pp_url:
                logger.info(f"Found privacy policy: {privacy_response.pp_url}. Checking for ToS link...")
                
                # Navigate to the privacy policy
                pp_success, _, _ = await navigate_with_retry(page, privacy_response.pp_url)
                
                if pp_success:
                    # Look for ToS link on the privacy policy page
                    tos_url = await find_tos_via_privacy_policy(page, browser_context)
                    
                    if tos_url:
                        if is_likely_user_generated_content(tos_url):
                            logger.warning(f"ToS URL from privacy policy appears to be user content: {tos_url}")
                            # Save as unverified but continue looking for better results
                            if not unverified_result:
                                unverified_result = tos_url
                        else:
                            logger.info(f"Found ToS via privacy policy: {tos_url}")
                            return ToSResponse(
                                url=url,
                                tos_url=tos_url,
                                success=True,
                                message="Terms of Service found from privacy policy page",
                                method_used="via_privacy_policy"
                            )
        except Exception as e:
            logger.error(f"Error finding ToS via privacy policy: {e}")
        
        # Step 5: If we have an unverified result but no better options, return it
        if unverified_result:
            logger.info(f"Using unverified ToS URL as fallback: {unverified_result}")
            return ToSResponse(
                url=url,
                tos_url=unverified_result,
                success=True,
                message="Terms of Service found (unverified, possible user content)",
                method_used="unverified_result"
            )
        
        # All methods failed
        return ToSResponse(
            url=url,
            tos_url=None,
            success=False,
            message="Could not find Terms of Service via any method",
            method_used="all_methods_failed"
        )
    except Exception as e:
        logger.error(f"Error during browser automation: {e}")
        return handle_error(url, None, str(e))
    finally:
        # Always ensure browser resources are cleaned up to prevent leaks
        try:
            if page:
                try:
                    await page.close()
                except Exception as e:
                    logger.error(f"Error closing page: {e}")
                    
            if browser_context:
                try:
                    await browser_context.close()
                except Exception as e:
                    logger.error(f"Error closing browser context: {e}")
                    
            if browser:
                try:
                    await browser.close()
                except Exception as e:
                    logger.error(f"Error closing browser: {e}")
                    
            if playwright:
                try:
                    await playwright.stop()
                except Exception as e:
                    logger.error(f"Error stopping playwright: {e}")
        except Exception as cleanup_error:
            logger.error(f"Error during browser cleanup: {cleanup_error}")


async def setup_browser(playwright=None):
    """
    Setup browser with optimized settings to avoid detection and cloudflare protection.
    Returns browser, context, and page objects.
    """
    try:
        # Initialize playwright if not provided
        if not playwright:
            import playwright.async_api
            playwright = await playwright.async_api.async_playwright().start()
        
        # Determine platform and setup appropriate user agent
        system = platform.system()
        if system == "Darwin":
            platform_string = "Macintosh; Intel Mac OS X"
        elif system == "Windows":
            platform_string = "Windows NT 10.0; Win64; x64"
        else:
            platform_string = "Linux"
            
        # Determine if mobile
        user_agent = get_random_user_agent()
        is_mobile = "Mobile" in user_agent or "Android" in user_agent

        # Get headless setting - use True for production deployment
        headless = True  # Changed from False to True for stability
        # Log the setting 
        print(f"Browser headless mode: {headless}")

        # Launch browser with optimized settings for bot-detection evasion
        browser = await playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1366,768",
                "--disable-automation",
            ],
            chromium_sandbox=False,
            slow_mo=random.randint(10, 30),  # Randomized slight delay for more human-like behavior
        )

        # Create context with human-like settings and consistent client hints
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=user_agent,
            locale="en-US",
            timezone_id="America/New_York",
            device_scale_factor=1,
            is_mobile=is_mobile,
            has_touch=is_mobile,
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Upgrade-Insecure-Requests": "1",
                "Connection": "keep-alive",
            },
        )

        # Add comprehensive stealth script to override navigator properties
        await context.add_init_script(
            """
            () => {
                // Override webdriver property
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                
                // Add fake plugins for more human-like fingerprint
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [
                        {
                            name: 'Chrome PDF Plugin',
                            description: 'Portable Document Format',
                            filename: 'internal-pdf-viewer',
                            length: 1
                        },
                        {
                            name: 'Chrome PDF Viewer',
                            description: '',
                            filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai',
                            length: 1
                        },
                        {
                            name: 'Native Client',
                            description: '',
                            filename: 'internal-nacl-plugin',
                            length: 1
                        }
                    ]
                });
                
                // Fix languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en']
                });
                
                // Hide automation-related properties
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
                );
                
                // Add chrome object if not present
                if (window.chrome === undefined) {
                    window.chrome = {
                        runtime: {},
                        loadTimes: function() {},
                        app: {},
                        csi: function() {},
                    };
                }
                
                // Prevent iframe detection technique
                try {
                    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
                        get: function() {
                            return window;
                        }
                    });
                } catch (e) {}
            }
            """
        )

        # Create a page
        page = await context.new_page()

        # Random delay function with variable timing
        async def random_delay(min_ms=100, max_ms=300):
            # More variable delay than the original
            delay = random.randint(min_ms, max_ms)
            await page.wait_for_timeout(delay)

        # Set reasonable timeouts - not too short, which would indicate automation
        page.set_default_timeout(15000)  # 15 seconds is more human-like

        return browser, context, page, random_delay

    except Exception as e:
        print(f"Error setting up browser: {e}")
        # Cleanup resources in case of error
        if "browser" in locals() and browser:
            try:
                await browser.close()
            except Exception as close_err:
                print(f"Error closing browser: {close_err}")
        
        if "playwright" in locals() and playwright and not playwright:
            try:
                await playwright.stop()
            except Exception as stop_err:
                print(f"Error stopping playwright: {stop_err}")
                
        raise


async def navigate_with_retry(page, url, max_retries=2):
    """
    Navigate to URL with optimized retry logic and human-like behaviors to avoid bot detection.
    """
    for attempt in range(max_retries):
        try:
            # Add varying delay between attempts to appear more human-like
            if attempt > 0:
                delay = random.randint(500, 1500)  # More human-like pause between retries
                logger.info(f"Waiting {delay/1000}s before retry {attempt+1}...")
                await page.wait_for_timeout(delay)

            logger.info(f"Navigation attempt {attempt+1}/{max_retries} to {url}")

            # Simulate human pre-navigation behavior
            if attempt == 0:
                # Sometimes move the mouse around first (simulating human pre-click behavior)
                if random.random() < 0.7:  # 70% chance
                    x, y = random.randint(100, 700), random.randint(100, 500)
                    await page.mouse.move(x, y, steps=random.randint(3, 7))
                    
                # Occasionally pause like a human would
                if random.random() < 0.3:  # 30% chance
                    await page.wait_for_timeout(random.randint(200, 800))

            # Use a more human-like navigation approach
            response = await page.goto(
                url, 
                timeout=15000,  # Longer timeout like a human would have
                wait_until=random.choice(["domcontentloaded", "networkidle"])  # Vary the navigation completion criteria
            )
            
            # After navigation, perform some natural human-like behaviors
            await page.wait_for_timeout(random.randint(300, 700))  # Pause briefly after page loads
            
            # Occasionally resize window slightly (human behavior)
            if random.random() < 0.2:  # 20% chance
                current_viewport = await page.evaluate("""() => { 
                    return { width: window.innerWidth, height: window.innerHeight } 
                }""")
                new_width = current_viewport['width'] + random.randint(-20, 20)
                new_height = current_viewport['height'] + random.randint(-10, 10)
                await page.set_viewport_size({"width": new_width, "height": new_height})
            
            # Add a small scroll after loading
            if random.random() < 0.8:  # 80% chance
                # Scroll down slightly first like a human scanning the page
                scroll_y = random.randint(100, 300)
                await page.evaluate(f"window.scrollBy(0, {scroll_y})")
                # Brief pause after scrolling
                await page.wait_for_timeout(random.randint(70, 150))

            # Check for anti-bot measures
            is_anti_bot, patterns = await detect_anti_bot_patterns(page)
            if is_anti_bot:
                if attempt < max_retries - 1:
                    logger.warning(f"Detected anti-bot protection, trying alternative approach...")
                    
                    # If we encounter anti-bot protection, try to scroll naturally and wait
                    # This sometimes helps with Cloudflare and similar protections
                    await page.evaluate("""() => {
                        const scrollDown = () => {
                            window.scrollBy(0, Math.floor(Math.random() * 30) + 5);
                        };
                        for (let i = 0; i < 20; i++) {
                            setTimeout(scrollDown, i * (Math.floor(Math.random() * 20) + 5));
                        }
                    }""")
                    await page.wait_for_timeout(3000)  # Wait for potential verification to complete
                    continue
                else:
                    logger.warning("All navigation attempts blocked by anti-bot protection")
                    return False, response, patterns

            # Check HTTP status
            if response.ok:
                logger.info(f"Navigation successful: HTTP {response.status}")
                return True, response, []
            else:
                logger.warning(f"Received HTTP {response.status}")
        except Exception as e:
            logger.error(f"Navigation error: {e}")

    logger.warning("All navigation attempts failed")
    return False, None, []


async def detect_anti_bot_patterns(page):
    """
    Advanced anti-bot detection with comprehensive checks for various protection systems.
    Detects Cloudflare, reCAPTCHA, hCaptcha, DataDome, PerimeterX and other common anti-bot systems.
    """
    try:
        # Comprehensive check looking for multiple anti-bot protection systems
        anti_bot_detection = await page.evaluate(
            """() => {
                // Helper function to check if an element exists but is potentially hidden from simple queries
                const elementExists = (selector) => {
                    try {
                        return document.querySelector(selector) !== null;
                    } catch (e) {
                        return false;
                    }
                };
                
                // Helper to check if text appears in page source
                const sourceContains = (text) => {
                    const html = document.documentElement.outerHTML.toLowerCase();
                    return html.includes(text);
                };
                
                // Check for common bot detection systems
                const results = {
                    isAntiBot: false,
                    detections: [],
                url: window.location.href,
                title: document.title
            };
                
                // 1. Check for Cloudflare
                if (
                    sourceContains('cloudflare') && 
                    (sourceContains('security check') || sourceContains('challenge') || sourceContains('jschl-answer'))
                ) {
                    results.isAntiBot = true;
                    results.detections.push('cloudflare');
                }
                
                // 2. Check for reCAPTCHA 
                if (
                    elementExists('.g-recaptcha') || 
                    elementExists('iframe[src*="recaptcha"]') ||
                    sourceContains('grecaptcha') ||
                    window.grecaptcha
                ) {
                    results.isAntiBot = true;
                    results.detections.push('recaptcha');
                }
                
                // 3. Check for hCaptcha
                if (
                    elementExists('.h-captcha') || 
                    elementExists('iframe[src*="hcaptcha"]') ||
                    sourceContains('hcaptcha') ||
                    window.hcaptcha
                ) {
                    results.isAntiBot = true;
                    results.detections.push('hcaptcha');
                }
                
                // 4. Check for DataDome
                if (
                    sourceContains('datadome') || 
                    window._dd_s ||
                    document.cookie.includes('datadome')
                ) {
                    results.isAntiBot = true;
                    results.detections.push('datadome');
                }
                
                // 5. Check for PerimeterX
                if (
                    sourceContains('perimeterx') || 
                    sourceContains('px-captcha') ||
                    document.cookie.includes('_px')
                ) {
                    results.isAntiBot = true;
                    results.detections.push('perimeterx');
                }
                
                // 6. Check for Akamai Bot Manager
                if (
                    sourceContains('akamai') && 
                    sourceContains('bot') &&
                    document.cookie.includes('ak_bmsc')
                ) {
                    results.isAntiBot = true;
                    results.detections.push('akamai');
                }
                
                // 7. Check for common WAFs (Web Application Firewalls)
                if (sourceContains('waf') && (sourceContains('block') || sourceContains('denied'))) {
                    results.isAntiBot = true;
                    results.detections.push('waf');
                }
                
                // 8. Check for IP blocking messages
                if (
                    sourceContains('access denied') || 
                    sourceContains('blocked') || 
                    sourceContains('your ip has been') ||
                    sourceContains('automated access')
                ) {
                    results.isAntiBot = true;
                    results.detections.push('ip_block');
                }
                
                // 9. Check for rate limiting
                if (
                    sourceContains('rate limit') || 
                    sourceContains('too many requests') || 
                    sourceContains('try again later')
                ) {
                    results.isAntiBot = true;
                    results.detections.push('rate_limit');
                }
                
                // 10. Check for common fingerprinting scripts
                if (
                    sourceContains('fingerprintjs') || 
                    sourceContains('tmx.js') ||
                    (window.document.FingerprintJS || window.fingerprintjs)
                ) {
                    // This alone doesn't mean we're blocked, but tracking is present
                    results.detections.push('fingerprinting');
                }
                
                // 11. Check for suspicious redirections indicating challenge pages
                if (document.location.pathname.includes('/cdn-cgi/') && sourceContains('challenge')) {
                    results.isAntiBot = true;
                    results.detections.push('cdn_challenge');
                }
                
                // 12. Check for unusual layout that might indicate a challenge page
                if (document.body && document.body.children.length < 5 && document.querySelectorAll('iframe').length > 0) {
                    // Very simple page with iframes often indicates a challenge
                    if (!results.isAntiBot) {
                        results.detections.push('suspicious_layout');
                    }
                }
                
                // Check if the page has JS challenges or timers (common in anti-bot systems)
                const hasJSChallenge = sourceContains('challenge') && sourceContains('timeout');
                if (hasJSChallenge) {
                    results.isAntiBot = true;
                    results.detections.push('js_challenge');
                }
                
                // Get meta information about the page for better analysis
                results.contentLength = document.documentElement.innerHTML.length;
                results.numElements = document.getElementsByTagName('*').length;
                results.hasRecaptchaAPI = typeof window.grecaptcha !== 'undefined';
                
                return results;
    }"""
        )

        # Process detection results
        if anti_bot_detection["isAntiBot"]:
            logger.warning(f"Detected anti-bot protection: {', '.join(anti_bot_detection['detections'])}")
            logger.info(f"Anti-bot page URL: {anti_bot_detection['url']}")
            logger.info(f"Anti-bot page title: {anti_bot_detection['title']}")
            
            # Check for specific anti-bot systems to handle specially
            patterns = anti_bot_detection.get("detections", [])
            
            # If Cloudflare detected, we might need special handling
            if "cloudflare" in patterns:
                logger.warning("Cloudflare protection detected - may need to wait longer")
            
            # If CAPTCHA detected, might need human intervention
            if "recaptcha" in patterns or "hcaptcha" in patterns:
                logger.warning("CAPTCHA detected - may require manual solving")
            
            return True, patterns
        
        # Additional browser/environment fingerprinting detection
        fingerprinting_detected = await page.evaluate("""() => {
            const detections = [];
            
            // Check for canvas fingerprinting
            const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
            let canvasFingerprinting = false;
            HTMLCanvasElement.prototype.toDataURL = function(type) {
                canvasFingerprinting = true;
                return originalToDataURL.apply(this, arguments);
            };
            
            // Force a single execution of any queued fingerprinting
            setTimeout(() => {}, 50);
            
            // Check for WebRTC IP detection
            if (window.RTCPeerConnection || window.webkitRTCPeerConnection) {
                detections.push('webrtc_detection');
            }
            
            // Check for navigator fingerprinting
            const navigatorProps = [
                'userAgent', 'appVersion', 'platform', 'language', 'languages',
                'hardwareConcurrency', 'deviceMemory', 'vendor', 'appName',
                'plugins', 'mimeTypes'
            ];
            
            const navigatorAccessed = navigatorProps.filter(prop => {
                try {
                    if (navigator[prop]) return true;
                } catch(e) {
                    return false;
                }
            });
            
            if (navigatorAccessed.length > 5) {
                detections.push('navigator_profiling');
            }
            
            // Restore original canvas function
            HTMLCanvasElement.prototype.toDataURL = originalToDataURL;
            
            if (canvasFingerprinting) {
                detections.push('canvas_fingerprinting');
            }
            
            return detections;
        }""")
        
        # If fingerprinting detected but no anti-bot yet, this is a hint but not conclusive
        if fingerprinting_detected and len(fingerprinting_detected) > 0:
            logger.info(f"Detected fingerprinting techniques: {', '.join(fingerprinting_detected)}")
            # Only mark as anti-bot if we have multiple aggressive fingerprinting techniques
            if len(fingerprinting_detected) >= 2:
                return True, fingerprinting_detected

        return False, []
    except Exception as e:
        logger.error(f"Error detecting anti-bot patterns: {e}")
        return False, []


async def find_all_links_js(page, context, unverified_result=None):
    """
    Use JavaScript to extract all links from the page that might be ToS links.
    Also detects if there's anti-bot protection.
    """
    try:
        print("\n=== Starting find_all_links_js ===")
        print("Searching for all links using JavaScript...")
        # Get the base domain for comparison
        current_url = await page.evaluate("() => window.location.href")
        parsed_url = urlparse(current_url)
        base_domain = parsed_url.netloc
        print(f"Base domain: {base_domain}")
        
        # Check if we're on Apple or Google domains
        is_apple_domain = "apple.com" in base_domain
        is_google_domain = "google.com" in base_domain or "play.google.com" in base_domain
        
        # Anti-bot protection test
        anti_bot = await detect_anti_bot_patterns(page)
        
        # If we're looking at a developer site that was navigated from an App/Play store
        # and we detect anti-bot protection, we should check if the links found are from Apple/Google
        # and filter them out if they are
        # Make sure context is a dictionary before trying to access it
        developer_site_from_store = False
        came_from_app_store = False
        came_from_play_store = False
        
        if isinstance(context, dict):
            developer_site_from_store = context.get("came_from_app_store") or context.get("came_from_play_store")
            came_from_app_store = context.get("came_from_app_store", False)
            came_from_play_store = context.get("came_from_play_store", False)
        
        # Different approaches based on anti-bot presence
        if anti_bot:
            print("Detected anti-bot protection, looking for footer links specifically...")
            
            # With anti-bot, focus on footer links which are most likely to contain ToS
            footer_links = await page.evaluate("""
                () => {
                    const footers = document.querySelectorAll('footer, [id*="foot"], [class*="foot"]');
                    const links = [];
                    
                    footers.forEach(footer => {
                        const footerLinks = footer.querySelectorAll('a[href]');
                        footerLinks.forEach(link => {
                            if (link.href && link.href.trim() !== '' && !link.href.startsWith('javascript:')) {
                                // Get link text or closest heading
                                let text = link.textContent.trim();
                                if (!text) {
                                    const closestHeading = link.closest('h1, h2, h3, h4, h5, h6');
                                    if (closestHeading) {
                                        text = closestHeading.textContent.trim();
                                    }
                                }
                                
                                links.push({
                                    href: link.href,
                                    text: text || "No text available"
                                });
                            }
                        });
                    });
                    
                    return links;
                }
            """)

            if footer_links and len(footer_links) > 0:
                print(f"Found {len(footer_links)} potential links in footer despite anti-bot protection")
                
                # Filter out links from Apple/Google domains if we're on a developer site navigated from store
                filtered_links = []
                for idx, link in enumerate(footer_links):
                    link_url = link.get('href', '')
                    link_text = link.get('text', '')
                    
                    # Skip links from Apple/Google domains if we're on a developer site
                    if developer_site_from_store:
                        link_domain = urlparse(link_url).netloc
                        if (is_apple_domain or "apple.com" in link_domain) and came_from_app_store:
                            print(f"Skipping Apple domain link: {link_text} - {link_url}")
                            continue
                        if (is_google_domain or "google.com" in link_domain or "play.google.com" in link_domain) and came_from_play_store:
                            print(f"Skipping Google domain link: {link_text} - {link_url}")
                            continue
                    
                    # Always skip Apple/Google domains if we're on their domains
                    if is_apple_domain and "apple.com" in link_url:
                        print(f"Skipping Apple domain link (on Apple domain): {link_text} - {link_url}")
                        continue
                    if is_google_domain and ("google.com" in link_url or "play.google.com" in link_url):
                        print(f"Skipping Google domain link (on Google domain): {link_text} - {link_url}")
                        continue
                    
                    # Calculate a score based on how likely this is a ToS link
                    score = 0
                    link_text_lower = link_text.lower()
                    link_url_lower = link_url.lower()
                    
                    # Highest priority: exact matches for user/customer terms
                    if "user agreement" in link_text_lower:
                        score += 150
                    if "customer agreement" in link_text_lower:
                        score += 150
                    if "user terms" in link_text_lower:
                        score += 140
                    if "customer terms" in link_text_lower:
                        score += 140
                    if "terms of use" in link_text_lower:
                        score += 140
                    if "terms of service" in link_text_lower:
                        score += 130
                    if "terms and conditions" in link_text_lower:
                        score += 120
                    if "terms & conditions" in link_text_lower:
                        score += 120
                    if "conditions of use" in link_text_lower:
                        score += 110
                    if "legal terms" in link_text_lower:
                        score += 100
                    
                    # Medium priority: partial matches
                    if "terms" in link_text_lower:
                        score += 90
                    if "legal" in link_text_lower:
                        score += 80
                    if "agreement" in link_text_lower:
                        score += 70
                    if "conditions" in link_text_lower:
                        score += 60
                    
                    # URL patterns
                    if "terms-of-service" in link_url_lower or "tos" in link_url_lower:
                        score += 50
                    if "terms-of-use" in link_url_lower or "tou" in link_url_lower:
                        score += 50
                    if "terms-and-conditions" in link_url_lower:
                        score += 40
                    if "legal/terms" in link_url_lower:
                        score += 40
                    if "agreement" in link_url_lower:
                        score += 30
                    
                    # If this is a good candidate, add it to our filtered list
                    if score > 0:
                        filtered_links.append({
                            "link": link,
                            "score": score
                        })
                        print(f"Footer link #{idx+1}: {link_text} - {link_url} (Score: {score})")
                
                # Sort by score (highest first)
                filtered_links.sort(key=lambda x: x["score"], reverse=True)
                
                # If we found good candidates
                if filtered_links:
                    best_link = filtered_links[0]["link"]
                    best_score = filtered_links[0]["score"]
                    
                    if best_score >= 100:
                        print(f"✅ Found high-score footer link with ToS-related title: {best_link['href']}")
                        return best_link['href'], page, unverified_result
                    elif filtered_links and len(filtered_links) > 0:
                        # Return the best link we found, even if score isn't super high
                        print(f"👍 Found potential footer link: {best_link['href']} (Score: {best_score})")
                        return best_link['href'], page, unverified_result
                else:
                    print("No relevant links found in footer")
            else:
                print("No footer links found")
        
        # If no anti-bot or no good footer links found, try standard method
        links_data = await page.evaluate("""
            () => {
                // Get all links on the page
                const allLinks = Array.from(document.querySelectorAll('a[href]'));
                const links = [];
                
                allLinks.forEach(link => {
                    if (link.href && link.href.trim() !== '' && 
                        !link.href.startsWith('javascript:') && 
                        !link.href.includes('mailto:') &&
                        !link.href.includes('tel:')) {
                        
                        // Get link text
                        let text = link.textContent.trim();
                        if (!text) {
                            // If no text, check for aria-label or title
                            text = link.getAttribute('aria-label') || link.getAttribute('title') || '';
                        }
                        
                        links.push({
                            href: link.href,
                    text: text,
                            isFooter: !!link.closest('footer, [id*="foot"], [class*="foot"]')
                        });
                    }
                });
                
                return links;
            }
        """)
        
        if not links_data or len(links_data) == 0:
            print("No relevant links found using JavaScript method")
            return None, page, unverified_result

        # Extract all links with a relevant name or URL
        relevant_links = []
        
        for link_data in links_data:
            href = link_data.get('href', '')
            text = link_data.get('text', '').lower()
            is_footer = link_data.get('isFooter', False)
            
            # Skip links from Apple/Google domains if we're on a developer site
            if developer_site_from_store:
                link_domain = urlparse(href).netloc
                if (is_apple_domain or "apple.com" in link_domain) and came_from_app_store:
                    continue
                if (is_google_domain or "google.com" in link_domain or "play.google.com" in link_domain) and came_from_play_store:
                    continue
            
            # Always skip Apple/Google domains if we're on their domains
            if is_apple_domain and "apple.com" in href:
                continue
            if is_google_domain and ("google.com" in href or "play.google.com" in href):
                continue
            
            # Calculate relevance score
            score = 0
            
            # Highest priority: exact matches for user/customer terms
            if "user agreement" in text:
                score += 150
            if "customer agreement" in text:
                score += 150
            if "user terms" in text:
                score += 140
            if "customer terms" in text:
                score += 140
            if "terms of use" in text:
                score += 140
            if "terms of service" in text:
                score += 130
            if "terms and conditions" in text:
                score += 120
            if "terms & conditions" in text:
                score += 120
            if "conditions of use" in text:
                score += 110
            if "legal terms" in text:
                score += 100
            
            # Medium priority: partial matches
            if "terms" in text:
                score += 90
            if "legal" in text:
                score += 80
            if "agreement" in text:
                score += 70
            if "conditions" in text:
                score += 60
            
            # URL patterns
            href_lower = href.lower()
            if "terms-of-service" in href_lower or "tos" in href_lower:
                score += 50
            if "terms-of-use" in href_lower or "tou" in href_lower:
                score += 50
            if "terms-and-conditions" in href_lower:
                score += 40
            if "legal/terms" in href_lower:
                score += 40
            if "agreement" in href_lower:
                score += 30
            
            # Boost score for footer links
            if is_footer:
                score *= 1.2  # 20% boost for footer links
            
            if score > 0:
                relevant_links.append({
                    "href": href,
                    "text": text,
                    "score": score
                })
        
        # Sort by score (highest first)
        relevant_links.sort(key=lambda x: x["score"], reverse=True)
        
        # Return the highest scoring link
        if relevant_links and len(relevant_links) > 0:
            best_link = relevant_links[0]["href"]
            print(f"Found best ToS link via JavaScript: {best_link} (Score: {relevant_links[0]['score']})")
            return best_link, page, unverified_result
        
        print("No relevant links found after filtering")
        return None, page, unverified_result

    except Exception as e:
        print(f"Error in find_all_links_js: {e}")
        traceback.print_exc()
        return None, page, unverified_result


async def find_matching_link(page, context, unverified_result=None):
    """Find and extract terms-related links without navigation."""
    try:
        # Use a more targeted selector for performance
        links = await page.query_selector_all(
            'footer a, .footer a, #footer a, a[href*="terms"], a[href*="tos"], a[href*="legal"]'
        )

        scored_links = []
        for link in links:
            try:
                text = await link.text_content()
                if not text:
                    continue
                text = text.lower().strip()
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Simplified scoring for speed
                score = 0
                if "terms of service" in text or "terms of use" in text:
                    score = 100
                elif "terms" in text:
                    score = 80
                elif "tos" in text:
                    score = 70
                elif "legal" in text:
                    score = 50

                # Additional URL scoring
                if "/terms-of-service" in href or "/terms_of_service" in href:
                    score += 50
                elif "/terms" in href or "/tos" in href:
                    score += 40
                elif "/legal" in href:
                    score += 30

                if score > 50:  # High confidence match
                    print(f"Found high confidence link: {text} ({score})")
                    scored_links.append((href, score, text))
            except Exception as e:
                continue
                
        # If we found high-confidence links, return the best one without navigation
        if scored_links:
            scored_links.sort(key=lambda x: x[1], reverse=True)
            best_link = scored_links[0][0]
            print(f"Returning best link without navigation: {best_link} (Score: {scored_links[0][1]})")
            return best_link, page, unverified_result
            
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_link: {e}")
        return None, page, unverified_result


async def click_and_wait_for_navigation(page, element, timeout=3000):
    """
    Click a link with human-like behavior and wait for navigation.
    Implements natural mouse movement and randomized delays between actions.
    """
    try:
        # Store the current URL before clicking
        current_url = page.url
        
        # Get element position and dimensions for natural click
        bound_box = await element.bounding_box()
        if not bound_box:
            print("Element not visible or has no bounding box")
            return False
            
        # Calculate a random position within the element (slightly off-center)
        center_x = bound_box['x'] + bound_box['width'] / 2
        center_y = bound_box['y'] + bound_box['height'] / 2
        
        # Add slight randomness to click position (more human-like)
        click_x = center_x + random.randint(-int(bound_box['width']/4), int(bound_box['width']/4))
        click_y = center_y + random.randint(-int(bound_box['height']/4), int(bound_box['height']/4))
        
        # Make sure we're still within the element
        click_x = max(bound_box['x'] + 2, min(click_x, bound_box['x'] + bound_box['width'] - 2))
        click_y = max(bound_box['y'] + 2, min(click_y, bound_box['y'] + bound_box['height'] - 2))
        
        # Get current mouse position
        current_mouse = await page.evaluate("""
            () => {
                return { 
                    x: window.mouseX || 100, 
                    y: window.mouseY || 100 
                }
            }
        """)
        
        start_x = current_mouse.get('x', 100)
        start_y = current_mouse.get('y', 100)
        
        # Human-like mouse movement (bezier curve) to the element
        await page.evaluate("""
            (startX, startY, endX, endY) => {
                // Store mouse position globally
                window.mouseX = startX;
                window.mouseY = startY;
                
                // Create bezier curve for natural movement
                function bezier(t, p0, p1, p2, p3) {
                    const cX = 3 * (p1 - p0);
                    const bX = 3 * (p2 - p1) - cX;
                    const aX = p3 - p0 - cX - bX;
                    
                    return aX * Math.pow(t, 3) + bX * Math.pow(t, 2) + cX * t + p0;
                }
                
                // Create control points with randomness for human curve
                const ctrlX1 = startX + (endX - startX) * (0.3 + Math.random() * 0.2);
                const ctrlY1 = startY + (endY - startY) * (0.1 + Math.random() * 0.2);
                const ctrlX2 = startX + (endX - startX) * (0.7 + Math.random() * 0.2);
                const ctrlY2 = startY + (endY - startY) * (0.8 + Math.random() * 0.2);
                
                const numSteps = Math.floor(Math.random() * 10) + 10; // 10-20 steps
                const duration = Math.floor(Math.random() * 400) + 300; // 300-700ms
                
                // Simulate the mouse movement
                for (let i = 0; i <= numSteps; i++) {
                    setTimeout(() => {
                        const t = i / numSteps;
                        const x = bezier(t, startX, ctrlX1, ctrlX2, endX);
                        const y = bezier(t, startY, ctrlY1, ctrlY2, endY);
                        
                        // Update stored position
                        window.mouseX = x;
                        window.mouseY = y;
                        
                        // Dispatch a mousemove event if this was a real browser
                        const evt = new MouseEvent('mousemove', {
                            bubbles: true,
                            cancelable: true,
                            clientX: x,
                            clientY: y
                        });
                        document.elementFromPoint(x, y)?.dispatchEvent(evt);
                    }, (duration / numSteps) * i);
                }
            }
        """, start_x, start_y, click_x, click_y)
        
        # Small random delay before clicking (like a real user hesitating)
        await page.wait_for_timeout(random.randint(70, 220))
        
        # Move the mouse to the actual click position
        await page.mouse.move(click_x, click_y)
        
        # Small delay between positioning and clicking
        await page.wait_for_timeout(random.randint(30, 100))
        
        # Use page.expect_navigation for modern Playwright API
        async with page.expect_navigation(timeout=timeout, wait_until="domcontentloaded") as navigation_info:
            # Click with a randomized delay (simulates how long mouse button is pressed)
            await page.mouse.down()
            await page.wait_for_timeout(random.randint(20, 70))  # Random press duration
            await page.mouse.up()
            
        # Wait for navigation to complete or timeout
        try:
            await navigation_info.value
            
            # After successful navigation, add human-like behavior
            await page.wait_for_timeout(random.randint(200, 500))  # Pause to "look" at the new page
            
            # Occasionally do a small scroll after navigation (like a human would)
            if random.random() < 0.7:  # 70% chance
                scroll_y = random.randint(80, 250)
                await page.evaluate(f"window.scrollBy(0, {scroll_y})")
                
            return True
        except Exception as e:
            print(f"Navigation error: {str(e)}")
            # Even if navigation times out, still return True if the URL changed
            new_url = page.url
            if new_url != current_url:
                setattr(page, "_last_url", new_url)
                return True
            return False
    except Exception as e:
        print(f"Error in click_and_wait_for_navigation: {str(e)}")
        return False


async def smooth_scroll_and_click(
    page, context, unverified_result=None, step=200, delay=30
):
    """
    Smooth scroll through the page using human-like patterns to find ToS links.
    Implements natural scrolling with variable speed and occasional pauses to mimic human browsing.
    """
    print("🔃 Starting human-like smooth scroll to find terms links...")
    high_score_footer_link = None
    
    try:
        # Get the current URL and domain
        current_url = await page.evaluate("() => window.location.href")
        parsed_url = urlparse(current_url)
        base_domain = parsed_url.netloc
        
        # Check if we're on Apple or Google domains
        is_apple_domain = "apple.com" in base_domain
        is_google_domain = "google.com" in base_domain or "play.google.com" in base_domain
        
        # Set up context flags similar to find_all_links_js
        developer_site_from_store = False
        came_from_app_store = False
        came_from_play_store = False
        
        if isinstance(context, dict):
            developer_site_from_store = context.get("came_from_app_store") or context.get("came_from_play_store")
            came_from_app_store = context.get("came_from_app_store", False)
            came_from_play_store = context.get("came_from_play_store", False)
        
        # Human-like behavior: Move the mouse to a random position before scrolling
        viewport_size = await page.evaluate("""() => {
            return {
                width: window.innerWidth,
                height: window.innerHeight
            }
        }""")
        
        # Random mouse position within viewport
        mouse_x = random.randint(100, viewport_size['width'] - 100)
        mouse_y = random.randint(100, viewport_size['height'] - 100)
        await page.mouse.move(mouse_x, mouse_y, steps=random.randint(3, 8))
        
        # Brief random pause before interaction
        await page.wait_for_timeout(random.randint(200, 600))
        
        # Try to find the footer first as it often contains ToS links
        footer_selector = await page.evaluate("""
            () => {
                const footers = document.querySelectorAll('footer, [id*="foot"], [class*="foot"]');
                return footers.length > 0 ? 'footer, [id*="foot"], [class*="foot"]' : null;
            }
        """)
        
        if footer_selector:
            print(f"Found footer with selector: {footer_selector}")
            # Scroll to the footer with a natural human-like scrolling pattern
            await page.evaluate(f"""() => {{
                // Get the element position
                const footer = document.querySelector('{footer_selector}');
                if (!footer) return;
                
                const footerTop = footer.getBoundingClientRect().top + window.scrollY;
                const startY = window.scrollY;
                const distance = footerTop - startY;
                
                // Natural easing function for human-like scrolling
                const easeOutCubic = t => 1 - Math.pow(1 - t, 3);
                
                // Set up scrolling animation with variable speeds
                const duration = {random.randint(800, 1500)};  // Random duration
                const startTime = Date.now();
                
                return new Promise(resolve => {{
                    function scrollStep() {{
                        const elapsed = Date.now() - startTime;
                        const progress = Math.min(elapsed / duration, 1);
                        const easedProgress = easeOutCubic(progress);
                        
                        // Calculate new position with slight random variation (like human jitter)
                        const currentY = startY + (distance * easedProgress);
                        const jitter = progress < 1 ? Math.random() * 2 - 1 : 0; // Small random movement
                        
                        window.scrollTo(0, currentY + jitter);
                        
                        if (progress < 1) {{
                            // Add slight randomness to scroll timing
                            setTimeout(scrollStep, Math.random() * 10 + 10);
                        }} else {{
                            // Small pause at the end of scrolling
                            setTimeout(resolve, {random.randint(300, 600)});
                        }}
                    }}
                    
                    // Occasionally add a brief pause during scrolling (like a human getting distracted)
                    if (Math.random() < 0.3) {{
                        setTimeout(scrollStep, {random.randint(200, 800)});
                    }} else {{
                        scrollStep();
                    }}
                }});
            }}""")
            
            # Additional human-like pause after scrolling to footer
            await page.wait_for_timeout(random.randint(500, 900))
        
        # Get all links that match ToS patterns with a more comprehensive search
        links = await page.evaluate("""
            () => {
                const links = [];
                
                // Check all link elements
                document.querySelectorAll('a[href]').forEach(link => {
                    const text = link.textContent.trim().toLowerCase();
                        const href = link.href.toLowerCase();
                    
                    // Skip javascript, mailto, etc.
                    if (href.startsWith('javascript:') || href.startsWith('mailto:') || href.startsWith('tel:')) {
                        return;
                    }
                    
                    let score = 0;
                    
                    // Score based on text
                    if (text.includes('user agreement')) score += 150;
                    if (text.includes('customer agreement')) score += 150;
                    if (text.includes('user terms')) score += 140;
                    if (text.includes('customer terms')) score += 140;
                    if (text.includes('terms of use')) score += 140;
                    if (text.includes('terms of service')) score += 130;
                    if (text.includes('terms and conditions')) score += 120;
                    if (text.includes('terms & conditions')) score += 120;
                    if (text.includes('conditions of use')) score += 110;
                    if (text.includes('legal terms')) score += 100;
                    if (text.includes('terms')) score += 90;
                    if (text.includes('legal')) score += 80;
                    if (text.includes('agreement')) score += 70;
                    if (text.includes('conditions')) score += 60;
                    
                    // URL patterns
                    if (href.includes('terms-of-service') || href.includes('tos')) score += 50;
                    if (href.includes('terms-of-use') || href.includes('tou')) score += 50;
                    if (href.includes('terms-and-conditions')) score += 40;
                    if (href.includes('legal/terms')) score += 40;
                    if (href.includes('agreement')) score += 30;
                    
                    // Add bonus for links in footer area
                    if (link.closest('footer, [id*="foot"], [class*="foot"]')) {
                        score += 40;
                    }
                    
                    // Add bonus for links at bottom of page
                    const rect = link.getBoundingClientRect();
                    const pageHeight = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                    const linkPosition = rect.top + window.scrollY;
                    if (linkPosition > (pageHeight * 0.7)) {
                        score += 20;
                    }
                    
                    if (score >= 60) {
                        // Get extra info about the link's position for more natural clicking
                        const rect = link.getBoundingClientRect();
                        links.push({
                            text: text,
                            href: link.href,
                            score: score,
                            x: rect.left + (rect.width / 2),
                            y: rect.top + (rect.height / 2),
                            width: rect.width,
                            height: rect.height,
                            isVisible: rect.height > 0 && rect.width > 0 && 
                                       rect.top >= 0 && rect.left >= 0 &&
                                       rect.bottom <= window.innerHeight && 
                                       rect.right <= window.innerWidth
                        });
                    }
                });
                
                return links;
            }
        """)
        
        # Filter out links from Apple/Google domains
        filtered_links = []
        
        for link in links:
            link_text = link.get('text', '')
            link_url = link.get('href', '')
            link_score = link.get('score', 0)
            
            # Skip Apple/Google domain links based on context
            link_domain = urlparse(link_url).netloc
            
            # Skip links from Apple/Google domains if we're on a developer site
            if developer_site_from_store:
                if (is_apple_domain or "apple.com" in link_domain) and came_from_app_store:
                    print(f"Skipping Apple domain link: {link_text} - {link_url}")
                    continue
                if (is_google_domain or "google.com" in link_domain or "play.google.com" in link_domain) and came_from_play_store:
                    print(f"Skipping Google domain link: {link_text} - {link_url}")
                    continue
            
            # Always skip Apple/Google domains if we're on their domains
            if is_apple_domain and "apple.com" in link_url:
                print(f"Skipping Apple domain link (on Apple domain): {link_text} - {link_url}")
                continue
            if is_google_domain and ("google.com" in link_url or "play.google.com" in link_url):
                print(f"Skipping Google domain link (on Google domain): {link_text} - {link_url}")
                continue
            
            # Keep high confidence links
            if link_score >= 60:
                print(f"Found high confidence link: {link_text} ({link_score})")
                filtered_links.append(link)
                # Track high score link for fallback
                high_score_footer_link = link_url
        
        # Sort links by score
        filtered_links.sort(key=lambda x: x['score'], reverse=True)
        
        # If we have links, try to click on the best one with natural mouse movement
        if filtered_links:
            best_link = filtered_links[0]
            best_link_url = best_link['href']
            best_score = best_link['score']
            
            print(f"Found best link: {best_link['text']} - {best_link_url} (Score: {best_score})")
            
            # If the link isn't visible in viewport, scroll to it first with natural movement
            if not best_link.get('isVisible', False):
                print("Link not visible in viewport, scrolling to it with natural movement...")
                
                # Get link position
                link_x = best_link.get('x', 0)
                link_y = best_link.get('y', 0)
                
                # Natural scroll to the element
                await page.evaluate(f"""() => {{
                    const targetY = {link_y};
                    const viewportHeight = window.innerHeight;
                    const targetScroll = Math.max(0, targetY - (viewportHeight / 2));
                    
                    // Get current scroll position
                    const startY = window.scrollY;
                    const distance = targetScroll - startY;
                    
                    // Natural easing function
                    const easeOutCubic = t => 1 - Math.pow(1 - t, 3);
                    
                    // Set up scrolling animation
                    const duration = {random.randint(600, 1200)};
                    const startTime = Date.now();
                    
                    return new Promise(resolve => {{
                        function step() {{
                            const elapsed = Date.now() - startTime;
                            const progress = Math.min(elapsed / duration, 1);
                            const easedProgress = easeOutCubic(progress);
                            
                            // Add slight "human" jitter
                            const jitter = progress < 1 ? (Math.random() * 2 - 1) : 0;
                            window.scrollTo(0, startY + (distance * easedProgress) + jitter);
                            
                            if (progress < 1) {{
                                requestAnimationFrame(step);
                            }} else {{
                                setTimeout(resolve, {random.randint(200, 500)});
                            }}
                        }}
                        
                        step();
                    }});
                }}""")
                
                # Pause briefly after scrolling
                await page.wait_for_timeout(random.randint(300, 700))
            
            # Move mouse to the link with human-like motion
            if 'x' in best_link and 'y' in best_link:
                # Start from current mouse position
                current_pos = await page.evaluate("() => { return { x: window.mouseX || 100, y: window.mouseY || 100 } }")
                start_x = current_pos.get('x', 100)
                start_y = current_pos.get('y', 100)
                
                # Target is center of element with slight randomness
                target_x = best_link['x'] + random.randint(-5, 5)
                target_y = best_link['y'] + random.randint(-3, 3)
                
                # Human-like mouse movement with bezier curve
                await page.evaluate(f"""(startX, startY, endX, endY) => {{
                    // Store mouse position globally for tracking
                    window.mouseX = startX;
                    window.mouseY = startY;
                    
                    // Create bezier curve for human-like movement
                    function bezier(t, p0, p1, p2, p3) {{
                        const cX = 3 * (p1 - p0);
                        const bX = 3 * (p2 - p1) - cX;
                        const aX = p3 - p0 - cX - bX;
                        
                        return aX * Math.pow(t, 3) + bX * Math.pow(t, 2) + cX * t + p0;
                    }}
                    
                    // Create control points with some randomness for natural curve
                    const ctrlX1 = startX + (endX - startX) * (0.3 + Math.random() * 0.2);
                    const ctrlY1 = startY + (endY - startY) * (0.1 + Math.random() * 0.2);
                    const ctrlX2 = startX + (endX - startX) * (0.7 + Math.random() * 0.2);
                    const ctrlY2 = startY + (endY - startY) * (0.8 + Math.random() * 0.2);
                    
                    const duration = {random.randint(500, 900)};
                    const steps = {random.randint(12, 20)};
                    
                    // Simulate the mouse movement in steps
                    return new Promise(resolve => {{
                        for (let i = 0; i <= steps; i++) {{
                            setTimeout(() => {{
                                const t = i / steps;
                                const x = bezier(t, startX, ctrlX1, ctrlX2, endX);
                                const y = bezier(t, startY, ctrlY1, ctrlY2, endY);
                                
                                // Update stored position
                                window.mouseX = x;
                                window.mouseY = y;
                                
                                // If this was a real browser with mouse events
                                const evt = new MouseEvent('mousemove', {{
                                    bubbles: true,
                                    cancelable: true,
                                    clientX: x,
                                    clientY: y
                                }});
                                document.elementFromPoint(x, y)?.dispatchEvent(evt);
                                
                                if (i === steps) resolve();
                            }}, (duration / steps) * i);
                        }}
                    }});
                }}""", start_x, start_y, target_x, target_y)
                
                # Small pause before clicking like a human would
                await page.wait_for_timeout(random.randint(70, 150))
                
                # Click the element (using mouse position from above)
                try:
                    # Find the element to click
                    elements = await page.query_selector_all(f"a[href='{best_link_url}']")
                    if elements and len(elements) > 0:
                        # Use the appropriate element
                        element = elements[0]
                        # Click with random offset from center and natural delay
                        await element.click({
                            "position": {
                                "x": random.randint(5, int(best_link.get('width', 10) - 5)),
                                "y": random.randint(3, int(best_link.get('height', 10) - 3))
                            },
                            "delay": random.randint(20, 100)  # Human-like click duration
                        })
                        
                        # Wait for navigation to complete
                        await page.wait_for_load_state("domcontentloaded")
                        
                        # Give the page time to settle
                        await page.wait_for_timeout(random.randint(500, 1000))
                        
                        # Return the URL we navigated to
                        current_url = page.url
                        print(f"✅ Successfully clicked and navigated to: {current_url}")
                        return current_url, page, unverified_result
                    else:
                        print(f"Element for URL {best_link_url} not found, returning URL without navigation")
                        return best_link_url, page, unverified_result
                except Exception as e:
                    print(f"Error clicking link: {e}")
                    # Return the URL even if we couldn't click it
                    return best_link_url, page, unverified_result
            
            # If we can't access coordinates, just return the URL without clicking
            print(f"Returning best link without navigation: {best_link_url} (Score: {best_score})")
            return best_link_url, page, unverified_result
        
        # If no high-confidence links found, try scrolling through the page naturally
        print("No high-confidence links found, performing natural scroll through page...")
        
        # Scroll down with natural, human-like pattern
        await page.evaluate("""() => {
            return new Promise(resolve => {
                // Get document height
                const docHeight = Math.max(
                    document.body.scrollHeight, 
                    document.documentElement.scrollHeight
                );
                const viewHeight = window.innerHeight;
                const scrollDistance = docHeight - viewHeight;
                
                // Start position
                let startTime = null;
                const duration = 3000 + Math.random() * 2000; // Random duration between 3-5s
                
                // Create random pauses
                const pausePoints = [];
                const numberOfPauses = Math.floor(Math.random() * 3) + 1; // 1-3 pauses
                
                for (let i = 0; i < numberOfPauses; i++) {
                    pausePoints.push({
                        position: Math.random() * 0.8 + 0.1, // 10%-90% of the scroll
                        duration: Math.random() * 800 + 400 // 400-1200ms pause
                    });
                }
                
                // Sort pause points
                pausePoints.sort((a, b) => a.position - b.position);
                
                // Variable to track if we're in a pause
                let isPausing = false;
                let currentPauseIdx = 0;
                
                // Scroll function with natural motion
                function smoothScroll(timestamp) {
                    if (!startTime) startTime = timestamp;
                    const elapsed = timestamp - startTime;
                    
                    // Check if we should pause
                    if (currentPauseIdx < pausePoints.length) {
                        const currentPause = pausePoints[currentPauseIdx];
                        const pausePosition = currentPause.position * duration;
                        
                        if (elapsed >= pausePosition && !isPausing) {
                            isPausing = true;
                            setTimeout(() => {
                                isPausing = false;
                                currentPauseIdx++;
                                requestAnimationFrame(smoothScroll);
                            }, currentPause.duration);
                            return;
                        }
                    }
                    
                    if (isPausing) return;
                    
                    // Calculate progress with easing
                    const progress = Math.min(elapsed / duration, 1);
                    
                    // Easing function for natural movement
                    // Slight acceleration at start, constant speed in middle, deceleration at end
                    let easedProgress;
                    if (progress < 0.2) {
                        // Accelerating from zero velocity (ease-in)
                        easedProgress = progress * progress * 5;
                    } else if (progress > 0.8) {
                        // Decelerating to zero velocity (ease-out)
                        const t = (progress - 0.8) * 5;
                        easedProgress = 0.8 + (1 - (1 - t) * (1 - t)) * 0.2;
                    } else {
                        // Constant speed
                        easedProgress = 0.2 + (progress - 0.2) * 0.75;
                    }
                    
                    // Add slight random jitter for more natural feel
                    const jitter = (progress < 1) ? (Math.random() * 2 - 1) * 2 : 0;
                    
                    // Do the scroll
                    window.scrollTo(0, easedProgress * scrollDistance + jitter);
                    
                    // Continue animation or resolve
                    if (progress < 1) {
                        requestAnimationFrame(smoothScroll);
                    } else {
                        // Final delay to look at content at bottom
                        setTimeout(resolve, 1000);
                    }
                }
                
                // Start animation
                requestAnimationFrame(smoothScroll);
            });
        }""")
        
        print("✅ Reached the bottom of the page.")

        # If we found a high score link but couldn't navigate to it, use it as fallback
        if high_score_footer_link and not unverified_result:
            unverified_result = high_score_footer_link

        return None, page, unverified_result

    except Exception as e:
        print(f"Error in smooth scroll: {str(e)}")
        # If we found a high score link before the error, use it as fallback
        if high_score_footer_link and not unverified_result:
            unverified_result = high_score_footer_link
        return None, page, unverified_result


async def analyze_landing_page(page, context, unverified_result=None):
    """
    Analyze landing page content to detect mentions of terms of service.
    Sometimes pages mention terms in the content but don't have direct links.
    """
    print("\n=== Starting landing page analysis ===")

    try:
        # Look for text patterns that might indicate terms of service info
        terms_mentions = await page.evaluate(
            """() => {
            // Get page text
            const pageText = document.body.innerText.toLowerCase();
            
            // Look for terms-related phrases
            const termsPhases = [
                'terms of service',
                'terms of use',
                'terms and conditions',
                'user agreement',
                'service agreement',
                'legal agreement',
                'platform agreement'
            ];
            
            const mentions = [];
            let context = '';
            
            // Find mentions of terms in text
            for (const phrase of termsPhases) {
                const index = pageText.indexOf(phrase);
                if (index > -1) {
                    // Get surrounding context (up to 50 chars before and after)
                    const start = Math.max(0, index - 50);
                    const end = Math.min(pageText.length, index + phrase.length + 50);
                    context = pageText.substring(start, end);
                    
                    mentions.push({
                        phrase: phrase,
                        context: context
                    });
                }
            }
            
            return mentions;
        }"""
        )

        if terms_mentions and len(terms_mentions) > 0:
            print(f"Found {len(terms_mentions)} terms mentions in content")

            # Look for URLs in the context of these mentions
            for mention in terms_mentions:
                print(
                    f"Terms mention: '{mention['phrase']}' in context: '{mention['context']}'"
                )

                # Try to find nearby links
                nearby_links = await page.evaluate(
                    """(searchPhrase) => {
                    const allText = document.body.innerText.toLowerCase();
                    const index = allText.indexOf(searchPhrase.toLowerCase());
                    if (index === -1) return [];
                    
                    // Find the containing element
                    let element = null;
                    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let node;
                    
                    while (node = walk.nextNode()) {
                        if (node.textContent.toLowerCase().includes(searchPhrase.toLowerCase())) {
                            element = node.parentElement;
                            break;
                        }
                    }
                    
                    if (!element) return [];
                    
                    // Look for nearby links (parent, siblings, children)
                    const searchArea = element.closest('div, section, article, footer') || element.parentElement;
                    if (!searchArea) return [];
                    
                    // Get all links in the search area
                    const links = Array.from(searchArea.querySelectorAll('a[href]'))
                        .filter(link => {
                            const href = link.href.toLowerCase();
                            return href && 
                                !href.startsWith('javascript:') && 
                                !href.startsWith('mailto:') &&
                                !href.startsWith('tel:') &&
                                !href.startsWith('#');
                        })
                        .map(link => ({
                            text: link.textContent.trim(),
                            href: link.href
                        }));
                    
                    return links;
                }""",
                    mention["phrase"],
                )

                if nearby_links and len(nearby_links) > 0:
                    print(f"Found {len(nearby_links)} links near the terms mention")

                    # Score and sort these links
                    scored_links = []
                    for link in nearby_links:
                        score = 0
                        text = link["text"].lower() if link["text"] else ""
                        href = link["href"].lower()

                        # Score based on text
                        if "terms" in text:
                            score += 40
                        if "service" in text:
                            score += 20
                        if "use" in text:
                            score += 15
                        if "conditions" in text:
                            score += 15

                        # Score based on URL
                        if "terms" in href:
                            score += 30
                        if "tos" in href:
                            score += 25
                        if "legal" in href:
                            score += 10

                        scored_links.append((link["href"], score, link["text"]))

                    # Sort by score
                    scored_links.sort(key=lambda x: x[1], reverse=True)

                    if (
                        scored_links and scored_links[0][1] >= 40
                    ):  # Good confidence threshold
                        best_link = scored_links[0][0]
                        print(
                            f"Best link from context: {best_link} (score: {scored_links[0][1]}, text: '{scored_links[0][2]}')"
                        )

                        # Try to navigate to verify
                        try:
                            await page.goto(
                                best_link, timeout=10000, wait_until="domcontentloaded"
                            )
                            is_terms_page = await page.evaluate(
                                """() => {
                                const text = document.body.innerText.toLowerCase();
                                const strongTermMatchers = [
                                    'terms of service', 
                                    'terms of use', 
                                    'terms and conditions',
                                    'accept these terms', 
                                    'agree to these terms',
                                    'legally binding',
                                    'your use of this website',
                                    'this agreement',
                                    'these terms govern'
                                ];
                                
                                return strongTermMatchers.some(term => text.includes(term));
                            }"""
                            )

                            if is_terms_page:
                                print(
                                    f"✅ Verified as terms of service page: {page.url}"
                                )
                                return page.url, page, unverified_result
                            else:
                                if not unverified_result:
                                    unverified_result = best_link
                        except Exception as e:
                            print(f"Error navigating to link from context: {e}")

        return None, page, unverified_result
    except Exception as e:
        print(f"Error in landing page analysis: {e}")
        return None, page, unverified_result


async def verify_is_terms_page(page):
    """Verify if the current page is a terms of service page."""
    try:
        page_title = await page.title()
        page_url = page.url

        # Initial checks on title and URL
        title_lower = page_title.lower()
        url_lower = page_url.lower()

        print("🔍 Performing thorough page verification...")

        # Define indicators
        title_indicators = [
            "terms",
            "conditions",
            "tos",
            "terms of service",
            "terms and conditions",
            "legal",
            "agreement",
            "user agreement",
            "terms of use",
            "legal terms",
            "terms & conditions",
            "service agreement",
        ]

        strong_indicators = [
            "terms of service",
            "terms and conditions",
            "user agreement",
            "conditions of use",
            "terms of use",
            "legal agreement",
        ]

        url_indicators = [
            "/terms",
            "/tos",
            "/terms-of-service",
            "/terms-and-conditions",
            "/legal/terms",
            "/legal",
            "/user-agreement",
            "/terms-of-use",
            "terms.html",
            "tos.html",
            "conditions.html",
            "agreement.html",
            "legal.html",
        ]

        # Check for presence of indicators
        strong_indicator = any(
            indicator in title_lower for indicator in strong_indicators
        )
        title_indicator = any(
            indicator in title_lower for indicator in title_indicators
        )
        url_indicator = any(indicator in url_lower for indicator in url_indicators)

        # Extract text content
        content = await page.evaluate(
            """() => {
            return document.body.innerText;
        }"""
        )

        content_lower = content.lower()
        content_length = len(content)

        # Check for legal sections and phrases
        legal_sections = 0
        legal_phrases = 0

        section_patterns = [
            "general terms",
            "acceptance of terms",
            "modifications to terms",
            "user responsibilities",
            "account registration",
            "user conduct",
            "intellectual property",
            "copyright",
            "trademark",
            "disclaimer",
            "limitation of liability",
            "indemnification",
            "termination",
            "governing law",
            "dispute resolution",
            "arbitration",
            "class action waiver",
            "severability",
            "entire agreement",
            "contact information",
            "privacy policy",
            "data collection",
            "third party rights",
            "force majeure",
            "assignment",
            "changes to service",
            "user content",
            "prohibited activities",
            "warranties",
            "representations",
            "compliance with laws",
            "electronic communications",
            "modification of service",
            "fees and payments",
            "refund policy",
            "cancellation policy",
        ]

        legal_phrase_patterns = [
            "by using this site",
            "by accessing this website",
            "please read these terms",
            "please read carefully",
            "agree to be bound",
            "constitutes your acceptance",
            "reserve the right to change",
            "at our sole discretion",
            "you acknowledge and agree",
            "without prior notice",
            "shall not be liable",
            "disclaim any warranties",
            "as is and as available",
            "limitation of liability",
            "indemnify and hold harmless",
            "jurisdiction and venue",
            "class action waiver",
            "binding arbitration",
            "no warranty of any kind",
            "exclusive remedy",
            "subject to these terms",
            "constitute acceptance",
            "terminate your account",
            "all rights reserved",
            "hereby grant",
            "represent and warrant",
            "we may modify",
            "applicable law",
        ]

        # Check for legal headings
        legal_headings = await page.evaluate(
            """() => {
            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6, strong, b'));
            const legalHeadingPatterns = [
                "terms", "conditions", "agreement", "disclaimer", "liability", 
                "rights", "privacy", "policy", "warranty", "remedies", 
                "arbitration", "termination", "law", "jurisdiction", "indemnification",
                "intellectual property", "copyright", "trademark", "user", "account",
                "governing law", "dispute", "refund", "cancellation", "payments"
            ];
            
            return headings.some(h => {
                const text = h.innerText.toLowerCase();
                return legalHeadingPatterns.some(pattern => text.includes(pattern));
            });
        }"""
        )

        # Count legal sections and phrases
        for pattern in section_patterns:
            if pattern in content_lower:
                legal_sections += 1

        for pattern in legal_phrase_patterns:
            if pattern in content_lower:
                legal_phrases += 1

        # Check for negative indicators that suggest it's not a terms page
        negative_indicators = [
            "sign in",
            "sign up",
            "login",
            "register",
            "create account",
            "add to cart",
            "shopping cart",
            "checkout",
            "buy now",
            "password",
            "email address",
            "payment",
            "credit card",
            "shipping",
            "delivery",
            "order status",
            "my account",
            "track order",
            "return policy",
        ]

        has_negative_indicators = any(
            indicator in content_lower and indicator not in ["return policy"]
            for indicator in negative_indicators
        )

        # Determine if this is a terms page
        minimum_text_length = 1000  # Minimum content length for a terms page
        minimum_legal_sections = 3  # Minimum number of legal sections required

        minimum_text_present = content_length >= minimum_text_length
        minimum_sections_present = legal_sections >= minimum_legal_sections

        # Calculate confidence score (0-100)
        confidence_score = 0

        # Base score from indicators
        if strong_indicator:
            confidence_score += 30
        if title_indicator:
            confidence_score += 20
        if url_indicator:
            confidence_score += 15

        # Content-based scoring
        confidence_score += min(
            20, legal_sections * 3
        )  # Up to 20 points for legal sections
        confidence_score += min(
            15, legal_phrases * 2
        )  # Up to 15 points for legal phrases

        if legal_headings:
            confidence_score += 10

        # Length bonus
        if content_length > 5000:
            confidence_score += 5

        # Additional title-based scoring for common terms page titles
        common_tos_titles = [
            "conditions of use",
            "terms of service",
            "user agreement",
            "terms and conditions",
        ]
        if any(title in title_lower for title in common_tos_titles):
            confidence_score += 10

        # Penalties
        if has_negative_indicators:
            # Reduce penalty impact if we have strong title indicators and legal content
            if (
                any(title in title_lower for title in common_tos_titles)
                and legal_sections >= 5
            ):
                confidence_score -= 10  # Reduced penalty
            else:
                confidence_score -= 20

        if not minimum_text_present:
            confidence_score -= 30

        if not minimum_sections_present:
            confidence_score -= 20

        # Cap the score
        confidence_score = max(0, min(100, confidence_score))

        # Determine if this is a terms page
        # A page is considered a terms page if it has a high confidence score
        is_terms_page = confidence_score >= 75

        # For pages with strong indicators in title but slightly lower scores,
        # be more lenient to avoid missing valid ToS pages
        if not is_terms_page and confidence_score >= 65:
            # Check if the title contains very strong ToS indicators
            very_strong_title_indicators = [
                "conditions of use",
                "terms of service",
                "user agreement",
                "terms and conditions",
            ]
            if any(
                indicator in title_lower for indicator in very_strong_title_indicators
            ):
                if legal_sections >= 5 or legal_phrases >= 3:
                    is_terms_page = True
                    print(
                        f"✅ Special consideration: High confidence title with legal content (Score: {confidence_score}/100)"
                    )

        # Print verification results
        print(f"📊 Page verification results:")
        print(f"  Title: {title_lower}...")
        print(f"  URL: {url_lower}")
        print(f"  Confidence Score: {confidence_score}/100")
        print(f"  Strong Indicators: {strong_indicator}")
        print(f"  Title Indicators: {title_indicator}")
        print(f"  URL Indicators: {url_indicator}")
        print(f"  Legal Sections: {legal_sections}")
        print(f"  Legal Phrases: {legal_phrases}")
        print(f"  Has Legal Headings: {legal_headings}")
        print(f"  Content Length: {content_length} chars")
        print(f"  Has Negative Indicators: {has_negative_indicators}")

        if is_terms_page:
            print(
                f"✅ VERIFIED: This appears to be a Terms of Service page (Score: {confidence_score}/100)"
            )
        else:
            print(
                f"❌ NOT VERIFIED: Does not appear to be a Terms of Service page (Score: {confidence_score}/100)"
            )

        # Return detailed verification results
        return {
            "isTermsPage": is_terms_page,
            "confidence": confidence_score,
            "title": page_title,
            "url": page_url,
            "strongIndicator": strong_indicator,
            "titleIndicator": title_indicator,
            "urlIndicator": url_indicator,
            "legalSectionCount": legal_sections,
            "legalPhraseCount": legal_phrases,
            "hasLegalHeadings": legal_headings,
            "contentLength": content_length,
            "minimumTextPresent": minimum_text_present,
            "minimumSectionsPresent": minimum_sections_present,
            "hasNegativeIndicators": has_negative_indicators,
        }

    except Exception as e:
        print(f"Error during page verification: {e}")
        return {"isTermsPage": False, "confidence": 0, "error": str(e)}


def score_tos_url_by_path_specificity(url):
    """
    Score a URL based on how likely it is to be a ToS page.
    Higher scores indicate higher likelihood.
    
    Args:
        url: URL to score
        
    Returns:
        Score integer
    """
    # Validate URL
    if not url:
        return 0
        
    url = url.lower()
    score = 0
    
    # Parse the URL
    parsed_url = urlparse(url)
    hostname = parsed_url.netloc
    path = parsed_url.path.strip('/')
    path_parts = path.split('/')
    
    # Check for paths that often contain terms of service
    path_patterns = [
        ('/terms', 100),
        ('/tos', 120),
        ('/terms-of-service', 150),
        ('/terms-of-use', 140),
        ('/terms-and-conditions', 130),
        ('/legal', 100),
        ('/legal/terms', 120),
        ('/legal/user-agreement', 120),
        ('/user-agreement', 110),
        ('/customer-agreement', 110),
        ('/agreement', 90),
        ('/policies', 80),
        ('/legal/policies', 90),
        ('/policies/terms', 100),
        ('/about/terms', 100),
        ('/about/legal', 90)
    ]
    
    # Score URL based on path patterns
    for pattern, pattern_score in path_patterns:
        if pattern in url:
            score += pattern_score
    
    # NEW: Give higher scores for policy-specific domains/subdomains
    # If hostname contains policy-related keywords
    policy_domains = ['policy', 'policies', 'legal', 'terms', 'tos', 'privacy']
    for keyword in policy_domains:
        if keyword in hostname:
            score += 70  # Significant boost for policy-specific domains
    
    # NEW: Analyze domain structure - company main domains are more authoritative than subdomains
    domain_parts = hostname.split('.')
    
    # NEW: Detect if this is a PDF - may contain official policy document
    if path.endswith('.pdf'):
        # PDF of terms is usually authoritative but less user-friendly
        score += 40
    
    # NEW: Detect policy domain pattern like policies.example.com or legal.example.com
    if len(domain_parts) > 2 and domain_parts[0] in policy_domains:
        score += 60
    
    # NEW: Consider URL "cleanliness" - shorter URLs are often more authoritative main policy pages
    if len(path_parts) <= 2:
        score += 30  # Boost for concise URLs like example.com/terms
    
    # NEW: Boost for root domain policy pages vs subdomain policies
    # E.g., example.com/terms vs subdomain.example.com/terms
    if len(domain_parts) == 2 or (len(domain_parts) == 3 and domain_parts[0] == 'www'):
        score += 40
    
    # NEW: Don't penalize language parameters in URLs
    query_params = parse_qs(parsed_url.query)
    has_only_language_params = True
    
    # Common language/localization parameter names
    language_params = ['hl', 'lang', 'locale', 'language', 'l']
    
    for param in query_params:
        if param.lower() not in language_params:
            has_only_language_params = False
            break
    
    # Prefer URLs without query parameters, but don't penalize language parameters
    if parsed_url.query and not has_only_language_params:
        score -= 30
    elif parsed_url.query and has_only_language_params:
        # No penalty for language params, actually a small boost as it suggests localized official content
        score += 10
    
    # Check for fragments - prefer URLs without them
    if parsed_url.fragment:
        score -= 20
    
    # NEW: More sophisticated path analysis
    # Analyze if the path is specifically about terms vs other policies
    terms_keywords = ['term', 'tos', 'service', 'agreement', 'condition']
    privacy_keywords = ['privacy', 'data', 'cookie', 'gdpr']
    
    # Count term-specific keywords in path
    term_keyword_count = sum(1 for part in path_parts for keyword in terms_keywords if keyword in part)
    privacy_keyword_count = sum(1 for part in path_parts for keyword in privacy_keywords if keyword in part)
    
    # We're looking for terms, not privacy policies
    if term_keyword_count > 0:
        score += term_keyword_count * 25
    if privacy_keyword_count > 0 and term_keyword_count == 0:
        # If it's just a privacy policy without terms, reduce score
        score -= privacy_keyword_count * 15
    
    # NEW: Handle the special case of top-level paths
    # For example: example.com/terms is likely more relevant than example.com/product/terms
    if len(path_parts) == 1 and any(keyword in path_parts[0] for keyword in terms_keywords):
        score += 40  # Big boost for top-level terms paths
    
    return score

async def yahoo_search_fallback(query, page):
    """
    Search for ToS using Yahoo as a fallback method.
    Uses a single query string containing multiple terms.
    
    Args:
        query: Search query string including multiple terms (e.g. "example.com terms of service, terms of use")
        page: Playwright page to use for the search
    
    Returns:
        URL to ToS page if found, None otherwise
    """
    try:
        print("Attempting search engine fallback with Yahoo...")
        
        # Navigate to Yahoo search
        yahoo_search_url = f"https://search.yahoo.com/search?p={query}"
        
        # Navigate to Yahoo and perform the search
        await page.goto(yahoo_search_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)  # Wait for results to load
        
        # Extract search results from Yahoo
        search_results = await page.evaluate("""
            () => {
                // Function to extract text content safely
                const getTextContent = (element) => {
                    return element ? element.textContent.trim() : '';
                };
                
                const results = [];
                
                // Yahoo results are in various formats depending on the layout
                // Try multiple selectors to increase chances of finding results
                const resultSelectors = [
                    '.algo-sr', // Standard results
                    '.dd.algo', // Alternative result format
                    'li.first', // Yet another format
                    '#web li'   // Basic web results
                ];
                
                // Try each selector
                for (const selector of resultSelectors) {
                    const elements = document.querySelectorAll(selector);
                    
                    if (elements && elements.length > 0) {
                        for (const element of elements) {
                            // Extract link - multiple possible selectors
                            let link = null;
                            
                            // Try to find links in various locations
                            const linkSelectors = [
                                'a.d-ib', // Main link
                                'a.ac-algo', // Alternative link format
                                'h3 a', // Heading link
                                'a.fz-m', // Another format
                                'a' // Any link as fallback
                            ];
                            
                            for (const linkSelector of linkSelectors) {
                                const linkElement = element.querySelector(linkSelector);
                                if (linkElement && linkElement.href) {
                                    link = linkElement.href;
                                    break;
                                }
                            }
                            
                            if (!link) continue;
                            
                            // Skip Yahoo's RU links which are redirect wrappers
                            if (link.includes('/RU=')) {
                                try {
                                    // Extract the actual URL from Yahoo's redirect
                                    const match = /\/RU=([^\\/]+)\//.exec(link);
                                    if (match && match[1]) {
                                        link = decodeURIComponent(match[1]);
                                    }
                                } catch (e) {
                                    // If decoding fails, skip this result
                                    continue;
                                }
                            }
                            
                            // Extract title
                            let title = '';
                            const titleSelectors = ['h3', '.title', '.fz-ms'];
                            
                            for (const titleSelector of titleSelectors) {
                                const titleElement = element.querySelector(titleSelector);
                                if (titleElement) {
                                    title = getTextContent(titleElement);
                                    break;
                                }
                            }
                            
                            // Extract description
                            let description = '';
                            const descriptionSelectors = ['.compText', '.lh-l', '.fz-ms'];
                            
                            for (const descSelector of descriptionSelectors) {
                                const descElement = element.querySelector(descSelector);
                                if (descElement && descElement.textContent.length > 20) {
                                    description = getTextContent(descElement);
                                    break;
                                }
                            }
                            
                            // Skip search engine and unrelated results
                            if (link.includes('yahoo.com/search') || 
                                link.includes('google.com/search') ||
                                link.includes('bing.com/search') ||
                                link.includes('duckduckgo.com')) {
                                continue;
                            }
                            
                            // Add to results
                            results.push({
                                url: link,
                                title: title,
                                description: description
                            });
                        }
                        
                        // If we found results with this selector, stop trying others
                        if (results.length > 0) {
                            break;
                        }
                    }
                }
                
                return results;
            }
        """)
        
        print(f"Found {len(search_results)} initial Yahoo results")
        
        # Score the results based on how likely they are to be ToS pages
        scored_results = []
        
        for result in search_results:
            url = result.get("url", "")
            title = result.get("title", "").lower()
            description = result.get("description", "").lower()
            
            # Skip invalid results
            if not url or not title:
                continue
                
            # Skip URLs that are likely not ToS
            if "youtube.com/watch" in url or "wikipedia.org" in url:
                continue
                
            # Score the result based on title, description and URL
            tos_score = score_tos_url_by_path_specificity(url)
            
            # Title indicators
            tos_indicators = ["terms", "tos", "service", "agreement", "legal", "policy", "conditions"]
            for indicator in tos_indicators:
                if indicator in title:
                    tos_score += 10
            
            # Description indicators
            if description:
                for indicator in tos_indicators:
                    if indicator in description:
                        tos_score += 5
                        
            # Big boost for exact matches
            if any(phrase in title for phrase in ["terms of service", "terms and conditions", "user agreement"]):
                tos_score += 50
                
            # Check for user/customer focus
            if "user" in title or "customer" in title:
                tos_score += 20
                
            # Only include results with a minimum score
            if tos_score > 20:
                scored_results.append({
                    "url": url,
                    "title": title,
                    "score": tos_score
                })
        
        # Sort by score and select the best result
        if scored_results:
            # Sort by score (highest first)
            scored_results.sort(key=lambda x: x["score"], reverse=True)
            
            # Log the top results for debugging
            for i, result in enumerate(scored_results[:3]):
                print(f"{i+1}. ToS Candidate: '{result['title']}' - {result['url']} (Score: {result['score']})")
            
            # Return the highest scoring result
            return scored_results[0]["url"]
            
        return None
    except Exception as e:
        print(f"Error in Yahoo search fallback: {e}")
        return None


async def bing_search_fallback(query, page):
    """
    Search for ToS using Bing as a fallback method.
    Uses a single query string containing multiple terms.
    
    Args:
        query: Search query string including multiple terms (e.g. "example.com terms of service, terms of use")
        page: Playwright page to use for the search
    
    Returns:
        URL to ToS page if found, None otherwise
    """
    try:
        print("Attempting search engine fallback with Bing...")
        
        # Navigate to Bing search
        bing_search_url = f"https://www.bing.com/search?q={query}"
        await page.goto(bing_search_url, timeout=5000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)  # Wait for search results to load

        # Check for captcha or other blocking
        is_blocked = await page.evaluate(
            """() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            return {
                isCaptcha: html.includes('captcha') || 
                           html.includes('challenge') || 
                           html.includes('blocked') ||
                           html.includes('verify you are a human'),
                title: document.title,
                url: window.location.href
            };
        }"""
        )

        if is_blocked["isCaptcha"]:
            print(f"⚠️ Captcha or blocking detected on Bing: {is_blocked['title']}")
            print("Waiting for possible manual intervention...")
            # Wait longer to allow manual captcha solving if headless=False
            await page.wait_for_timeout(15000)

        # Extract search results from Bing
        search_results = await page.evaluate(
            r"""() => {
            // Bing uses different selectors depending on the layout
            const selectors = [
                'h2 a[href^="http"]',                  // Common Bing result title links
                '.b_algo h2 a[href^="http"]',          // Older Bing layout
                '.b_title a[href^="http"]',            // Another Bing pattern
                'li.b_algo cite',                      // URL citation fields
                'a[href^="http"]'                      // Fallback to all links (filtered later)
            ];
            
            // Try each selector and merge results
            let allLinks = [];
            for (const selector of selectors) {
                try {
                    const links = Array.from(document.querySelectorAll(selector));
                    if (links.length > 0) {
                        allLinks = allLinks.concat(links);
                    }
                } catch (e) {
                    // Ignore selector errors
                }
            }
            
            // Filter and process links
            const results = allLinks
                .filter(a => {
                    try {
                        const url = new URL(a.href);
                        const urlPath = url.pathname.toLowerCase();
                        
                        // Filter out search engines
                        if (url.href.includes('bing.com') ||
                            url.href.includes('google.com') ||
                            url.href.includes('yahoo.com') ||
                            url.href.includes('duckduckgo.com')) {
                            return false;
                        }
                        
                        // Filter out articles/blog posts by checking URL patterns
                        
                        // 1. Filter out date patterns (YYYY/MM/DD) commonly used in news/blog URLs
                        if (/\/\d{4}\/\d{1,2}\//.test(urlPath)) {
                            return false;
                        }
                        
                        // 2. Filter out URLs with long numeric IDs (often articles)
                        if (/\/\d{5,}/.test(urlPath)) {
                            return false;
                        }
                        
                        // 3. Filter out news/blog sections
                        if (urlPath.includes('/news/') || 
                            urlPath.includes('/blog/') || 
                            urlPath.includes('/article/') || 
                            urlPath.includes('/story/') ||
                            urlPath.includes('/posts/')) {
                            return false;
                        }
                        
                        // 4. Filter out forum/discussion content
                        if (/\/r\/\w+/.test(urlPath) || // Reddit
                            /\/t\/\w+/.test(urlPath) || // Discourse
                            urlPath.includes('/discussion/') ||
                            urlPath.includes('/forum/') ||
                            urlPath.includes('/thread/') ||
                            urlPath.includes('/comment/') ||
                            urlPath.includes('/viewtopic') ||
                            urlPath.includes('/showthread') ||
                            urlPath.includes('/profile/') ||
                            urlPath.includes('/user/')) {
                            return false;
                        }
                        
                        return true;
                    } catch (e) {
                        return false;
                    }
                })
                .map(a => {
                    // Get title and parent element for additional info
                    const title = a.textContent.trim();
                    
                    // Look for description in parent elements
                    let description = '';
                    let parent = a;
                    for (let i = 0; i < 5; i++) { // Look up to 5 levels up
                        parent = parent.parentElement;
                        if (!parent) break;
                        
                        // Try common Bing description selectors
                        const descEl = parent.querySelector('.b_caption p, .b_snippet, .b_dList');
                        if (descEl) {
                            description = descEl.textContent.trim();
                            break;
                        }
                        
                        // If this parent has enough text that's not just the title, use it
                        const parentText = parent.textContent.trim();
                        if (parentText.length > title.length + 30) {
                            description = parentText;
                            break;
                        }
                    }
                    
                    const isTermsTitle = 
                        title.toLowerCase().includes('terms') || 
                        title.toLowerCase().includes('tos') ||
                        title.toLowerCase().includes('agreement') || 
                        title.toLowerCase().includes('legal');
                        
                    const isTermsURL = 
                        a.href.toLowerCase().includes('/terms') || 
                        a.href.toLowerCase().includes('/tos') ||
                        a.href.toLowerCase().includes('/legal') || 
                        a.href.toLowerCase().includes('/agreement');
                
                    return {
                        url: a.href,
                        title: title,
                        description: description,
                        score: (isTermsTitle ? 20 : 0) + (isTermsURL ? 30 : 0)
                    };
                })
                .filter(item => item.title && item.url);
                
            return results;
        }""",
        )

        print(f"Found {len(search_results)} initial Bing results")
        
        # Now score and filter the results
        scored_results = []
        
        for result in search_results:
            url = result["url"]
            title = result["title"].lower()
            
            # Skip empty results
            if not url or not title:
                continue
                
            # Skip URLs that are likely not ToS
            if "youtube.com/watch" in url or "wikipedia.org" in url:
                continue
                
            # Generate a base score for ToS-like titles
            base_score = result.get("score", 0)
            
            # Check for ToS indicators in title
            tos_indicators = ["terms", "tos", "service", "agreement", "legal", "policy", "conditions"]
            for indicator in tos_indicators:
                if indicator in title:
                    base_score += 10
            
            # Big boost for exact matches
            if any(phrase in title for phrase in ["terms of service", "terms and conditions", "user agreement"]):
                base_score += 50
                
            # Check for user/customer focus
            if "user" in title or "customer" in title:
                base_score += 20
                
            # Score the URL paths, focusing on ToS-like paths
            path_score = score_tos_url_by_path_specificity(url)
            
            total_score = base_score + path_score
            
            # Only consider results with some relevance
            if total_score > 0:
                scored_results.append({
                    "url": url,
                    "title": title,
                    "score": total_score
                })
        
        # If we have scored results, return the best one
        if scored_results:
            # Sort by score (highest first)
            scored_results.sort(key=lambda x: x["score"], reverse=True)
            
            # Log the top results for debugging
            for i, result in enumerate(scored_results[:3]):
                print(f"{i+1}. ToS Candidate: '{result['title']}' - {result['url']} (Score: {result['score']})")
            
            # Return the highest scoring result
            return scored_results[0]["url"]
        
        return None
        
    except Exception as e:
        print(f"Error in Bing search fallback: {e}")
        return None



async def duckduckgo_search_fallback(search_query, page):
    """Search for terms of service using DuckDuckGo with a lighter implementation."""
    try:
        logger.info("Attempting search engine fallback with DuckDuckGo...")

        # Use the lite version for faster, simpler search
        ddg_search_url = f"https://lite.duckduckgo.com/lite?q={search_query}"

        # Navigate to DuckDuckGo with appropriate timeout
        await page.goto(ddg_search_url, timeout=5000, wait_until="domcontentloaded")
        
        # Wait for results to load with a fixed timeout
        await page.wait_for_timeout(2000)  # Consistent short timeout

        # Extract search results from DuckDuckGo's simplified HTML
        search_results = await page.evaluate(
            r"""(domain) => {
            // DuckDuckGo lite version uses simple HTML with tables
            const results = [];
            const allLinks = Array.from(document.querySelectorAll('a[href]'));
            
            // Extract domain from query for filtering
            const domainMatch = domain.match(/^(\S+?)(?:\s|$)/);
            const extractedDomain = domainMatch ? domainMatch[1] : '';
            
            // Filter for result links - DuckDuckGo lite has a simple structure
            for (const link of allLinks) {
                try {
                    const url = new URL(link.href);
                    
                    // Skip DuckDuckGo internal links
                    if (url.hostname.includes('duckduckgo.com')) {
                        continue;
                    }
                    
                    // Only include links to the target domain if we have a domain
                    if (extractedDomain && 
                        !url.hostname.includes(extractedDomain) && 
                        !extractedDomain.includes(url.hostname)) {
                        continue;
                    }
                    
                    // Score the result
                    let score = 0;
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href.toLowerCase();
                    
                    // Text-based scoring
                    if (text.includes('user agreement')) score += 70;
                    else if (text.includes('conditions of use')) score += 65;
                    else if (text.includes('terms of service')) score += 60;
                    else if (text.includes('terms and conditions')) score += 55;
                    else if (text.includes('terms')) score += 30;
                    else if (text.includes('legal')) score += 20;
                    
                    // URL-based scoring
                    if (href.includes('user-agreement')) score += 45;
                    else if (href.includes('conditions-of-use')) score += 43;
                    else if (href.includes('terms-of-service')) score += 40;
                    else if (href.includes('terms-and-conditions')) score += 38;
                    else if (href.includes('tos')) score += 35;
                    else if (href.includes('terms')) score += 20;
                    else if (href.includes('legal')) score += 20;
                    
                    // Only add results with a minimum score
                    if (score >= 30) {
                        results.push({
                            url: link.href,
                            title: text || url.hostname + url.pathname,
                            score: score
                        });
                    }
                } catch (e) {
                    // Skip problematic links
                    continue;
                }
            }
            
            // Sort and return top results
            return results.sort((a, b) => b.score - a.score).slice(0, 5);
        }""",
            search_query,
        )

        logger.info(f"DuckDuckGo search found {len(search_results)} potential ToS results")
        
        if len(search_results) > 0:
            # Add the path specificity score to the existing score
            for result in search_results:
                path_score = score_tos_url_by_path_specificity(result["url"])
                result["path_specificity_score"] = path_score
                result["final_score"] = result["score"] + path_score
                
                logger.info(f"ToS Candidate: '{result['title']}' - {result['url']}")
                logger.info(f"  Base Score: {result['score']}, Path Score: {path_score}, Final Score: {result['final_score']}")
            
            # Re-sort based on the combined scores
            search_results.sort(key=lambda x: x["final_score"], reverse=True)
            
            # Return highest scoring result if score is sufficient
            if search_results[0]["final_score"] >= 50:
                logger.info(f"Best DuckDuckGo ToS result: {search_results[0]['url']} (Score: {search_results[0]['final_score']})")
                return search_results[0]["url"]
            else:
                logger.info("No high-confidence ToS results found from DuckDuckGo")
        
        return None
    except Exception as e:
        logger.error(f"Error in DuckDuckGo search fallback: {e}")
        return None


def handle_navigation_failure(url: str, unverified_result: str = None) -> ToSResponse:
    """Handle cases where navigation to a terms page failed."""
    if unverified_result:
        return ToSResponse(
            url=url,
            tos_url=unverified_result,
            success=True,
            message="Navigation issues encountered, but found probable Terms of Service URL",
            method_used="partial_success"
        )
    return ToSResponse(
        url=url,
        tos_url=None,
        success=False,
        message="Failed to navigate to the site and find Terms of Service",
        method_used="navigation_failure"
    )

def handle_error(url: str, unverified_result: str, error: str) -> ToSResponse:
    """Handle errors with a fallback to unverified results if available."""
    return ToSResponse(
        url=url,
        tos_url=unverified_result,
        success=True,
        message=f"Error verifying Terms of Service URL, but found probable link (error: {error})",
        method_used="error_with_result"
    )

def prefer_main_domain(links, main_domain):
    # main_domain: e.g. "amazon.com"
    def is_main_domain(url):
        parsed = urlparse(url)
        # Accept www.amazon.com or amazon.com, but not foo.amazon.com
        return parsed.netloc == main_domain or parsed.netloc == f"www.{main_domain}"
    # Prefer main domain links, then others
    main_links = [l for l in links if is_main_domain(l)]
    return main_links if main_links else links

async def find_user_customer_terms_links(page):
    """
    Specially look for user/customer terms or agreement links with highest priority.
    This function is designed to find links specifically related to user or customer terms.
    """
    try:
        logger.info("Searching for user/customer terms links with HIGHEST PRIORITY...")
        
        # Evaluate the page for user/customer terms links
        links = await page.evaluate("""() => {
            // Find all anchor elements with href attributes
            const allLinks = Array.from(document.querySelectorAll('a[href]')).filter(link => 
                link.href && link.href.trim() !== '' && 
                !link.href.startsWith('javascript:') &&
                !link.href.includes('mailto:') &&
                !link.href.includes('tel:')
            );
            
            // Extract relevant info from each link
            return allLinks.map(link => ({
                element: link,
                text: link.textContent.trim(),
                href: link.href,
                id: link.id || '',
                classes: link.className || '',
                // Check if element contains "user" or "customer" and "terms" or "agreement"
                isUserTerms: (
                    (link.textContent.toLowerCase().includes('user') || 
                     link.textContent.toLowerCase().includes('customer')) &&
                    (link.textContent.toLowerCase().includes('terms') || 
                     link.textContent.toLowerCase().includes('agreement') ||
                     link.textContent.toLowerCase().includes('conditions'))
                ),
                isUserTermsHref: (
                    (link.href.toLowerCase().includes('user') || 
                     link.href.toLowerCase().includes('customer')) &&
                    (link.href.toLowerCase().includes('terms') || 
                     link.href.toLowerCase().includes('agreement') ||
                     link.href.toLowerCase().includes('conditions'))
                )
            }));
        }""")
        
        # Filter for user/customer terms links
        user_terms_links = []
        for link_info in links:
            link_text = link_info['text'].lower()
            link_href = link_info['href'].lower()
            
            # Assign a score to each link
            score = 0
            
            # Highest priority combinations in text - INCREASED VALUES
            if 'user terms' in link_text:
                score += 200 # Was 100
            elif 'customer terms' in link_text:
                score += 200 # Was 100
            elif 'user agreement' in link_text:
                score += 190 # Was 95
            elif 'customer agreement' in link_text:
                score += 190 # Was 95
            elif 'user conditions' in link_text:
                score += 180 # Was 90
            elif 'customer conditions' in link_text:
                score += 180 # Was 90
            # Individual terms in text - INCREASED VALUES
            elif 'user' in link_text and ('terms' in link_text or 'agreement' in link_text):
                score += 170 # Was 85
            elif 'customer' in link_text and ('terms' in link_text or 'agreement' in link_text):
                score += 160 # Was 80
            
            # URL patterns - INCREASED VALUES
            if 'user-terms' in link_href or 'customer-terms' in link_href:
                score += 150 # Was 75
            elif 'user-agreement' in link_href or 'customer-agreement' in link_href:
                score += 140 # Was 70
            elif ('user' in link_href or 'customer' in link_href) and ('terms' in link_href or 'agreement' in link_href):
                score += 130 # Was 65
            
            # Additional score for links in the footer (common location for ToS)
            if link_info.get('isUserTerms', False) or link_info.get('isUserTermsHref', False):
                score += 50 # Bonus for matching both conditions
            
            # Only include links with a minimum score - REDUCED THRESHOLD
            if score >= 40: # Was 65
                try:
                    link = await page.querySelector(f'a[href="{link_info["href"]}"]')
                    if link:
                        user_terms_links.append({"link": link, "text": link_text, "href": link_href, "score": score})
                except Exception as e:
                    logger.error(f"Error finding link element: {e}")
                    continue
        
        # Sort by score
        scored_links = sorted(user_terms_links, key=lambda x: x["score"], reverse=True)
        
        # Log details about high-scoring links
        for link in scored_links:
            logger.info(f"User/Customer Terms Link: '{link['text']}' - {link['href']} (Score: {link['score']})")
        
        # Try the highest scoring links
        for scored_link in scored_links[:3]:  # Try the top 3 links
            link = scored_link["link"]
            href = scored_link["href"]
            text = scored_link["text"]
            score = scored_link["score"]
            
            logger.info(f"Trying HIGHEST PRIORITY user/customer terms link: {text} - {href} (Score: {score})")
            try:
                success = await click_and_wait_for_navigation(page, link, timeout=3000) # Reduced timeout
                if success:
                    logger.info(f"Successfully navigated to USER/CUSTOMER terms link: {page.url}")
                    return page.url
            except Exception as e:
                logger.error(f"Error navigating to user/customer terms link: {e}")
                continue
        
        # If navigation failed for all links, return the best URL anyway
        if scored_links:
            best_link = scored_links[0]["href"]
            logger.info(f"Navigation failed, but returning best user/customer terms link: {best_link}")
            return best_link
        
        # Fallback to basic search if no links found with scoring method
        logger.info("No user/customer terms links found with high scoring, trying basic search...")
        fallback_links = await page.evaluate("""
            () => {
                const userTermsLinks = [];
                document.querySelectorAll('a[href]').forEach(link => {
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href;
                    
                    // Much broader matching for fallback
                    if ((text.includes('terms') || text.includes('agreement') || text.includes('conditions')) &&
                        href && href.trim() !== '' && 
                        !href.startsWith('javascript:') &&
                        !href.includes('mailto:') &&
                        !href.includes('tel:')) {
                        
                        userTermsLinks.push({
                            href: href,
                            text: text
                        });
                    }
                });
                return userTermsLinks;
            }
        """)
        
        if fallback_links and len(fallback_links) > 0:
            logger.info(f"Found basic terms link as fallback: {fallback_links[0]['text']} - {fallback_links[0]['href']}")
            return fallback_links[0]['href']
        
        logger.info("No user/customer terms links found")
        return None
    except Exception as e:
        logger.error(f"Error in user/customer terms search: {e}")
        return None


async def extract_app_store_privacy_link(page):
    """
    Extract privacy policy link from Apple App Store pages.
    App Store has a specific HTML structure for privacy policy links.
    """
    try:
        print("🍎 Checking if this is an Apple App Store page...")
        
        # Check if this is an App Store page
        is_app_store = await page.evaluate("""
            () => {
                return window.location.href.includes('apps.apple.com') || 
                       document.querySelector('meta[property="og:site_name"]')?.content?.includes('App Store');
            }
        """)
        
        if not is_app_store:
            print("Not an App Store page, skipping App Store specific extraction")
            return None
            
        print("✅ Detected Apple App Store page, looking for privacy policy link")
        
        # Extract developer info first to help filter out Apple links
        developer_info = await page.evaluate("""
            () => {
                // Get the developer name and website if available
                const developerElement = document.querySelector('.app-header__identity a');
                if (!developerElement) return null;
                
                const developerName = developerElement.textContent.trim();
                const developerLink = developerElement.href;
                
                return {
                    name: developerName,
                    link: developerLink
                };
            }
        """)
        
        if developer_info:
            print(f"📱 App developer: {developer_info['name']}")
            print(f"🔗 Developer link: {developer_info['link']}")
            
            # If the developer link is available and not an Apple domain, return it
            # as the base for finding privacy policy/ToS
            if developer_info['link'] and not 'apple.com' in developer_info['link']:
                print(f"✅ Using developer website as base for policy links: {developer_info['link']}")
                return developer_info['link']
        
        # Look for the privacy policy link in the app-privacy section
        privacy_link = await page.evaluate("""
            () => {
                // Target the app-privacy section
                const privacySection = document.querySelector('.app-privacy');
                if (!privacySection) return null;
                
                // Look for direct privacy policy link
                const directLink = privacySection.querySelector('a[href*="privacy"]');
                if (directLink && directLink.href) {
                    // Skip Apple domains
                    if (!directLink.href.includes('apple.com')) {
                        return { 
                            href: directLink.href,
                            text: directLink.textContent.trim(),
                            confidence: 'high'
                        };
                    }
                }
                
                // Fallback: look for any link in the privacy section
                const allLinks = Array.from(privacySection.querySelectorAll('a[href]'));
                const nonAppleLinks = allLinks.filter(link => !link.href.includes('apple.com'));
                
                if (nonAppleLinks.length > 0) {
                    return { 
                        href: nonAppleLinks[0].href,
                        text: nonAppleLinks[0].textContent.trim(),
                        confidence: 'medium'
                    };
                }
                
                // Second fallback: look for links in developer info section
                const devInfoSection = document.querySelector('.information-list--app');
                if (devInfoSection) {
                    const devLinks = Array.from(devInfoSection.querySelectorAll('a[href]'))
                        .filter(link => !link.href.includes('apple.com'));
                    
                    const privacyLink = devLinks.find(link => 
                        link.textContent.toLowerCase().includes('privacy') || 
                        link.href.toLowerCase().includes('privacy')
                    );
                    
                    if (privacyLink) {
                        return {
                            href: privacyLink.href,
                            text: privacyLink.textContent.trim(),
                            confidence: 'medium'
                        };
                    }
                    
                    // If we found any developer links but none are privacy policy,
                    # return the first one as a fallback
                    if (devLinks.length > 0) {
                        return {
                            href: devLinks[0].href,
                            text: devLinks[0].textContent.trim(),
                            confidence: 'low'
                        };
                    }
                }
                
                return null;
            }
        """)
        
        if privacy_link:
            print(f"🍏 Found privacy policy link in App Store: {privacy_link['text']} - {privacy_link['href']}")
            print(f"Confidence: {privacy_link['confidence']}")
            return privacy_link['href']
        
        print("No privacy policy link found in App Store page")
        return None
    except Exception as e:
        print(f"Error extracting App Store privacy link: {e}")
        return None


async def extract_play_store_privacy_link(page):
    """
    Extract privacy policy link from Google Play Store pages.
    """
    try:
        print("🤖 Checking if this is a Google Play Store page...")
            
        # Check if this is a Play Store page
        is_play_store = await page.evaluate("""
            () => {
                return window.location.href.includes('play.google.com/store/apps') || 
                       document.querySelector('meta[property="og:url"]')?.content?.includes('play.google.com');
            }
        """)
        
        if not is_play_store:
            print("Not a Play Store page, skipping Play Store specific extraction")
            return None
            
        print("✅ Detected Google Play Store page, looking for privacy policy link")
        
        # Now extract the privacy policy link
        privacy_link = await page.evaluate("""
            () => {
                // Get all divs containing text and links
                const allDivs = Array.from(document.querySelectorAll('div'));
                
                // Find divs with the specific developer policy text pattern
                for (const div of allDivs) {
                    const text = div.textContent?.toLowerCase() || '';
                    const hasDevPattern = text.includes('developer') && 
                                         text.includes('privacy');
                                       
                    if (hasDevPattern) {
                        // Look for a direct link inside this div
                        const links = Array.from(div.querySelectorAll('a[href]'));
                        
                        // Exclude Google domains
                        const nonGoogleLinks = links.filter(link => {
                            const href = link.href.toLowerCase();
                            return !href.includes('google.com') && 
                                   !href.includes('play.google.com') &&
                                   !href.includes('support.google.com') &&
                                   !href.includes('myaccount.google.com');
                        });
                        
                        if (nonGoogleLinks.length > 0) {
                            return {
                                href: nonGoogleLinks[0].href,
                                text: nonGoogleLinks[0].textContent.trim(),
                                confidence: 'high'
                            };
                        }
                    }
                }
                
                // Second approach: look for elements with "Developer" section that contains "Privacy Policy"
                const detailsElements = Array.from(document.querySelectorAll('a[href]'));
                
                // Extract all links that might be privacy policy
                const privacyLinks = detailsElements.filter(link => {
                    const href = link.href.toLowerCase();
                    const text = link.textContent.toLowerCase();
                    
                    // Check if this link is likely a privacy policy link
                    const isPrivacyLink = (text.includes('privacy') || href.includes('privacy')) &&
                                         !href.includes('google.com') &&
                                         !href.includes('play.google.com');
                                         
                    return isPrivacyLink;
                });
                
                if (privacyLinks.length > 0) {
                    return {
                        href: privacyLinks[0].href,
                        text: privacyLinks[0].textContent.trim(),
                        confidence: 'medium'
                    };
                }
                
                return null;
            }
        """)
        
        if privacy_link:
            print(f"🤖 Found developer privacy policy link in Play Store: {privacy_link['text']} - {privacy_link['href']}")
            print(f"Confidence: {privacy_link['confidence']}")
            return privacy_link['href']
        
        print("❌ No developer privacy policy link found in Play Store page")
        return None
        
    except Exception as e:
        print(f"Error extracting Play Store privacy link: {e}")
        return None


async def find_tos_via_privacy_policy(page, context):
    """
    Find ToS for App Store and Play Store links by:
    1. Finding privacy policy link
    2. Extracting base domain from privacy policy
    3. Using pattern replacement to find ToS URL
    4. If pattern replacement fails, navigate to the site and use regular ToS detection
    
    Returns:
        tuple of (tos_url, method_used, page)
    """
    # Check for Play Store or App Store
    is_app_store = await page.evaluate("""
        () => window.location.href.includes('apps.apple.com') || 
              document.querySelector('meta[property="og:site_name"]')?.content?.includes('App Store')
    """)
    
    is_play_store = await page.evaluate("""
        () => window.location.href.includes('play.google.com/store/apps') || 
              document.querySelector('meta[property="og:url"]')?.content?.includes('play.google.com')
    """)
    
    if not (is_app_store or is_play_store):
        print("Not an App Store or Play Store page, skipping app-specific ToS finder")
        return None, None, page
    
    print(f"✅ Detected {'App Store' if is_app_store else 'Play Store'} page, attempting ToS via privacy policy")
    
    # Create a custom context object instead of modifying the browser context
    store_context = {
        "came_from_app_store": is_app_store,
        "came_from_play_store": is_play_store
    }
    
    # Extract privacy policy link based on store type
    privacy_link = None
    if is_app_store:
        privacy_link = await extract_app_store_privacy_link(page)
    elif is_play_store:
        privacy_link = await extract_play_store_privacy_link(page)
    
    if not privacy_link:
        print("❌ Could not extract privacy policy link from store page")
        return None, None, page
    
    print(f"✅ Found privacy policy link: {privacy_link}")
    
    # Extract base URL from privacy policy without visiting it
    try:
        # Parse the privacy link to get the base domain
        parsed_url = urlparse(privacy_link)
        
        # Ensure this is not an Apple or Google domain
        if "apple.com" in parsed_url.netloc or "google.com" in parsed_url.netloc or "play.google.com" in parsed_url.netloc:
            print(f"❌ Privacy link is from {'Apple' if 'apple.com' in parsed_url.netloc else 'Google'} domain, not from app developer")
            return None, None, page
        
        # Try to guess the ToS URL based on the privacy URL patterns
        privacy_url_lower = privacy_link.lower()
        
        # Direct replacement of "privacy" with "terms" in the original URL
        if "privacy" in privacy_url_lower:
            # Try several possible replacements based on common patterns
            possible_replacements = [
                ("privacy", "terms"),
                ("privacy", "tos"),
                ("privacy-policy", "terms-of-service"),
                ("privacy-policy", "terms-of-use"),
                ("privacy-policy", "terms"),
                ("privacy/policy", "terms"),
                ("privacy_policy", "terms_of_service")
            ]
            
            for old_pattern, new_pattern in possible_replacements:
                if old_pattern in privacy_url_lower:
                    tos_url = privacy_url_lower.replace(old_pattern, new_pattern)
                    if tos_url != privacy_url_lower:  # Only if it actually changed something
                        print(f"✅ Created ToS URL by replacing '{old_pattern}' with '{new_pattern}': {tos_url}")
                        return tos_url, "app_store_privacy_to_tos_pattern_replacement", page
        
        # If we can't do a simple pattern replacement, we'll navigate to the base domain
        # and use regular ToS detection methods instead of returning the privacy URL
        base_domain = parsed_url.netloc
        base_url = f"{parsed_url.scheme}://{base_domain}"
        
        print(f"⚠️ Pattern replacement failed. Navigating to base URL: {base_url}")
        
        # Save the current URL so we can return to it if needed
        current_url = await page.evaluate("() => window.location.href")
        
        # Navigate to the base URL and use regular ToS detection
        success, _, _ = await navigate_with_retry(page, base_url, max_retries=2)
        if not success:
            print(f"❌ Failed to navigate to {base_url}")
            # Try to navigate back to the original page
            try:
                await page.goto(current_url, timeout=5000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"❌ Error returning to original page: {e}")
            return None, None, page
        
        print("🔍 Looking for ToS on developer website...")
        
        # First check for user/customer terms links with high priority
        user_terms_link = await find_user_customer_terms_links(page)
        if user_terms_link:
            print(f"✅ Found user/customer terms link on developer site: {user_terms_link}")
            return user_terms_link, "app_store_base_domain_user_terms", page
        
        # Try JavaScript method
        js_result, page, js_unverified = await find_all_links_js(page, store_context, None)
        if js_result:
            # Check if the found link is from an Apple or Google domain
            js_result_domain = urlparse(js_result).netloc
            if (is_app_store and "apple.com" in js_result_domain) or (is_play_store and ("google.com" in js_result_domain or "play.google.com" in js_result_domain)):
                print(f"❌ Found link is from {'Apple' if 'apple.com' in js_result_domain else 'Google'} domain, not from app developer")
                # Do not return this link
            else:
                print(f"✅ Found ToS link via JavaScript method on developer site: {js_result}")
                return js_result, "app_store_base_domain_js", page
        
        # Try scroll method
        scroll_result, page, scroll_unverified = await smooth_scroll_and_click(page, store_context, js_unverified)
        if scroll_result:
            # Check if the found link is from an Apple or Google domain
            scroll_result_domain = urlparse(scroll_result).netloc
            if (is_app_store and "apple.com" in scroll_result_domain) or (is_play_store and ("google.com" in scroll_result_domain or "play.google.com" in scroll_result_domain)):
                print(f"❌ Found link is from {'Apple' if 'apple.com' in scroll_result_domain else 'Google'} domain, not from app developer")
                # Do not return this link
            else:
                print(f"✅ Found ToS link via scroll method on developer site: {scroll_result}")
                return scroll_result, "app_store_base_domain_scroll", page
        
        # If all else fails, return to the app store page
        print("❌ Could not find ToS on developer website")
        try:
            await page.goto(current_url, timeout=5000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"❌ Error returning to original page: {e}")
        
        return None, None, page
        
    except Exception as e:
        print(f"❌ Error finding ToS via privacy policy: {e}")
        return None, None, page

def is_app_store_url(url: str) -> bool:
    """Check if the URL is from Apple App Store."""
    return "apps.apple.com" in url or "itunes.apple.com" in url

def is_play_store_url(url: str) -> bool:
    """Check if the URL is from Google Play Store."""
    return "play.google.com" in url or "play.app.goo.gl" in url

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


async def find_tos_via_html_inspection(url: str) -> str:
    """
    Find ToS page by inspecting HTML content.
    
    Args:
        url: URL of the website to inspect
    
    Returns:
        URL to ToS page if found, None otherwise
    """
    try:
        # Set up browser to inspect HTML
        playwright = await async_playwright().start()
        browser, browser_context, page, _ = await setup_browser(playwright)
        
        try:
            # Navigate to the URL with reduced timeout for better performance
            success, _, _ = await navigate_with_retry(page, url, max_retries=1)
            if not success:
                logger.warning(f"Failed to navigate to URL in HTML inspection: {url}")
                return None
                
            # Use optimized JavaScript to search for ToS links in the HTML content
            logger.info("Searching for ToS links with improved pattern matching...")
            start_time = time.time()
            
            links = await page.evaluate("""
                () => {
                    // More comprehensive terms list for better matching
                    const tosTerms = [
                        'user agreement',
                        'customer agreement',
                        'user terms', 
                        'customer terms',
                        'terms of service', 
                        'terms of use', 
                        'terms and conditions',
                        'terms & conditions', 
                        'legal terms', 
                        'legal agreement',
                        'terms',
                        'conditions of use',
                        'legal notices'
                    ];
                    
                    // Use an optimized selector to filter links before processing
                    const allLinks = document.querySelectorAll(
                        'a[href]:not([href^="javascript:"]):not([href*="mailto:"]):not([href*="tel:"])'
                    );
                    
                    // Score system for better link matching
                    const scoredLinks = [];
                    
                    for (const link of allLinks) {
                        const text = link.textContent.trim().toLowerCase();
                        const href = link.href.toLowerCase();
                        
                        // Skip empty links or irrelevant protocols
                        if (!href || (!href.startsWith('http') && !href.startsWith('/') && !href.startsWith('./') && !href.startsWith('../'))) {
                            continue;
                        }
                        
                        let score = 0;
                        let matchedTerm = '';
                        
                        // Text content matching with weighted scoring
                        for (let i = 0; i < tosTerms.length; i++) {
                            const term = tosTerms[i];
                            if (text === term) {
                                // Exact match gets highest score
                                score += 100 - (i * 2); 
                                matchedTerm = term;
                                break;
                            } else if (text.includes(term)) {
                                // Partial match gets lesser score
                                score += 60 - (i * 2);
                                matchedTerm = term;
                                // Don't break, keep checking for exact matches
                            }
                        }
                        
                        // Highest priority for user/customer terms
                        if (text.includes('user agreement')) {
                            score += 200; // Significantly higher priority
                            if (!matchedTerm) matchedTerm = 'user agreement';
                        }
                        if (text.includes('customer agreement')) {
                            score += 200; // Significantly higher priority
                            if (!matchedTerm) matchedTerm = 'customer agreement';
                        }
                        if (text.includes('user terms')) {
                            score += 180; // Significantly higher priority
                            if (!matchedTerm) matchedTerm = 'user terms';
                        }
                        if (text.includes('customer terms')) {
                            score += 180; // Significantly higher priority
                            if (!matchedTerm) matchedTerm = 'customer terms';
                        }
                        
                        // Additional bonus for having 'terms' in both text and URL
                        if (text.includes('terms') && href.includes('terms')) {
                            score += 25;
                        }
                        
                        // Boost score for footer links (often where ToS is found)
                        if (link.closest('footer, [id*="foot"], [class*="foot"]')) {
                            score *= 1.2; // 20% boost
                        }
                        
                        // Boost for links that are in containers with legal-related terms
                        const parentText = link.parentElement ? link.parentElement.textContent.toLowerCase() : '';
                        if (parentText.includes('legal') || parentText.includes('policy') || parentText.includes('terms')) {
                            score += 15;
                        }
                        
                        // Only include links with some relevance
                        if (score > 0) {
                            scoredLinks.push({
                                href: href,
                                text: text,
                                score: score,
                                match: matchedTerm
                            });
                        }
                    }
                    
                    // Sort by score (highest first)
                    scoredLinks.sort((a, b) => b.score - a.score);
                    
                    // Return the top candidates
                    return scoredLinks.slice(0, 5);
                }
            """)
            
            end_time = time.time()
            logger.info(f"Link scoring completed in {end_time - start_time:.2f} seconds")
            
            if links and len(links) > 0:
                # Log the found links
                logger.info(f"Found {len(links)} potential ToS links:")
                for idx, link in enumerate(links):
                    logger.info(f"Link #{idx+1}: '{link.get('text', '')}' - {link.get('href', '')} (Score: {link.get('score', 0)})")
                
                # Return the best link
                best_link = links[0]['href']
                logger.info(f"Selected best ToS link: {best_link}")
                return best_link
                
            logger.info("No potential Terms of Service links found during HTML inspection")
            return None
        finally:
            # Ensure browser resources are cleaned up
            await browser_context.close()
            await browser.close()
            await playwright.stop()
            
    except Exception as e:
        logger.error(f"Error during HTML inspection: {e}")
        return None


async def standard_search_fallback(search_query, page):
    """
    Runs a standard search approach using Bing as primary with fallbacks to other engines.
    
    Args:
        search_query: Search query string for finding ToS
        page: Playwright page to use for searches
        
    Returns:
        Tuple of (best_url, search_results_dict, empty_list)
        - best_url: The most likely ToS URL found
        - search_results_dict: Dictionary containing the search engine used and the result
        - empty_list: Empty list for compatibility with old consensus links return
    """
    print(f"Running standard search for: {search_query}")
    
    # Dictionary to store result
    search_results = {}
    best_url = None
    
    # Try Bing first
    try:
        print("Trying Bing search...")
        bing_url = await bing_search_fallback(search_query, page)
        if bing_url:
            best_url = bing_url
            search_results["bing"] = bing_url
            print(f"Found ToS via Bing: {bing_url}")
            # If we find a good result with Bing, return it immediately
            if score_tos_url_by_path_specificity(bing_url) > 50:
                return best_url, search_results, []
    except Exception as e:
        print(f"Error in Bing search: {e}")
    
    # Try Yahoo if Bing failed or gave a low-quality result
    if not best_url or score_tos_url_by_path_specificity(best_url) < 30:
        try:
            print("Trying Yahoo search...")
            yahoo_url = await yahoo_search_fallback(search_query, page)
            if yahoo_url:
                search_results["yahoo"] = yahoo_url
                print(f"Found ToS via Yahoo: {yahoo_url}")
                
                # Replace our best URL if Yahoo's result is better or if we don't have one yet
                if not best_url or score_tos_url_by_path_specificity(yahoo_url) > score_tos_url_by_path_specificity(best_url):
                    best_url = yahoo_url
        except Exception as e:
            print(f"Error in Yahoo search: {e}")
    
    # Try DuckDuckGo as last resort
    if not best_url or score_tos_url_by_path_specificity(best_url) < 30:
        try:
            print("Trying DuckDuckGo search...")
            ddg_url = await duckduckgo_search_fallback(search_query, page)
            if ddg_url:
                search_results["duckduckgo"] = ddg_url
                print(f"Found ToS via DuckDuckGo: {ddg_url}")
                
                # Replace our best URL if DuckDuckGo's result is better or if we don't have one yet
                if not best_url or score_tos_url_by_path_specificity(ddg_url) > score_tos_url_by_path_specificity(best_url):
                    best_url = ddg_url
        except Exception as e:
            print(f"Error in DuckDuckGo search: {e}")
    
    # If we found any results
    if best_url:
        # Extract the domain being searched for to check for domain match
        search_domain = None
        domain_match = re.search(r'^(\S+?)(?:\s|$)', search_query)
        if domain_match:
            search_domain = domain_match.group(1).lower()
            # Clean up domain (remove https://, www., etc.)
            search_domain = re.sub(r'^https?://(www\.)?', '', search_domain)
            # Remove trailing slash and path
            search_domain = search_domain.split('/')[0]
            
            # Extra verification for domain match
            url_domain = urlparse(best_url).netloc.lower()
            url_domain_clean = re.sub(r'^www\.', '', url_domain)
            
            # If the result domain doesn't match search domain at all, 
            # but we're confident it's a ToS page, still return it
            if search_domain and search_domain not in url_domain_clean:
                tos_score = score_tos_url_by_path_specificity(best_url)
                if tos_score < 70:  # If not very confident, log a warning
                    print(f"Warning: Found ToS URL domain {url_domain_clean} doesn't match search domain {search_domain}")
                    print(f"URL: {best_url}, ToS score: {tos_score}")
                    # Still return it as we might not find anything better
        
        print(f"Best search result: {best_url}")
        engines_used = ", ".join(search_results.keys())
        print(f"Search engines used: {engines_used}")
        
        return best_url, search_results, []
    
    print("No results found from any search engine")
    return None, {}, []
