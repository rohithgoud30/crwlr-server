import random
from urllib.parse import urlparse
import re
import traceback
import time
import logging
import aiohttp
from fastapi import APIRouter, HTTPException, status
from playwright.async_api import async_playwright

from app.models.tos import ToSRequest, ToSResponse

# Set up logging
logger = logging.getLogger(__name__)

router = APIRouter()

# Define User_Agents list for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# Priorities for exact match terms
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


@router.post("/tos", response_model=ToSResponse, status_code=status.HTTP_200_OK)
async def find_tos(request: ToSRequest) -> ToSResponse:
    """
    Find the Terms of Service URL for a given website.
    """
    logger.info(f"Processing request for URL: {request.url}")
    start_time = time.time()

    try:
        # Validate and normalize URL
        url = request.url.strip()
        
        sanitized_url = sanitize_url(url)
        if not sanitized_url:
            logger.warning(f"Invalid URL provided: {url}")
            return ToSResponse(
                url=url,
                success=False,
                message="Invalid URL format",
                method_used="validation"
            )

        url = normalize_url(url)
        
        # Check if this is an app store URL
        if is_app_store_url(url):
            logger.info(f"Detected App Store URL: {url}")
            tos_url = await find_app_store_tos(url)
            if tos_url:
                return ToSResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message="Terms of Service found via App Store",
                    method_used="app_store"
                )
        
        if is_play_store_url(url):
            logger.info(f"Detected Play Store URL: {url}")
            tos_url = await find_play_store_tos(url)
            if tos_url:
                return ToSResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message="Terms of Service found via Play Store",
                    method_used="play_store"
                )

        # Try to find ToS via common paths/URLs
        tos_url = await find_tos_via_common_paths(url)
        if tos_url:
            logger.info(f"Found ToS via common paths: {tos_url}")
            return ToSResponse(
                url=url,
                tos_url=tos_url,
                success=True,
                message="Terms of Service found via common paths",
                method_used="common_paths"
            )
        
        # Use direct HTML inspection approach
        playwright = await async_playwright().start()
        browser, browser_context, page, _ = await setup_browser(playwright)
        
        try:
            # Navigate to the URL
            success, _, _ = await navigate_with_retry(page, url)
            if not success:
                logger.warning(f"Failed to navigate to URL: {url}")
                return ToSResponse(
                    url=url,
                    success=False,
                    message="Failed to navigate to URL",
                    method_used="navigation_failed"
                )
                
            # Try to find ToS via HTML inspection
            tos_url = await find_tos_via_html_inspection(page)
            if tos_url:
                logger.info(f"Found ToS via HTML inspection: {tos_url}")
                return ToSResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message="Terms of Service found via HTML inspection",
                    method_used="html_inspection"
                )
                
            # Try to find ToS via privacy policy check
            context = {}  # Create an empty context dictionary
            tos_url = await find_tos_via_privacy_policy(page, context)
            if tos_url:
                logger.info(f"Found ToS via privacy policy check: {tos_url}")
                return ToSResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message="Terms of Service found via privacy policy",
                    method_used="privacy_policy"
                )
                
            # Try to find user agreement or customer terms links
            tos_url = await find_user_terms(page)
            if tos_url:
                logger.info(f"Found ToS via user/customer terms: {tos_url}")
                return ToSResponse(
                    url=url,
                    tos_url=tos_url,
                    success=True,
                    message="Terms of Service found via user/customer terms",
                    method_used="user_terms"
                )
                
            # If we get here, we couldn't find the ToS
            logger.warning(f"Could not find ToS for URL: {url}")
            return ToSResponse(
                url=url,
                success=False,
                message="Could not find Terms of Service URL",
                method_used="none"
            )
        finally:
            # Ensure browser resources are cleaned up
            await browser_context.close()
            await browser.close()
            await playwright.stop()
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        return ToSResponse(
            url=request.url,
            success=False,
            message=f"Error: {str(e)}",
            method_used="error"
        )
    finally:
        end_time = time.time()
        logger.info(f"Total processing time: {end_time - start_time:.2f} seconds")


