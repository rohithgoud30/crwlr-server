import random
from urllib.parse import urlparse
import re

from fastapi import APIRouter, HTTPException 
from playwright.async_api import async_playwright

from app.models.tos import ToSRequest, ToSResponse

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


@router.post("/tos", response_model=ToSResponse)
async def find_tos(request: ToSRequest) -> ToSResponse:
    """Find Terms of Service page for a given URL."""
    original_url = request.url

    if not original_url:
        raise HTTPException(status_code=400, detail="URL is required")

    # First sanitize the URL to handle malformed URLs
    url = sanitize_url(original_url)
    
    if not url:
        print(f"Invalid URL detected: {original_url}")
        return ToSResponse(
            url=original_url,
            tos_url=None,
            success=False,
            message="Invalid URL format. The URL appears to be malformed or non-existent.",
            method_used="url_validation_failed"
        )

    # Normalize the URL domain for consistent processing
    url = normalize_domain(url)

    # Handle URLs without scheme is now unnecessary as sanitize_url adds it
    # But keep for safety
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    parsed_url = urlparse(url)
    domain = parsed_url.netloc

    browser = None
    playwright = None
    unverified_result = None  # Initialize unverified_result here
    high_score_footer_link = None  # Track high-scoring footer links

    try:
        playwright = await async_playwright().start()
        browser, context, page, _ = await setup_browser(playwright)
        success, _, _ = await navigate_with_retry(page, url, max_retries=2)
        if not success:
            print("\nMain site navigation had issues, but trying to analyze current page...")

        # Special handling for App Store and Play Store - extract ToS via privacy policy link
        app_store_tos, app_store_method, page = await find_tos_via_privacy_policy(page, context)
        if app_store_tos:
            print(f"\n\n🔍 APP STORE/PLAY STORE SUCCESS: Found ToS via privacy policy link: {app_store_tos}")
            return ToSResponse(
                url=original_url,
                tos_url=app_store_tos,
                success=True,
                message="Found Terms of Service via app's privacy policy link",
                method_used=app_store_method
            )

        # First check for user/customer terms links with high priority
        print("Searching for user/customer terms links with highest priority...")
        user_terms_link = await find_user_customer_terms_links(page)
        if user_terms_link:
            print(f"\n\n⭐⭐⭐ HIGHEST PRIORITY SUCCESS: Found user/customer terms link: {user_terms_link}")
            # Return immediately with this high priority link
            return ToSResponse(
                url=original_url,
                tos_url=user_terms_link,
                success=True,
                message="Found User/Customer Terms of Service (HIGHEST PRIORITY)",
                method_used="user_customer_supreme_priority"
            )

        all_links = []
        method_sources = []

        # 1. JavaScript method - Highest priority
        print("Trying find_all_links_js approach...")
        try:
            js_result, page, js_unverified = await find_all_links_js(page, context, None)
            if js_result:
                all_links.append(js_result)
                method_sources.append((js_result, "javascript"))

                # Track if this is a high-scoring footer link
                try:
                    # Check if this looks like ToS from the title
                    await page.goto(js_result, timeout=3000, wait_until="domcontentloaded")
                    page_title = await page.title()
                    title_lower = page_title.lower()

                    # If the title contains key ToS terms, mark it as a
                    # high-score footer link
                    if ('terms of service' in title_lower or
                        'terms of use' in title_lower or
                        'terms and conditions' in title_lower or
                        'user agreement' in title_lower):
                        print(f"✅ Found high-score footer link with ToS-related title: {js_result}")
                        high_score_footer_link = js_result
                except Exception as e:
                    print(f"Error checking footer link title: {e}")
        except Exception as e:
            print(f"Error in JavaScript method: {e}")

        # 2. Scroll method - Second highest priority
        print("Trying smooth_scroll_and_click approach...")
        try:
            scroll_result, page, scroll_unverified = await smooth_scroll_and_click(page, context, js_unverified if 'js_unverified' in locals() else None)
            if scroll_result:
                all_links.append(scroll_result)
                method_sources.append((scroll_result, "scroll"))

                # If no high-score footer link yet, check this one
                if not high_score_footer_link:
                    try:
                        await page.goto(scroll_result, timeout=3000, wait_until="domcontentloaded")
                        page_title = await page.title()
                        title_lower = page_title.lower()

                        if ('terms of service' in title_lower or
                            'terms of use' in title_lower or
                            'terms and conditions' in title_lower or
                            'user agreement' in title_lower):
                            print(f"✅ Found high-score scroll link with ToS-related title: {scroll_result}")
                            high_score_footer_link = scroll_result
                    except Exception as e:
                        print(f"Error checking scroll link title: {e}")
        except Exception as e:
            print(f"Error in scroll method: {e}")

        # If we have a high-score footer link with ToS-related title, check if it's a user/customer one first
        if high_score_footer_link:
            # Before returning, check if there are any js_unverified or scroll_unverified with user/customer terms
            user_customer_link = None
            if 'js_unverified' in locals() and js_unverified and ('user' in js_unverified.lower() or 'customer' in js_unverified.lower()):
                user_customer_link = js_unverified
                method = "javascript_user_customer"
            elif 'scroll_unverified' in locals() and scroll_unverified and ('user' in scroll_unverified.lower() or 'customer' in scroll_unverified.lower()):
                user_customer_link = scroll_unverified
                method = "scroll_user_customer"
                
            # If we found a user/customer link, prioritize it over the high score footer link
            if user_customer_link:
                print(f"🚀🚀🚀 Found user/customer terms in link - PRIORITIZING OVER STANDARD TOS LINK: {user_customer_link}")
                try:
                    # Try to navigate to it
                    await page.goto(user_customer_link, timeout=5000, wait_until="domcontentloaded")
                    return ToSResponse(
                        url=original_url,
                        tos_url=user_customer_link,
                        success=True,
                        message="Found User/Customer Terms of Service (highest priority)",
                        method_used=method
                    )
                except Exception as e:
                    print(f"Error navigating to user/customer link: {e}")
                    # Still return it even if navigation fails
                    return ToSResponse(
                        url=original_url,
                        tos_url=user_customer_link,
                        success=True,
                        message="Found User/Customer Terms of Service (navigation failed but high confidence)",
                        method_used=method + "_nav_failed"
                    )
            
            # Otherwise proceed with the high score footer link
            print(f"Prioritizing high-score footer link with ToS-related title: {high_score_footer_link}")
            return ToSResponse(
                url=original_url,
                tos_url=high_score_footer_link,
                success=True,
                message="Found Terms of Service using high-priority footer link with ToS title",
                method_used="footer_title_match"
            )

        # 3. Search engine methods - Lower priority
        search_results = []
        
        # Try Yahoo search first (often most reliable)
        try:
            print("Trying Yahoo search fallback...")
            yahoo_result = await yahoo_search_fallback(domain, page)
            if yahoo_result:
                search_results.append(yahoo_result)
                all_links.append(yahoo_result)
                method_sources.append((yahoo_result, "Yahoo"))
                
                # If this is a high-confidence result, return it immediately
                try:
                    await page.goto(yahoo_result, timeout=5000, wait_until="domcontentloaded")
                    page_title = await page.title()
                    title_lower = page_title.lower()
                    
                    if ('terms of service' in title_lower or
                        'terms of use' in title_lower or
                        'terms and conditions' in title_lower or
                        'user agreement' in title_lower):
                        print(f"✅ Found high-confidence Yahoo result with ToS title: {yahoo_result}")
                        return ToSResponse(
                            url=original_url,
                            tos_url=yahoo_result,
                            success=True,
                            message="Found Terms of Service using Yahoo method (highest confidence)",
                            method_used="Yahoo"
                        )
                except Exception as e:
                    print(f"Error verifying Yahoo result title: {e}")
        except Exception as e:
            print(f"Error with Yahoo search: {e}")

        # Try Bing search
        try:
            print("Trying Bing search fallback...")
            bing_result = await bing_search_fallback(domain, page)
            if bing_result:
                search_results.append(bing_result)
                all_links.append(bing_result)
                method_sources.append((bing_result, "Bing"))
                
                # Also check if this is a high-confidence result
                try:
                    await page.goto(bing_result, timeout=5000, wait_until="domcontentloaded")
                    page_title = await page.title()
                    title_lower = page_title.lower()
                    
                    if ('terms of service' in title_lower or
                        'terms of use' in title_lower or
                        'terms and conditions' in title_lower or
                        'user agreement' in title_lower):
                        print(f"✅ Found high-confidence Bing result with ToS title: {bing_result}")
                        return ToSResponse(
                            url=original_url,
                            tos_url=bing_result,
                            success=True,
                            message="Found Terms of Service using Bing method (highest confidence)",
                            method_used="Bing"
                        )
                except Exception as e:
                    print(f"Error verifying Bing result title: {e}")
        except Exception as e:
            print(f"Error with Bing search: {e}")
        
        # Try DuckDuckGo search
        try:
            print("Trying DuckDuckGo search fallback...")
            ddg_result = await duckduckgo_search_fallback(domain, page)
            if ddg_result:
                search_results.append(ddg_result)
                all_links.append(ddg_result)
                method_sources.append((ddg_result, "DuckDuckGo"))
                
                # Also check if this is a high-confidence result
                try:
                    await page.goto(ddg_result, timeout=5000, wait_until="domcontentloaded")
                    page_title = await page.title()
                    title_lower = page_title.lower()
                    
                    if ('terms of service' in title_lower or
                        'terms of use' in title_lower or
                        'terms and conditions' in title_lower or
                        'user agreement' in title_lower):
                        print(f"✅ Found high-confidence DuckDuckGo result with ToS title: {ddg_result}")
                        return ToSResponse(
                            url=original_url,
                            tos_url=ddg_result,
                            success=True,
                            message="Found Terms of Service using DuckDuckGo method (highest confidence)",
                            method_used="DuckDuckGo"
                        )
                except Exception as e:
                    print(f"Error verifying DuckDuckGo result title: {e}")
        except Exception as e:
            print(f"Error with DuckDuckGo search: {e}")

        # Continue with existing processing for all found links
        # Deduplicate links
        seen = set()
        unique_links = []
        unique_sources = []
        for link, src in method_sources:
            if link and link not in seen:
                unique_links.append(link)
                unique_sources.append(src)
                seen.add(link)

        # Prefer main domain links before scoring
        main_domain = domain.replace('www.', '')
        unique_links = prefer_main_domain(unique_links, main_domain)
        unique_sources = [src for src in unique_sources if any(link in unique_links for link, s in zip(unique_links, unique_sources) if s == src)]

        # Score each link using verify_is_terms_page
        scored_links = []
        for link, src in zip(unique_links, unique_sources):
            try:
                print(f"Verifying link: {link} (source: {src})")
                await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1000)
                verification = await verify_is_terms_page(page)
                score = verification.get("confidence", 0)
                is_terms = verification.get("isTermsPage", False)
                scored_links.append({
                    "link": link,
                    "source": src,
                    "score": score,
                    "is_terms": is_terms,
                })
            except Exception as e:
                print(f"Error verifying link {link}: {e}")
                continue

        # Sort by score (confidence), prefer is_terms True
        scored_links.sort(key=lambda x: (x["is_terms"], x["score"]), reverse=True)

        if scored_links:
            best = scored_links[0]
            return ToSResponse(
                url=original_url,
                tos_url=best["link"],
                success=True,
                message=f"Found Terms of Service using {best['source']} method (highest confidence)",
                method_used=best["source"]
            )

        # If we reach here, no high-scoring links were found
        # Fall back to any unverified link found during the process
        if unverified_result:
            print(f"No high-scoring links found. Using unverified result: {unverified_result}")
            return ToSResponse(
                url=original_url,
                tos_url=unverified_result,
                success=True,
                message="Found unverified Terms of Service URL (lower confidence)",
                method_used="unverified"
            )
        
        # No links found at all
        return ToSResponse(
            url=original_url,
            tos_url=None,
            success=False,
            message="No Terms of Service URL found",
            method_used="none"
        )

    except Exception as e:
        print(f"Error: {e}")
        # If we have an unverified result despite the error, return it
        if unverified_result:
            return handle_error(original_url, unverified_result, str(e))
        else:
            return ToSResponse(
                url=original_url,
                tos_url=None,
                success=False,
                message=f"Error finding Terms of Service: {str(e)}",
                method_used="error"
            )
    finally:
        if browser:
            try:
                await browser.close()
            except Exception as e:
                print(f"Error closing browser: {e}")
        if playwright:
            try:
                await playwright.stop()
            except Exception as e:
                print(f"Error stopping playwright: {e}")


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
    """Optimized JavaScript-based link finder with anti-bot protection handling."""
    print("\n=== Starting find_all_links_js ===")
    print("Searching for all links using JavaScript...")

    try:
        # Shorter wait for page loading
        await page.wait_for_timeout(500)

        # First check if we're on an anti-bot page
        is_anti_bot = await page.evaluate(
            """() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            return html.includes('captcha') || 
                   html.includes('cloudflare') || 
                   html.includes('challenge') ||
                   html.includes('security check') ||
                   html.includes('bot detection');
        }"""
        )

        if is_anti_bot:
            print(
                "Detected anti-bot protection, looking for footer links specifically..."
            )
            # On anti-bot pages, specifically target footer links which are often still accessible
            footer_links = await page.evaluate(
                """() => {
                // Look for footer elements, which often contain Terms links
                const footers = [
                    document.querySelector('footer'),
                    document.querySelector('.footer'),
                    document.querySelector('#footer'),
                    document.querySelector('[class*="footer"]'),
                    document.querySelector('[id*="footer"]'),
                ];
                
                const footer = footers.find(f => f !== null);
                if (!footer) return [];
                
                // Get all links in the footer and score them
                return Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => {
                        // General filtering
                        if (!a.href || !a.textContent) return false;
                        if (a.href.startsWith('javascript:') || a.href.startsWith('mailto:')) return false;
                        
                        const text = a.textContent.trim().toLowerCase();
                        const href = a.href.toLowerCase();
                        
                        // Look for terms-related links
                        return text.includes('terms') || 
                               text.includes('tos') || 
                               text.includes('legal') ||
                               text.includes('conditions') ||
                               href.includes('terms') ||
                               href.includes('tos') ||
                               href.includes('legal');
                    })
                    .map(a => {
                        const text = a.textContent.trim().toLowerCase();
                        const href = a.href.toLowerCase();
                        
                        // Score the link
                        let score = 0;
                        if (text.includes('terms of service') || text.includes('terms of use')) score += 100;
                        else if (text.includes('terms')) score += 80;
                        else if (text.includes('tos')) score += 70;
                        else if (text.includes('legal')) score += 50;
                        
                        if (href.includes('terms-of-service') || href.includes('terms_of_service')) score += 50;
                        else if (href.includes('terms') || href.includes('tos')) score += 40;
                        else if (href.includes('legal')) score += 30;
                        
                        return {
                            text: a.textContent.trim(),
                            href: a.href,
                            score: score
                        };
                    })
                    .sort((a, b) => b.score - a.score);
            }"""
            )

            if footer_links and len(footer_links) > 0:
                print(
                    f"Found {len(footer_links)} potential links in footer despite anti-bot protection"
                )
                for i, link in enumerate(footer_links[:3]):
                    print(
                        f"Footer link #{i+1}: {link['text']} - {link['href']} (Score: {link['score']})"
                    )

                best_link = footer_links[0]["href"]
                # Just return the link without navigating to verify
                return best_link, page, best_link

        # Get the base domain for validation
        base_domain = await page.evaluate(
            """() => {
            try {
                return new URL(window.location.href).hostname;
            } catch (e) {
                return '';
            }
        }"""
        )

        print(f"Base domain: {base_domain}")

        # Enhanced link detection script - specifically targeting common ToS links at bottom of page
        links = await page.evaluate(
            """(baseDomain) => {
            // Get all footer sections (most ToS links are in footers)
            const footerSelectors = [
                'footer', '.footer', '#footer', '[class*="footer"]', '[id*="footer"]',
                '.legal', '#legal', '.bottom', '.links', '.nav-bottom', '.site-info'
            ];
            
            // Get all links that might be ToS links
            let allLinks = [];
            
            // First check footer areas
            for (const selector of footerSelectors) {
                const container = document.querySelector(selector);
                if (container) {
                    const links = Array.from(container.querySelectorAll('a[href]'));
                    allLinks = [...allLinks, ...links];
                }
            }
            
            // If no links found in footers, check the entire page
            if (allLinks.length === 0) {
                allLinks = Array.from(document.querySelectorAll('a[href]'));
            }
            
            // Term patterns to look for
            const termPatterns = [
                'terms of use', 'terms of service', 'terms and conditions', 
                'user agreement', 'terms', 'tos', 'legal', 'conditions'
            ];
            
            // Score and filter links
            const scoredLinks = allLinks
                .filter(link => {
                    // Basic filtering
                    if (!link.href || !link.textContent) return false;
                    
                    // Skip javascript, mail links
                    if (link.href.startsWith('javascript:') || 
                        link.href.startsWith('mailto:') || 
                        link.href.startsWith('tel:')) return false;
                    
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href.toLowerCase();
                    
                    // Include if text or URL contains any of our terms
                    return termPatterns.some(term => text.includes(term) || href.includes(term));
                })
                .map(link => {
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href.toLowerCase();
                    
                    // Score the link
                let score = 0;
                    
                    // Text matching
                    if (text === 'terms of use' || text === 'terms of service') score += 100;
                    else if (text.includes('terms of use') || text.includes('terms of service')) score += 90;
                    else if (text.includes('terms') && text.includes('conditions')) score += 85;
                    else if (text.includes('user agreement')) score += 80;
                    else if (text.includes('terms')) score += 70;
                    else if (text === 'legal' || text === 'legal info') score += 60;
                    else if (text.includes('legal')) score += 50;
                    
                    // URL matching
                    if (href.includes('terms-of-use') || 
                        href.includes('terms-of-service') || 
                        href.includes('terms_of_use') || 
                        href.includes('terms_of_service')) score += 50;
                    else if (href.includes('terms-and-conditions') || 
                             href.includes('terms_and_conditions')) score += 45;
                    else if (href.includes('user-agreement') || 
                             href.includes('user_agreement')) score += 45;
                    else if (href.includes('/terms/') || 
                             href.includes('/tos/')) score += 40;
                    else if (href.includes('/terms') || 
                             href.includes('/tos')) score += 35;
                    else if (href.includes('legal') || 
                             href.includes('conditions')) score += 30;
                    
                    // Add boost for links in the page footer or with legal in the path
                    const isInFooter = footerSelectors.some(sel => 
                        link.closest(sel) !== null);
                    
                    if (isInFooter) score += 20;
                    if (href.includes('/legal/')) score += 15;
                
                return {
                    text: text,
                        href: href,
                        score: score
                    };
                })
                .filter(item => item.score > 30) // Only include higher scored links
                .sort((a, b) => b.score - a.score);
            
            return scoredLinks;
        }""",
            base_domain,
        )

        # No links found
        if not links or len(links) == 0:
            print("No relevant links found using JavaScript method")
            return None, page, unverified_result

        # Display top results with scores for JS method
        print(f"Found {len(links)} relevant links (JS):")
        for i, link in enumerate(links[:5]):
            print(f"JS Link #{i+1}: {link['text']} - {link['href']} (Score: {link['score']})")

        # If links were found, use the highest scored one
        if links:
            best_link = links[0]["href"]

            # Set unverified result if none exists
            if not unverified_result:
                unverified_result = best_link

            # Skip navigation completely for any link with score > 60
            # This removes mandatory navigation to improve performance
            if links[0]["score"] > 60:
                print(f"Found high-scoring link ({links[0]['score']}): {best_link}")
                print(f"Skipping navigation to save time")
                return best_link, page, unverified_result
                
            # Only try navigation for medium-confidence links (score between 30-60)
            # For scores below this threshold, just return the best link
            if links[0]["score"] <= 60:
                print(f"Medium-confidence link found with score {links[0]['score']}, returning without navigation")
                return best_link, page, unverified_result

        return None, page, unverified_result

    except Exception as e:
        print(f"Error in JavaScript link finder: {e}")
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
    """Optimized version that finds links without mandatory navigation."""
    print("🔃 Starting smooth scroll with strong term matching...")
    visited_url = None
    current_page = page

    # First check visible links before scrolling
    visited_url, current_page, unverified_result = await find_matching_link(
        current_page, context, unverified_result
    )
    if visited_url:
        return visited_url, current_page, unverified_result

    try:
        # Simplified footer selectors
        footer_selectors = ["footer", ".footer", "#footer"]

        # Get page height more efficiently
        page_height = await current_page.evaluate(
            """() => document.documentElement.scrollHeight"""
        )

        # Use fewer positions to check
        positions_to_check = [
            page_height,
            page_height * 0.5,
        ]

        for scroll_pos in positions_to_check:
            await current_page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            await current_page.wait_for_timeout(200)

            # Extract all links at this scroll position
            links = await current_page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .filter(a => {
                            if (!a.href || !a.textContent) return false;
                            if (a.href.startsWith('javascript:') || a.href.startsWith('mailto:')) return false;
                            
                            const text = a.textContent.trim().toLowerCase();
                            const href = a.href.toLowerCase();
                            
                            return text.includes('terms') || 
                                   text.includes('tos') || 
                                   text.includes('conditions') ||
                                   href.includes('terms') || 
                                   href.includes('tos') ||
                                   href.includes('conditions');
                        })
                        .map(a => ({
                            text: a.textContent.trim(),
                            href: a.href
                        }));
                }
            """)
            
            if links and len(links) > 0:
                # Score and sort links
                scored_links = []
                for link in links:
                    score = 0
                    text = link['text'].lower()
                    href = link['href'].lower()
                    
                    # Score based on text
                    if "terms of service" in text: score += 100
                    elif "terms of use" in text: score += 90
                    elif "terms and conditions" in text: score += 80
                    elif "terms" in text: score += 60
                    elif "tos" in text: score += 50
                    
                    # Score based on URL
                    if "/terms-of-service" in href: score += 50
                    elif "/terms" in href: score += 30
                    elif "/tos" in href: score += 30
                    
                    if score > 50:
                        scored_links.append((href, score, text))
                
                # Sort by score
                scored_links.sort(key=lambda x: x[1], reverse=True)
                
                # Return the best link without navigation if score is high enough
                if scored_links and scored_links[0][1] > 60:
                    best_link = scored_links[0][0]
                    print(f"Found high confidence link in scroll: {scored_links[0][2]} ({scored_links[0][1]})")
                    print(f"Returning link without navigation: {best_link}")
                    return best_link, current_page, unverified_result
            
            # Check with traditional method as fallback
            visited_url, current_page, unverified_result = await find_matching_link(
                current_page, context, unverified_result
            )
            if visited_url:
                return visited_url, current_page, unverified_result

        # Check footer area with simplified approach
        for selector in footer_selectors:
            footer = await current_page.query_selector(selector)
            if footer:
                print(f"Found footer with selector: {selector}")
                await footer.scroll_into_view_if_needed()

                # Check for terms links with faster query
                terms_links = await current_page.evaluate(
                    """(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    const links = Array.from(footer.querySelectorAll('a'));
                    return links.filter(link => {
                        const text = link.textContent.toLowerCase();
                        const href = link.href.toLowerCase();
                        return text.includes('terms') || 
                               text.includes('tos') || 
                               href.includes('terms') || 
                               href.includes('tos');
                    }).map(link => ({
                    text: link.textContent.trim(),
                        href: link.href,
                        score: (text.includes('terms of service') || text.includes('terms of use')) ? 100 :
                              text.includes('terms') ? 80 :
                              text.includes('tos') ? 70 : 
                              50
                    }));
                }""",
                    selector,
                )

                if terms_links and len(terms_links) > 0:
                    print(f"Found {len(terms_links)} potential terms links in footer")
                    # Sort by score
                    terms_links.sort(key=lambda x: x.get('score', 0), reverse=True)
                    
                    # Return highest scoring link without navigation
                    if terms_links[0].get('score', 0) > 60:
                        print(f"Returning highest scoring footer link without navigation: {terms_links[0]['href']}")
                        return terms_links[0]['href'], current_page, unverified_result
                    
                    # Only navigate if absolutely necessary for lower scoring links
                    # Limit to first 2 links for speed
                    for link in terms_links[:2]:
                        try:
                            element = await current_page.query_selector(
                                f"a[href='{link['href']}']"
                            )
                            if element:
                                success = await click_and_wait_for_navigation(
                                    current_page, element, timeout=3000
                                )
                                if success:
                                    return (
                                        current_page.url,
                                        current_page,
                                        unverified_result,
                                    )
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")

        # Skip scrolling back up to save time
        print("✅ Reached the bottom of the page.")

    except Exception as e:
        print(f"Error in footer/scroll check: {e}")

    return None, current_page, unverified_result


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
        
        # Look for the privacy policy link in the app-privacy section
        privacy_link = await page.evaluate("""
            () => {
                // Target the app-privacy section
                const privacySection = document.querySelector('.app-privacy');
                if (!privacySection) return null;
                
                // Look for direct privacy policy link
                const directLink = privacySection.querySelector('a[href*="privacy"]');
                if (directLink && directLink.href) {
                    return { 
                        href: directLink.href,
                        text: directLink.textContent.trim(),
                        confidence: 'high'
                    };
                }
                
                // Fallback: look for any link in the privacy section
                const anyLink = privacySection.querySelector('a[href]');
                if (anyLink && anyLink.href) {
                    return { 
                        href: anyLink.href,
                        text: anyLink.textContent.trim(),
                        confidence: 'medium'
                    };
                }
                
                // Second fallback: look for links in developer info section
                const devInfoSection = document.querySelector('.information-list--app');
                if (devInfoSection) {
                    const devLinks = Array.from(devInfoSection.querySelectorAll('a[href]'));
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
        
        # Ensure this is not a Google domain
        if "google.com" in parsed_url.netloc or "play.google.com" in parsed_url.netloc:
            print("❌ Privacy link is from Google domain, not from app developer")
            return None, None, page
            
        # Try to guess the ToS URL based on the privacy URL patterns
        privacy_path = parsed_url.path.lower()
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
        
        # If no replacements worked, just return the privacy URL itself
        # This is better than making up a path that doesn't exist
        print("⚠️ Could not determine ToS URL through pattern replacement, returning privacy URL as fallback")
        return privacy_link, "app_store_privacy_to_tos_privacy_fallback", page
            
    except Exception as e:
        print(f"❌ Error finding ToS via privacy policy: {e}")
        return None, None, page