async def setup_browser(playwright=None):
    """
    Setup browser with optimized configurations for performance.
    """
    if not playwright:
        playwright = await async_playwright().start()
    try:
        # Use random user agent
        user_agent = random.choice(USER_AGENTS)

        # Get headless setting from environment or default to True for performance
        # Set to False only for debugging when needed
        headless = True

        print(f"Browser headless mode: {headless}")

        # Launch browser with optimized settings
        browser = await playwright.chromium.launch(
            headless=headless,  # Set to True for better performance
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-infobars",
                "--window-size=1920,1080",  # Maximum window size for better rendering
                "--disable-extensions",
                "--disable-features=site-per-process",  # For memory optimization
            ],
            chromium_sandbox=False,
            slow_mo=20,  # Reduced delay for better performance
        )

        # Create context with optimized settings
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},  # Maximum viewport size
            user_agent=user_agent,
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
        )

        # Add minimal stealth script
        await context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """
        )

        # Create a page
        page = await context.new_page()

        # Random delay function with shorter times for performance
        async def random_delay(min_ms=100, max_ms=300):
            delay = random.randint(min_ms, max_ms)
            await page.wait_for_timeout(delay)

        # Set reasonable timeouts
        page.set_default_timeout(10000)  # Reduced timeout

        return browser, context, page, random_delay

    except Exception as e:
        if "playwright" in locals():
            await playwright.stop()
        print(f"Error setting up browser: {e}")
        raise


async def navigate_with_retry(page, url, max_retries=2):
    """Navigate to URL with optimized retry logic."""
    for attempt in range(max_retries):
        try:
            # Add shorter random delay between attempts
            if attempt > 0:
                delay = random.randint(300, 500)  # Reduced delay
                print(f"Waiting {delay/1000}s before retry {attempt+1}...")
                await page.wait_for_timeout(delay)

            print(f"Navigation attempt {attempt+1}/{max_retries} to {url}")

            # Optimized navigation strategy with shorter timeout
            response = await page.goto(url, timeout=5000, wait_until="domcontentloaded")

            # Quick check for anti-bot measures
            is_anti_bot, patterns = await detect_anti_bot_patterns(page)
            if is_anti_bot:
                if attempt < max_retries - 1:
                    print(
                        f"Detected anti-bot protection, trying alternative approach..."
                    )
                    continue
                else:
                    print("All navigation attempts blocked by anti-bot protection")
                    return False, response, patterns

            # Check HTTP status
            if response.ok:
                print(f"Navigation successful: HTTP {response.status}")
                return True, response, []
            else:
                print(f"Received HTTP {response.status}")
        except Exception as e:
            print(f"Navigation error: {e}")

    print("All navigation attempts failed")
    return False, None, []


async def detect_anti_bot_patterns(page):
    """
    Optimized anti-bot detection that runs faster.
    """
    try:
        # Simplified check for common anti-bot patterns
        anti_bot_patterns = await page.evaluate(
            """() => {
        const html = document.documentElement.innerHTML.toLowerCase();
            
            // Check for common anti-bot keywords
            const isCloudflare = html.includes('cloudflare') && 
                                (html.includes('security check') || 
                                 html.includes('challenge'));
            const isRecaptcha = html.includes('recaptcha');
            const isHcaptcha = html.includes('hcaptcha');
            const isBotDetection = html.includes('bot detection') || 
                                  (html.includes('please wait') && 
                                   html.includes('redirecting'));
                
            return {
                isAntiBot: isCloudflare || isRecaptcha || isHcaptcha || isBotDetection,
                url: window.location.href,
                title: document.title
            };
    }"""
        )

        if anti_bot_patterns["isAntiBot"]:
            print(f"\n⚠️ Detected anti-bot protection: recaptcha")
            print(f"  URL: {anti_bot_patterns['url']}")
            print(f"  Title: {anti_bot_patterns['title']}")
            return True, ["bot_protection"]

        return False, []
    except Exception as e:
        print(f"Error detecting anti-bot patterns: {e}")
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
    """Click a link and wait for navigation with shorter timeout."""
    try:
        async with page.expect_navigation(
            timeout=timeout, wait_until="domcontentloaded"
        ):
            await element.click()
        return True
    except Exception as e:
        print(f"Navigation error: {e}")
        return False


async def smooth_scroll_and_click(
    page, context, unverified_result=None, step=200, delay=50
):
    """
    Smooth scroll through the page to find ToS links.
    """
    try:
        print("🔃 Starting smooth scroll with strong term matching...")
        
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
        
        # Try to find the footer first as it often contains ToS links
        footer_selector = await page.evaluate("""
            () => {
                const footers = document.querySelectorAll('footer, [id*="foot"], [class*="foot"]');
                return footers.length > 0 ? 'footer, [id*="foot"], [class*="foot"]' : null;
            }
        """)
        
        if footer_selector:
            print(f"Found footer with selector: {footer_selector}")
            # Just scroll to the footer and look for ToS links there
            await page.evaluate(f"document.querySelector('{footer_selector}').scrollIntoView()")
            await page.wait_for_timeout(500)  # Short wait for any animations
        
        # Get all links that match ToS patterns
        links = await page.evaluate("""
            () => {
                const links = [];
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
                    
                    if (score >= 60) {
                        links.push({
                            text: text,
                            href: link.href,
                            score: score
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
        
        # Sort links by score
        filtered_links.sort(key=lambda x: x['score'], reverse=True)
        
        # If we have links, return the best one without navigating
        if filtered_links:
            best_link = filtered_links[0]['href']
            best_score = filtered_links[0]['score']
            
            # Skip full-page scrolling since we already found high-confidence links
            print(f"Returning best link without navigation: {best_link} (Score: {best_score})")
            return best_link, page, unverified_result
        
        print("✅ Reached the bottom of the page.")

        return None, page, unverified_result

    except Exception as e:
        print(f"Error in smooth scroll: {e}")
        return None, page, unverified_result


def is_likely_registration_or_login_page(url: str) -> bool:
    """
    Check if a URL is likely a registration, login or authentication page.

    Args:
        url: URL to check

    Returns:
        bool: True if URL is likely a registration/login page
    """
    url_lower = url.lower()

    # Patterns that indicate registration or login pages
    suspicious_patterns = [
        "/register",
        "/signup",
        "/login",
        "/signin",
        "/registration",
        "/create-account",
        "/join",
        "/auth/",
        "/openid",
        "/ap/register",
        "registration",
        "signup",
        "create_account",
        "createaccount",
        "authentication",
        "returnto",
        "redirect",
        "callback",
    ]

    # Check if URL contains any suspicious patterns
    return any(pattern in url_lower for pattern in suspicious_patterns)


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


async def yahoo_search_fallback(domain, page):
    """Search for terms of service using Yahoo Search."""
    try:
        print("Attempting search engine fallback with Yahoo...")

        # Create a search query for the domain with specific site constraint
        search_query = f'site:{domain} ("terms of service" OR "terms of use" OR "user agreement" OR "legal terms")'
        yahoo_search_url = f"https://search.yahoo.com/search?p={search_query}"

        # Navigate to Yahoo search with longer timeout
        await page.goto(yahoo_search_url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)  # Longer wait for results to load

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
            print(f"⚠️ Captcha or blocking detected on Yahoo: {is_blocked['title']}")
            print("Waiting for possible manual intervention...")
            # Wait longer to allow manual captcha solving if headless=False
            await page.wait_for_timeout(15000)

        # Extract search results with multiple selector options for robustness
        search_results = await page.evaluate(
            r"""(domain) => {
            // Yahoo uses different selectors depending on the layout
            const selectors = [
                'a.d-ib.fz-20.lh-26.td-hu.tc',              // Standard Yahoo results
                '.algo-sr a[href^="http"]',                 // Alternative Yahoo results
                'h3.title a[href^="http"]',                 // Older Yahoo layout
                'div.compTitle a[href^="http"]',            // Yahoo component layout
                'a[href^="http"].lb-title',                 // Another Yahoo pattern
                'a[href^="http"]'                           // Fallback to all links (filtered later)
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
                        
                        // Only include links to the target domain
                        if (!url.hostname.includes(domain) && !domain.includes(url.hostname)) {
                            return false;
                        }
                        
                        // Filter out search engines and unrelated domains
                        if (url.href.includes('yahoo.com') ||
                            url.href.includes('google.com') ||
                            url.href.includes('bing.com') ||
                            url.href.includes('duckduckgo.com')) {
                            return false;
                        }
                        
                        // Filter out articles/blog posts by checking URL patterns
                        
                        // 1. Filter out date patterns (YYYY/MM/DD) commonly used in news/blog URLs
                        if (/\/\d{4}\/\d{1,2}\/\d{1,2}\//.test(urlPath)) {
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
                        
                        // 4. Filter out URLs with dates in them (likely articles)
                        const segments = urlPath.split('/').filter(s => s.length > 0);
                        if (segments.length > 2 && /^\d{4}$/.test(segments[0])) {
                            return false;
                        }
                        
                        return true;
                    } catch (e) {
                        return false;
                    }
                })
                .map(a => {
                    // Get title from the element
                    const title = a.textContent.trim();
                    
                    // Look for description in parent elements
                    let description = '';
                    let parent = a;
                    for (let i = 0; i < 5; i++) { // Look up to 5 levels up
                        parent = parent.parentElement;
                        if (!parent) break;
                        
                        // Try common Yahoo description selectors
                        const descEl = parent.querySelector('.compText, .ac-1st, .fz-ms');
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
                    
                    // Score based on title, description and URL
                    let score = 0;
                    const titleLower = title.toLowerCase();
                    const descLower = description ? description.toLowerCase() : '';
                    const urlLower = a.href.toLowerCase();
                    
                    // Scoring for title matches (prioritize user agreement and conditions of use)
                    if (titleLower.includes('user agreement') || titleLower.includes('users agreement') || titleLower.includes('user agremment')) score += 70;
                    else if (titleLower.includes('conditions of use') || titleLower.includes('condition of use')) score += 65;
                    else if (titleLower.includes('terms of service')) score += 60;
                    else if (titleLower.includes('terms and conditions')) score += 55;
                    else if (titleLower.includes('terms')) score += 30;
                    else if (titleLower.includes('conditions')) score += 30;
                    else if (titleLower.includes('legal')) score += 25;
                    
                    // Scoring for description matches
                    if (descLower.includes('terms of service')) score += 30;
                    else if (descLower.includes('terms and conditions')) score += 28;
                    else if (descLower.includes('user agreement')) score += 30;
                    else if (descLower.includes('conditions of use')) score += 33;
                    else if (descLower.includes('legal terms')) score += 24;
                    else if (descLower.includes('terms')) score += 15; // Lower priority
                    
                    // URL-based scoring
                    if (urlLower.includes('user-agreement') || urlLower.includes('useragreement')) score += 45;
                    else if (urlLower.includes('conditions-of-use') || urlLower.includes('conditionsofuse')) score += 43;
                    else if (urlLower.includes('terms-of-service')) score += 40;
                    else if (urlLower.includes('terms-and-conditions')) score += 38;
                    else if (urlLower.includes('tos')) score += 35;
                    else if (urlLower.includes('terms')) score += 20; // Lower priority for generic terms
                    else if (urlLower.includes('legal')) score += 20;
                    
                    return {
                        url: a.href,
                        title: title || "No Title",  // Fallback title
                        description: description,
                        score: score
                    };
                })
                .filter(result => 
                    result.score > 0 || 
                    result.url.toLowerCase().includes('terms') || 
                    result.url.toLowerCase().includes('tos') || 
                    result.url.toLowerCase().includes('legal')
                );
                
            // Deduplicate results based on URL
            const uniqueResults = [];
            const seenUrls = new Set();
            
            for (const result of results) {
                if (!seenUrls.has(result.url)) {
                    seenUrls.add(result.url);
                    uniqueResults.push(result);
                }
            }
            
            return uniqueResults;
        }""",
            domain,
        )

        if search_results and len(search_results) > 0:
            print(f"Found {len(search_results)} potential results from Yahoo search")

            # Sort by score
            search_results.sort(key=lambda x: x["score"], reverse=True)

            # Store best result in case all verifications fail
            best_result_url = search_results[0]["url"]
            best_result_score = search_results[0]["score"]

            # Display top results for debugging
            for i, result in enumerate(search_results[:3]):
                print(
                    f"Result #{i+1}: {result['title']} - {result['url']} (Score: {result['score']})"
                )

            # Check the top 5 results instead of just 3
            for result_index in range(min(5, len(search_results))):
                best_result = search_results[result_index]["url"]

                try:
                    # Visit the page to verify it's a terms page
                    print(f"Checking result: {best_result}")
                    await page.goto(
                        best_result, timeout=10000, wait_until="domcontentloaded"
                    )
                    await page.wait_for_timeout(
                        2000
                    )  # Increased wait time for full page load

                    # Check if we hit a captcha or "just a moment" page
                    is_captcha = await page.evaluate(
                        """() => {
                        const html = document.documentElement.innerHTML.toLowerCase();
                        const title = document.title.toLowerCase();
                        return {
                            isCaptcha: html.includes('captcha') || 
                                      html.includes('challenge') || 
                                      html.includes('security check') ||
                                      html.includes('please verify') ||
                                      html.includes('just a moment') ||
                                      title.includes('just a moment'),
                            title: document.title,
                            url: window.location.href
                        };
                    }"""
                    )

                    if is_captcha["isCaptcha"]:
                        print(
                            f"⚠️ Captcha/protection detected when accessing: {is_captcha['url']}"
                        )

                        # If this is a high-scoring result from the target domain, accept it even with captcha
                        original_url_lower = best_result.lower()

                        # Look for terms indicators in URL
                        has_terms_path = (
                            "policies" in original_url_lower
                            or "terms-of-service" in original_url_lower
                            or "terms-of-use" in original_url_lower
                            or "user-agreement" in original_url_lower
                            or "terms" in original_url_lower
                        )

                        if (
                            domain in original_url_lower
                            and has_terms_path
                            and search_results[result_index]["score"] >= 60
                        ):
                            print(
                                f"✅ Accepting URL from target domain despite protection: {best_result}"
                            )
                            return best_result

                    # Perform two-stage verification for more confidence
                    # First stage: comprehensive verification
                    verification = await verify_is_terms_page(page)

                    if (
                        verification["isTermsPage"] and verification["confidence"] >= 60
                    ):  # Increased threshold
                        print(
                            f"✅ First verification passed: {page.url} (score: {verification['confidence']})"
                        )

                        # Second stage: Double-check specific content markers
                        content_markers = await page.evaluate(
                            """() => {
                            const html = document.body.innerText.toLowerCase();
                            return {
                                hasAgreementText: html.includes('by using') && html.includes('agree'),
                                hasLegalDefinitions: html.includes('definitions') || html.includes('defined terms'),
                                hasLegalSections: html.includes('governing law') || html.includes('jurisdiction'),
                                hasTosSpecificText: html.includes('terms of service') || 
                                                   html.includes('terms and conditions') || 
                                                   html.includes('user agreement')
                            };
                        }"""
                        )

                        # Calculate second-stage confidence
                        second_stage_confidence = 0
                        if content_markers["hasAgreementText"]:
                            second_stage_confidence += 25
                        if content_markers["hasLegalDefinitions"]:
                            second_stage_confidence += 25
                        if content_markers["hasLegalSections"]:
                            second_stage_confidence += 25
                        if content_markers["hasTosSpecificText"]:
                            second_stage_confidence += 25

                        if second_stage_confidence >= 50:  # At least two strong markers
                            print(
                                f"✅✅ Complete verification passed (secondary score: {second_stage_confidence}/100)"
                            )
                            return page.url
                        else:
                            print(
                                f"⚠️ Secondary verification failed (score: {second_stage_confidence}/100)"
                            )
                    else:
                        print(
                            f"❌ Primary verification failed (score: {verification['confidence']})"
                        )
                except Exception as e:
                    print(f"Error checking Yahoo search result: {e}")

            # If we checked all results but none were verified, only return highest scored result
            # if it meets a very high threshold
            if (
                len(search_results) > 0 and search_results[0]["score"] >= 80
            ):  # Higher threshold than before
                print(
                    f"⚠️ No verified pages found. Final attempt with highest-scored result: {search_results[0]['url']} (Score: {search_results[0]['score']})"
                )
                try:
                    await page.goto(
                        search_results[0]["url"],
                        timeout=10000,
                        wait_until="domcontentloaded",
                    )
                    await page.wait_for_timeout(2000)

                    verification = await verify_is_terms_page(page)
                    if verification["confidence"] >= 50:  # Higher minimum threshold
                        print(
                            f"⚠️ Final verification passed with sufficient confidence: {verification['confidence']}"
                        )
                        return page.url
                    else:
                        print(
                            f"❌ Final verification failed with confidence score: {verification['confidence']}"
                        )
                        # Return the highest-scored result even if verification fails
                        print(
                            f"⚠️ Returning highest-scored result as last resort: {search_results[0]['url']}"
                        )
                        return search_results[0]["url"]
                except Exception as e:
                    print(f"Error in final verification: {e}")
                    # Return the best result anyway since verification failed with an error
                    print(
                        f"⚠️ Verification failed with error, returning highest-scored result: {search_results[0]['url']}"
                    )
                    return search_results[0]["url"]

            # Return the best result anyway if it has a decent score (60+)
            if best_result_score >= 60:
                print(
                    f"⚠️ All verification methods failed, returning best unverified result: {best_result_url} (Score: {best_result_score})"
                )
                return best_result_url

        print("No relevant Yahoo search results found")
        return None
    except Exception as e:
        print(f"Error in Yahoo search fallback: {e}")
        return None


async def bing_search_fallback(domain, page):
    """Search for terms of service using Bing Search."""
    try:
        print("Attempting search engine fallback with Bing...")

        # Create a search query for the domain with specific site constraint and exact term matches
        search_query = f'site:{domain} "terms of service" OR "terms and conditions" OR "user agreement" OR "legal"'
        bing_search_url = f"https://www.bing.com/search?q={search_query}"

        # Navigate to Bing search with shorter timeout
        await page.goto(bing_search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)  # Shorter wait for results to load

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
            await page.wait_for_timeout(
                2000
            )  # Give a small window for manual intervention

        # Extract search results with multiple selector options for robustness
        search_results = await page.evaluate(
            r"""(domain) => {
            // Bing uses different selectors depending on the layout
            const selectors = [
                'h2 a[href^="http"]',                   // Standard Bing results
                '#b_results .b_algo a[href^="http"]',   // Main results
                '.b_title a[href^="http"]',             // Title links
                '.b_caption a[href^="http"]',           // Caption links
                'a[href^="http"]'                       // Fallback to all links (filtered later)
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
                        
                        // Only include links to the target domain
                        if (!url.hostname.includes(domain) && !domain.includes(url.hostname)) {
                            return false;
                        }
                        
                        // Filter out search engines and unrelated domains
                        if (url.href.includes('bing.com') ||
                            url.href.includes('microsoft.com/en-us/bing') ||
                            url.href.includes('google.com') ||
                            url.href.includes('yahoo.com') ||
                            url.href.includes('duckduckgo.com')) {
                            return false;
                        }
                        
                        // Filter out articles/blog posts by checking URL patterns
                        
                        // 1. Filter out date patterns (YYYY/MM/DD) commonly used in news/blog URLs
                        if (/\/\d{4}\/\d{1,2}\/\d{1,2}\//.test(urlPath)) {
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
                        
                        // 4. Filter out URLs with dates in them (likely articles)
                        const segments = urlPath.split('/').filter(s => s.length > 0);
                        if (segments.length > 2 && /^\d{4}$/.test(segments[0])) {
                            return false;
                        }
                        
                        return true;
                    } catch (e) {
                        return false;
                    }
                })
                .map(a => {
                    // Get title from the element
                    const title = a.textContent.trim();
                    
                    // Look for description in parent elements
                    let description = '';
                    let parent = a;
                    for (let i = 0; i < 5; i++) { // Look up to 5 levels up
                        parent = parent.parentElement;
                        if (!parent) break;
                        
                        // Try common Bing description selectors
                        const descEl = parent.querySelector('.b_caption p, .b_snippet');
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
                    
                    // Score based on title, description and URL
                    let score = 0;
                    const titleLower = title.toLowerCase();
                    const descLower = description ? description.toLowerCase() : '';
                    const urlLower = a.href.toLowerCase();
                    const urlPath = new URL(a.href).pathname.toLowerCase();
                    
                    // Scoring for title matches (prioritize user agreement and conditions of use)
                    if (titleLower.includes('user agreement')) score += 70;
                    else if (titleLower.includes('conditions of use')) score += 65;
                    else if (titleLower.includes('terms of service')) score += 60;
                    else if (titleLower.includes('terms and conditions')) score += 55;
                    else if (titleLower.includes('terms')) score += 30;  // Lower priority for generic terms
                    else if (titleLower.includes('conditions')) score += 30;
                    else if (titleLower.includes('legal')) score += 25;
                    
                    // Boost URLs in common "legal" directories
                    if (urlPath === '/terms' || 
                        urlPath === '/terms/' ||
                        urlPath === '/tos' || 
                        urlPath === '/tos/' ||
                        urlPath === '/terms-of-service' || 
                        urlPath === '/terms-of-service/' ||
                        urlPath === '/terms-and-conditions' || 
                        urlPath === '/terms-and-conditions/') {
                        score += 80;  // High boost for exact matches
                    }
                    else if (urlPath === '/legal/terms' || 
                             urlPath === '/legal/terms/' ||
                             urlPath === '/policies/terms' || 
                             urlPath === '/policies/terms/') {
                        score += 75;  // Also high boost
                    }
                    else if (urlPath.startsWith('/legal/') || 
                             urlPath.startsWith('/policies/') ||
                             urlPath.startsWith('/about/legal/')) {
                        score += 50;  // Medium boost for general legal directories
                    }
                    
                    // Scoring for description matches
                    if (descLower.includes('terms of service')) score += 30;
                    else if (descLower.includes('terms and conditions')) score += 28;
                    else if (descLower.includes('user agreement')) score += 30;
                    else if (descLower.includes('conditions of use')) score += 33;
                    else if (descLower.includes('legal terms')) score += 24;
                    else if (descLower.includes('terms')) score += 15;
                    
                    // URL-based scoring
                    if (urlLower.includes('terms-of-service')) score += 40;
                    else if (urlLower.includes('terms-and-conditions')) score += 38;
                    else if (urlLower.includes('user-agreement')) score += 40;
                    else if (urlLower.includes('useragreement')) score += 40;
                    else if (urlLower.includes('conditions-of-use') || urlLower.includes('condition-of-use') || urlLower.includes('conditionsofuse') || urlLower.includes('conditionofuse')) score += 43;
                    else if (urlLower.includes('tos')) score += 35;
                    else if (urlLower.includes('terms')) score += 20;
                    else if (urlLower.includes('legal')) score += 20;
                    
                    return {
                        url: a.href,
                        title: title || "No Title",  // Fallback title
                        description: description,
                        score: score
                    };
                })
                .filter(result => 
                    result.score > 0 || 
                    result.url.toLowerCase().includes('terms') || 
                    result.url.toLowerCase().includes('tos') || 
                    result.url.toLowerCase().includes('legal')
                );
                
            // Deduplicate results based on URL
            const uniqueResults = [];
            const seenUrls = new Set();
            
            for (const result of results) {
                if (!seenUrls.has(result.url)) {
                    seenUrls.add(result.url);
                    uniqueResults.push(result);
                }
            }
            
            return uniqueResults;
        }""",
            domain,
        )

        if search_results and len(search_results) > 0:
            print(f"Found {len(search_results)} potential results from Bing search")

            # Sort by score
            search_results.sort(key=lambda x: x["score"], reverse=True)

            # Store the best result in case all verifications fail
            best_result_url = search_results[0]["url"]
            best_result_score = search_results[0]["score"]

            # Display top results for debugging
            for i, result in enumerate(search_results[:3]):
                print(
                    f"Result #{i+1}: {result['title']} - {result['url']} (Score: {result['score']})"
                )

            # Check the top 5 results
            for result_index in range(min(5, len(search_results))):
                best_result = search_results[result_index]["url"]

                try:
                    # Visit the page to verify it's a terms page
                    print(f"Checking result: {best_result}")
                    await page.goto(
                        best_result, timeout=10000, wait_until="domcontentloaded"
                    )
                    await page.wait_for_timeout(2000)  # Allow time for page to load

                    # Check if we hit a captcha
                    is_captcha = await page.evaluate(
                        """() => {
                        const html = document.documentElement.innerHTML.toLowerCase();
                        const url = window.location.href.toLowerCase();
                        return {
                            isCaptcha: html.includes('captcha') || 
                                      url.includes('captcha') ||
                                      html.includes('security measure') ||
                                      html.includes('security check') ||
                                      html.includes('please verify') ||
                                      html.includes('just a moment'),
                            url: window.location.href
                        };
                    }"""
                    )

                    # Special handling for high-scoring URLs that hit captchas
                    if is_captcha["isCaptcha"]:
                        print(f"⚠️ Captcha detected when accessing: {is_captcha['url']}")

                        # If the original URL was high-scoring and contains key terms, accept it even with captcha
                        original_url_lower = best_result.lower()
                        captcha_bypass_domains = [
                            "ebay.com",
                            "amazon.com",
                            "facebook.com",
                            "meta.com",
                            "instagram.com",
                            "openai.com",
                        ]

                        # Check if it's from a known domain and has user agreement/terms in URL
                        is_known_domain = any(
                            domain in original_url_lower
                            for domain in captcha_bypass_domains
                        )
                        has_terms_path = (
                            "user-agreement" in original_url_lower
                            or "useragreement" in original_url_lower
                            or "conditions-of-use" in original_url_lower
                            or "conditionsofuse" in original_url_lower
                            or "terms" in original_url_lower
                            or "tos" in original_url_lower
                            or "policies" in original_url_lower
                        )

                        if (
                            is_known_domain
                            and has_terms_path
                            and search_results[result_index]["score"] >= 60
                        ):
                            print(
                                f"✅ Accepting high-scoring URL from known domain despite captcha: {best_result}"
                            )
                            return best_result
                        else:
                            print(
                                f"❌ Not accepting captcha-protected URL as it doesn't meet criteria"
                            )

                    # Perform verification
                    verification = await verify_is_terms_page(page)

                    if verification["isTermsPage"] and verification["confidence"] >= 60:
                        print(f"✅ Verified terms page from Bing results: {page.url}")
                        return page.url
                    else:
                        print(
                            f"❌ Not a valid Terms page (verification score: {verification['confidence']})"
                        )
                except Exception as e:
                    print(f"Error checking Bing search result: {e}")

            # If we checked all results but none were verified, consider the highest scored result
            # with a minimum score threshold
            if len(search_results) > 0 and search_results[0]["score"] >= 70:
                print(
                    f"⚠️ No verified pages found. Checking highest-scored result: {search_results[0]['url']} (Score: {search_results[0]['score']})"
                )
                try:
                    await page.goto(
                        search_results[0]["url"],
                        timeout=10000,
                        wait_until="domcontentloaded",
                    )
                    await page.wait_for_timeout(2000)

                    verification = await verify_is_terms_page(page)
                    if verification["confidence"] >= 50:  # Higher minimum threshold
                        print(
                            f"⚠️ Final verification passed with sufficient confidence: {verification['confidence']}"
                        )
                        return page.url
                    else:
                        print(
                            f"❌ Final verification failed with confidence score: {verification['confidence']}"
                        )
                        # Return the highest-scored result even if verification fails
                        print(
                            f"⚠️ Returning highest-scored result as last resort: {search_results[0]['url']}"
                        )
                        return search_results[0]["url"]
                except Exception as e:
                    print(f"Error in final verification: {e}")
                    # Return the highest-scored result even if verification fails due to error
                    print(
                        f"⚠️ Verification failed with error, returning highest-scored result: {search_results[0]['url']}"
                    )
                    return search_results[0]["url"]

            # Return the best result anyway if it has a decent score (60+)
            if best_result_score >= 60:
                print(
                    f"⚠️ All verification methods failed, returning best unverified result: {best_result_url} (Score: {best_result_score})"
                )
                return best_result_url

        print("No relevant Bing search results found")
        return None
    except Exception as e:
        print(f"Error in Bing search fallback: {e}")
        return None


async def duckduckgo_search_fallback(domain, page):
    """Search for terms of service using DuckDuckGo Search."""
    try:
        print("Attempting search engine fallback with DuckDuckGo...")
        search_query = f'site:{domain} "terms of service" OR "terms of use" OR "terms and conditions" OR "user agreement" OR "legal"'
        ddg_search_url = f"https://duckduckgo.com/?q={search_query}&t=h_&ia=web"

        await page.goto(ddg_search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        search_results = await page.evaluate(r"""(domain) => {
            const selectors = [
                '.result__a[href^="http"]',
                'article a[href^="http"]',
                'a.eVNpHGjtxRBq_gLOfGDr[href^="http"]',
                '.nrn-react-div a[href^="http"]',
                '.react-results a[href^="http"]',
                '.web-result a[href^="http"]',
                'a[href^="http"]'
            ];
            let allLinks = [];
            for (const selector of selectors) {
                try {
                    const links = Array.from(document.querySelectorAll(selector));
                    if (links.length > 0) {
                        allLinks = [...allLinks, ...links];
                    }
                } catch (e) {}
            }
            const results = allLinks
                .filter(a => {
                    try {
                        const urlText = a.href.toLowerCase();
                        if (!urlText.includes(domain.toLowerCase())) {
                            return false;
                        }
                        if (urlText.includes('duckduckgo.com') ||
                            urlText.includes('google.com') ||
                            urlText.includes('bing.com') ||
                            urlText.includes('yahoo.com')) {
                            return false;
                        }
                        return true;
                    } catch (e) { return false; }
                })
                .map(a => {
                    let title = a.textContent.trim();
                    let description = '';
                    let parentElem = a.parentElement;
                    for (let i = 0; i < 3; i++) {
                        if (!parentElem) break;
                        const descriptionElem = parentElem.querySelector('.result__snippet') || 
                                              parentElem.querySelector('p') ||
                                              parentElem.querySelector('div > span');
                        if (descriptionElem && descriptionElem.textContent) {
                            description = descriptionElem.textContent.trim();
                            break;
                        }
                        parentElem = parentElem.parentElement;
                    }
                    let score = 0;
                    const titleLower = title.toLowerCase();
                    const descLower = description.toLowerCase();
                    const urlLower = a.href.toLowerCase();
                    if (titleLower.includes('terms of service')) score += 50;
                    else if (titleLower.includes('terms of use')) score += 48;
                    else if (titleLower.includes('terms and conditions')) score += 45;
                    else if (titleLower.includes('user agreement')) score += 40;
                    else if (titleLower.includes('terms')) score += 30;
                    else if (titleLower.includes('legal')) score += 20;
                    if (descLower.includes('terms of service')) score += 20;
                    else if (descLower.includes('terms and conditions')) score += 18;
                    else if (descLower.includes('user agreement')) score += 16;
                    else if (descLower.includes('legal') && descLower.includes('terms')) score += 15;
                    if (urlLower.includes('/terms-of-service') || urlLower.includes('/tos/')) score += 60;
                    else if (urlLower.includes('/terms-of-use') || urlLower.includes('/terms_of_use')) score += 55;
                    else if (urlLower.includes('/terms-and-conditions')) score += 50;
                    else if (urlLower.includes('/terms/') || urlLower.includes('/tos')) score += 45;
                    else if (urlLower.includes('/legal/terms')) score += 40;
                    else if (urlLower.includes('/legal/')) score += 30;
                    return {
                        url: a.href,
                        title: title || 'No Title',
                        description: description || 'No Description',
                        score: score
                    };
                });
            const uniqueResults = [];
            const seenUrls = new Set();
            for (const result of results) {
                if (!seenUrls.has(result.url)) {
                    seenUrls.add(result.url);
                    uniqueResults.push(result);
                }
            }
            return uniqueResults;
        }""", domain)
        if search_results and len(search_results) > 0:
            print(f"Found {len(search_results)} potential results from DuckDuckGo search")
            search_results.sort(key=lambda x: x['score'], reverse=True)
            for i in range(min(3, len(search_results))):
                print(f"Result #{i+1}: {search_results[i]['title']} - {search_results[i]['url']} (Score: {search_results[i]['score']})")
            for result in search_results[:3]:
                try:
                    await page.goto(result['url'], timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                    verification = await verify_is_terms_page(page)
                    if verification["isTermsPage"]:
                        print(f"✅ Verified terms page from DuckDuckGo results: {page.url}")
                        return page.url
                    else:
                        print(f"Not a terms page (score: {verification['confidence']})")
                except Exception as e:
                    print(f"Error checking result: {e}")
            if search_results[0]['score'] >= 60:
                print(f"Returning unverified high-confidence result: {search_results[0]['url']}")
                return search_results[0]['url']
        else:
            print("No relevant search results found from DuckDuckGo")
        return None
    except Exception as e:
        print(f"Error in DuckDuckGo search fallback: {e}")
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
        print("Searching for user/customer terms links with HIGHEST PRIORITY...")
        
        # Evaluate the page for user/customer terms links
        links = await page.evaluate("""() => {
            // Find all anchor elements
            const allLinks = Array.from(document.querySelectorAll('a'));
            
            // Extract relevant info from each link
            return allLinks.map(link => ({
                element: link,
                text: link.innerText.trim(),
                href: link.href,
                id: link.id || '',
                classes: link.className || '',
                // Check if element contains "user" or "customer" and "terms" or "agreement"
                isUserTerms: (
                    (link.innerText.toLowerCase().includes('user') || 
                     link.innerText.toLowerCase().includes('customer')) &&
                    (link.innerText.toLowerCase().includes('terms') || 
                     link.innerText.toLowerCase().includes('agreement') ||
                     link.innerText.toLowerCase().includes('conditions'))
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
            
            # Highest priority combinations in text
            if 'user terms' in link_text or 'customer terms' in link_text:
                score += 100
            elif 'user agreement' in link_text or 'customer agreement' in link_text:
                score += 95
            elif 'user conditions' in link_text or 'customer conditions' in link_text:
                score += 90
            # Individual terms in text
            elif 'user' in link_text and ('terms' in link_text or 'agreement' in link_text):
                score += 85
            elif 'customer' in link_text and ('terms' in link_text or 'agreement' in link_text):
                score += 80
            
            # URL patterns (slightly lower priority than text)
            if 'user-terms' in link_href or 'customer-terms' in link_href:
                score += 75
            elif 'user-agreement' in link_href or 'customer-agreement' in link_href:
                score += 70
            elif ('user' in link_href or 'customer' in link_href) and ('terms' in link_href or 'agreement' in link_href):
                score += 65
            
            # Only include links with a minimum score
            if score >= 65:
                try:
                    link = await page.querySelector(f'a[href="{link_info["href"]}"]')
                    if link:
                        user_terms_links.append({"link": link, "text": link_text, "href": link_href, "score": score})
                except Exception as e:
                    print(f"Error finding link element: {e}")
                    continue
        
        # Sort by score
        scored_links = sorted(user_terms_links, key=lambda x: x["score"], reverse=True)
        
        # Print details about high-scoring links
        for link in scored_links:
            print(f"User/Customer Terms Link: '{link['text']}' - {link['href']} (Score: {link['score']})")
        
        # Try the highest scoring links
        for scored_link in scored_links[:3]:  # Try the top 3 links
            link = scored_link["link"]
            href = scored_link["href"]
            text = scored_link["text"]
            score = scored_link["score"]
            
            print(f"⭐⭐⭐ Trying HIGHEST PRIORITY user/customer terms link: {text} - {href} (Score: {score})")
            try:
                success = await click_and_wait_for_navigation(page, link, timeout=5000)
                if success:
                    print(f"✓✓✓ Successfully navigated to USER/CUSTOMER terms link: {page.url}")
                    return page.url
            except Exception as e:
                print(f"Error navigating to user/customer terms link: {e}")
                continue
        
        # If navigation failed for all links, return the best URL anyway
        if scored_links:
            best_link = scored_links[0]["href"]
            print(f"⚠️ Navigation failed, but returning best user/customer terms link: {best_link}")
            return best_link
        
        return None
    except Exception as e:
        print(f"Error in user/customer terms search: {e}")
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

async def find_app_store_tos(url: str) -> str:
    """Find Terms of Service URL for an App Store app."""
    # Implementation would go here
    return None

async def find_play_store_tos(url: str) -> str:
    """Find Terms of Service URL for a Google Play Store app."""
    # Implementation would go here
    return None

async def find_tos_via_common_paths(url: str) -> str:
    """Try to find Terms of Service via intelligent URL pattern detection."""
    try:
        # Parse the base URL
        parsed_url = urlparse(url)
        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
        
        logger.info(f"Setting up browser for common paths check on {base_url}")
        
        # Set up browser to inspect paths
        playwright = await async_playwright().start()
        browser, browser_context, page, _ = await setup_browser(playwright)
        
        try:
            # Navigate to the base URL
            success, _, _ = await navigate_with_retry(page, base_url)
            if not success:
                logger.warning(f"Failed to navigate to base URL: {base_url}")
                return None
                
            # Use JavaScript to find link patterns that might be ToS 
            possible_tos_urls = await page.evaluate("""
                () => {
                    // Get all links on the page
                    const allLinks = Array.from(document.querySelectorAll('a[href]'))
                        .filter(link => link.href && link.href.trim() !== '' && 
                                !link.href.startsWith('javascript:') &&
                                !link.href.includes('mailto:') &&
                                !link.href.includes('tel:'));
                    
                    // Strong pattern matching for ToS links
                    const tosMatches = [];
                    
                    const tosTextPatterns = [
                        'terms of service', 
                        'terms of use', 
                        'terms and conditions',
                        'terms & conditions',
                        'user agreement', 
                        'legal terms',
                        'terms',
                        'tos',
                        'user terms',
                        'legal'
                    ];
                    
                    const tosUrlPatterns = [
                        '/terms',
                        '/tos',
                        '/terms-of-service',
                        '/terms-of-use',
                        '/terms-and-conditions',
                        '/legal/terms',
                        '/legal',
                        '/user-agreement',
                        '/eula'
                    ];
                    
                    // Helper function to score links
                    const scoreLink = (link) => {
                        const text = link.textContent.trim().toLowerCase();
                        const href = link.href.toLowerCase();
                        let score = 0;
                        
                        // Text content matching
                        tosTextPatterns.forEach((pattern, idx) => {
                            if (text.includes(pattern)) {
                                // Higher score for exact matches at start of text
                                if (text.startsWith(pattern)) {
                                    score += 100 - idx; // Give higher priority to patterns earlier in the array
                                } else {
                                    score += 50 - idx;
                                }
                            }
                        });
                        
                        // URL pattern matching
                        tosUrlPatterns.forEach((pattern, idx) => {
                            if (href.includes(pattern)) {
                                score += 30 - idx;
                            }
                        });
                        
                        // Boost score for footer links (often where ToS is found)
                        if (link.closest('footer') || 
                            link.closest('[id*="foot"]') || 
                            link.closest('[class*="foot"]')) {
                            score *= 1.5;
                        }
                        
                        // Boost for links in the footer or legal sections
                        if (href.includes('/legal') || href.includes('/terms')) {
                            score += 20;
                        }
                        
                        return {link: link, href: href, text: text, score: score};
                    };
                    
                    // Score all links
                    const scoredLinks = allLinks
                        .map(scoreLink)
                        .filter(item => item.score > 0); // Only consider links with positive scores
                    
                    // Sort by score (highest first)
                    scoredLinks.sort((a, b) => b.score - a.score);
                    
                    // Return top 5 candidates with their scores
                    return scoredLinks.slice(0, 5).map(item => ({
                        url: item.href,
                        text: item.text,
                        score: item.score
                    }));
                }
            """)
            
            # Log the candidates we found
            if possible_tos_urls and len(possible_tos_urls) > 0:
                logger.info(f"Found {len(possible_tos_urls)} potential ToS URL candidates:")
                for idx, candidate in enumerate(possible_tos_urls):
                    logger.info(f"  Candidate #{idx+1}: {candidate['text']} - {candidate['url']} (Score: {candidate['score']})")
                
                # Return the highest-scoring candidate
                best_candidate = possible_tos_urls[0]['url']
                logger.info(f"Selected best ToS URL candidate: {best_candidate}")
                return best_candidate
            
            return None
        finally:
            # Clean up browser resources
            await browser_context.close()
            await browser.close()
            await playwright.stop()
            
    except Exception as e:
        logger.error(f"Error in find_tos_via_common_paths: {str(e)}")
        return None

async def find_tos_via_html_inspection(url: str) -> str:
    """Try to find Terms of Service via HTML inspection."""
    try:
        # Set up browser to inspect HTML
        playwright = await async_playwright().start()
        browser, browser_context, page, _ = await setup_browser(playwright)
        
        try:
            # Navigate to the URL
            success, _, _ = await navigate_with_retry(page, url)
            if not success:
                return None
                
            # Use JavaScript to search for ToS links in the HTML content
            links = await page.evaluate("""
                () => {
                    const tosTerms = ['terms of service', 'terms of use', 'terms and conditions', 'legal terms'];
                    const links = [];
                    
                    document.querySelectorAll('a[href]').forEach(link => {
                        const text = link.textContent.trim().toLowerCase();
                        const href = link.href;
                        
                        for (const term of tosTerms) {
                            if (text.includes(term) || href.toLowerCase().includes(term.replace(/ /g, '-'))) {
                                links.push({
                                    href: href,
                                    text: text,
                                    match: term
                                });
                                break;
                            }
                        }
                    });
                    
                    return links;
                }
            """)
            
            if links and len(links) > 0:
                # Return the best matching link
                return links[0]['href']
                
            return None
        finally:
            # Ensure browser resources are cleaned up
            await browser_context.close()
            await browser.close()
            await playwright.stop()
            
    except Exception as e:
        print(f"Error during HTML inspection: {e}")
        return None

async def find_user_terms(page) -> str:
    """Try to find user agreement or customer terms links."""
    try:
        # Implementation would go here - for now just check for user terms links
        links = await page.evaluate("""
            () => {
                const links = [];
                document.querySelectorAll('a[href]').forEach(link => {
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href;
                    
                    if (text.includes('user agreement') || 
                        text.includes('customer agreement') ||
                        text.includes('user terms') || 
                        text.includes('customer terms')) {
                        
                        links.push({
                            href: href,
                            text: text
                        });
                    }
                });
                return links;
            }
        """)
        
        if links and len(links) > 0:
            # Return the first matching link
            return links[0]['href']
        
        return None
    except Exception as e:
        print(f"Error finding user terms: {e}")
        return None

def normalize_url(url: str) -> str:
    """Normalize URL by adding protocol if missing."""
    if not url.startswith('http'):
        return 'https://' + url
    return url
