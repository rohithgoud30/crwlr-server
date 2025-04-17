import random
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException 
from playwright.async_api import async_playwright

from app.models.privacy import PrivacyRequest, PrivacyResponse

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
    "privacy policy": 100,
    "privacy notice": 95,
    "data privacy": 90,
    "privacy statement": 85,
    "privacy information": 80,
    "data protection": 75,
    "information collection": 70,
}

# Priorities for partial match terms
partialMatchPriorities = {
    "privacy": 60,
    "personal data": 55,
    "data collection": 50,
    "information usage": 45,
    "cookies": 40,
    "data processing": 35,
    "personal information": 30,
    "data rights": 25,
    "data practices": 20,
}

# Define your strong match terms here
strong_privacy_matches = [
    "privacy policy",
    "privacy notice",
    "privacy statement",
    "data privacy",
    "data protection notice",
    "privacy information",
    "privacy",
    "gdpr",
    "ccpa",
    "data protection",
]

# Define dynamic scoring weights for link evaluation
LINK_EVALUATION_WEIGHTS = {
    "text_match": 0.4,
    "url_structure": 0.3,
    "context": 0.2,
    "position": 0.1,
}


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
@router.post("/privacy", response_model=PrivacyResponse)
async def find_privacy_policy(request: PrivacyRequest) -> PrivacyResponse:
    """Find Privacy Policy page for a given URL."""
    url = request.url

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # Normalize the URL domain for consistent processing
    url = normalize_domain(url)

    # Handle URLs without scheme
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    parsed_url = urlparse(url)
    domain = parsed_url.netloc

    browser = None
    playwright = None
    unverified_result = None  # Initialize unverified_result here

    # Define search_engines here to make it accessible throughout the function
    search_engines = [
        (bing_search_fallback_privacy, "Bing"),
        (yahoo_search_fallback_privacy, "Yahoo"),
        (duckduckgo_search_fallback_privacy, "DuckDuckGo"),
    ]

    try:
        playwright = await async_playwright().start()
        browser, context, page, _ = await setup_browser(playwright)
        success, _, _ = await navigate_with_retry(page, url, max_retries=2)
        if not success:
            print("\nMain site navigation had issues, but trying to analyze current page...")

        all_links = []
        method_sources = []

        # 1. JavaScript method
        print("Trying find_all_links_js approach...")
        try:
            js_result, page, js_unverified = await find_all_privacy_links_js(page, context, None)
            if js_result:
                all_links.append(js_result)
                method_sources.append((js_result, "javascript"))
        except Exception as e:
            print(f"Error in JavaScript method: {e}")

        # 2. Scroll method
        print("Trying smooth_scroll_and_click approach...")
        try:
            scroll_result, page, scroll_unverified = await smooth_scroll_and_click_privacy(page, context, js_unverified if 'js_unverified' in locals() else None)
            if scroll_result:
                all_links.append(scroll_result)
                method_sources.append((scroll_result, "scroll"))
        except Exception as e:
            print(f"Error in scroll method: {e}")

        # 3. Search engine methods
        search_engines = [
            (bing_search_fallback_privacy, "Bing"),
            (yahoo_search_fallback_privacy, "Yahoo"),
            (duckduckgo_search_fallback_privacy, "DuckDuckGo"),
        ]
        for search_func, engine_name in search_engines:
            try:
                print(f"Trying {engine_name} search fallback...")
                search_result = await search_func(domain, page)
                if search_result:
                    all_links.append(search_result)
                    method_sources.append((search_result, engine_name))
            except Exception as e:
                print(f"Error with {engine_name} search: {e}")
                continue

        # Deduplicate links
        seen = set()
        unique_links = []
        unique_sources = []
        for link, src in method_sources:
            if link and link not in seen:
                unique_links.append(link)
                unique_sources.append(src)
                seen.add(link)

        # Score each link using verify_is_privacy_page
        scored_links = []
        for link, src in zip(unique_links, unique_sources):
            try:
                print(f"Verifying link: {link} (source: {src})")
                await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1000)
                verification = await verify_is_privacy_page(page)
                score = verification.get("confidence", 0)
                is_privacy = verification.get("isPrivacyPage", False)
                scored_links.append({
                    "url": link,
                    "source": src,
                    "score": score,
                    "is_privacy": is_privacy,
                })
            except Exception as e:
                print(f"Error verifying link {link}: {e}")
                continue

        # Sort by score (confidence), prefer is_privacy True, then corporate pages, then score
        corporate_patterns = ["company/", "privacy-center", "privacy-notice", "/legal/"]
        # Sort by (is_privacy, is_corporate, confidence)
        scored_links.sort(
            key=lambda x: (
                x["is_privacy"],
                any(p in x["url"].lower() for p in corporate_patterns),
                x["score"]
            ),
            reverse=True
        )

        if scored_links:
            # After prioritizing, the first link is the best candidate
            best = scored_links[0]
            # Try to follow any corporate-style privacy link on the selected page
            try:
                await page.goto(best["url"], timeout=10000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1000)
                corp_link = await page.evaluate(f"""
                    () => {{
                        const anchors = Array.from(document.querySelectorAll('a[href]'));
                        const corpKeywords = ['company/', 'privacy-center', 'privacy-notice', '/legal/'];
                        const currentHost = new URL(window.location.href).hostname.replace('www.', '');
                        for (const a of anchors) {{
                            const href = a.href;
                            try {{
                                const u = new URL(href);
                                const host = u.hostname.replace('www.', '');
                                const path = u.pathname.toLowerCase();
                                // external host with corporate path segment
                                if (host !== currentHost && corpKeywords.some(k => path.includes(k))) {{
                                    return href;
                                }}
                            }} catch {{}}
                        }}
                        return null;
                    }}
                """)
                final_pp = corp_link or best["url"]
            except Exception:
                final_pp = best["url"]
            return PrivacyResponse(
                url=url,
                pp_url=final_pp,
                success=True,
                message=f"Found Privacy Policy using {best['source']} method (highest confidence)",
                method_used=best['source'],
            )

        # === Fallback: Dynamically scan all links and footer links for Privacy Policy candidates ===
        print("\n[Fallback] Scanning all links and footer for Privacy Policy candidates...")
        # 1. Scan all links on the page
        all_links = await page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a'));
            return links.map(link => ({
                text: link.innerText.trim(),
                href: link.href,
                id: link.id,
                classes: link.className
            }));
        }""")
        
        # Print all links with "Privacy" in text for debugging
        for link in all_links:
            link_text = link['text'].lower() if link['text'] else ''
            if 'privacy' in link_text:
                print(f"[Debug] Found link with privacy in text: {link['text']} - {link['href']}")
                
        privacy_keywords = ["privacy", "data", "cookies", "gdpr", "ccpa", "personal information", "collection"]
        potential_privacy_links = []
        for link in all_links:
            link_text = link['text'].lower() if link['text'] else ''
            link_href = link['href'].lower() if link['href'] else ''
            if any(kw in link_text for kw in privacy_keywords) or any(kw in link_href for kw in privacy_keywords):
                potential_privacy_links.append(link)
                
        # Score footer links higher - give them priority
        for link in potential_privacy_links:
            link_text = link['text'].lower() if link['text'] else ''
            if 'privacy' in link_text and ('notice' in link_text or 'policy' in link_text):
                print(f"[Footer Priority] Found strong privacy text match: {link['text']} - {link['href']}")
                # Try this link first as it's the most likely candidate
                try:
                    await page.goto(link['href'], timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
                    final_url = page.url  # Get the final URL after redirects
                    verification = await verify_is_privacy_page(page)
                    if verification.get("isPrivacyPage", False) or verification.get("confidence", 0) >= 40:
                        print(f"[Footer Priority] ✅ Verified Privacy Policy page: {final_url}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=final_url,
                            success=True,
                            message="Found Privacy Policy using high-priority footer link",
                            method_used="footer_priority",
                        )
                except Exception as e:
                    print(f"[Footer Priority] Error verifying link: {e}")

        # Deduplicate by href
        seen_hrefs = set()
        deduped_links = []
        for link in potential_privacy_links:
            href = link['href']
            if href and href not in seen_hrefs:
                seen_hrefs.add(href)
                deduped_links.append(link)
        # Try to verify each candidate
        for link in deduped_links:
            try:
                candidate_url = link['href']
                if not candidate_url:
                    continue
                print(f"[Fallback] Verifying candidate: {candidate_url}")
                await page.goto(candidate_url, timeout=10000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1000)
                final_url = page.url  # Get the final URL after redirects
                verification = await verify_is_privacy_page(page)
                if verification.get("isPrivacyPage", False):
                    print(f"[Fallback] ✅ Verified Privacy Policy page: {final_url}")
                    return PrivacyResponse(
                        url=url,
                        pp_url=final_url,  # Use the final URL after all redirects
                        success=True,
                        message="Found Privacy Policy using dynamic footer/link scan fallback",
                        method_used="dynamic_footer_fallback",
                    )
            except Exception as e:
                print(f"[Fallback] Error verifying candidate: {e}")
                continue

        # === Final fallback: HTML-level link extraction ===
        print("[HTML Fallback] Extracting links from raw HTML...")
        try:
            html_content = await page.content()
            import re
            anchor_pattern = re.compile(r'<a\\s+[^>]*href=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
            matches = anchor_pattern.findall(html_content)
            html_candidates = []
            for href, text in matches:
                text_l = text.lower()
                href_l = href.lower()
                if any(kw in text_l for kw in privacy_keywords) or any(kw in href_l for kw in privacy_keywords):
                    html_candidates.append(href)
            # Deduplicate
            html_candidates = list(dict.fromkeys(html_candidates))
            for candidate_url in html_candidates:
                try:
                    print(f"[HTML Fallback] Verifying candidate: {candidate_url}")
                    await page.goto(candidate_url, timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
                    verification = await verify_is_privacy_page(page)
                    if verification.get("isPrivacyPage", False):
                        print(f"[HTML Fallback] ✅ Verified Privacy Policy page: {candidate_url}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=candidate_url,
                            success=True,
                            message="Found Privacy Policy using HTML-level fallback",
                            method_used="html_fallback",
                        )
                except Exception as e:
                    print(f"[HTML Fallback] Error verifying candidate: {e}")
                    continue
        except Exception as e:
            print(f"[HTML Fallback] Error extracting links: {e}")

        # If all methods fail, return failure
        return handle_navigation_failure_privacy(url, None)

    except Exception as e:
        print(f"Error during browser automation: {e}")
        return handle_error_privacy(url, None, str(e))
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
                "--window-size=1920,1080",  # Reduced resolution for stability
                "--disable-extensions",
                "--disable-features=site-per-process",  # For memory optimization
            ],
            chromium_sandbox=False,
            slow_mo=50,  # Reduced delay for better performance
        )

        # Create context with optimized settings
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},  # Consistent with browser window
            user_agent=user_agent,
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
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
        async def random_delay(min_ms=200, max_ms=800):
            delay = random.randint(min_ms, max_ms)
            await page.wait_for_timeout(delay)

        # Set reasonable timeouts
        page.set_default_timeout(20000)  # Reduced timeout

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
                delay = random.randint(500, 1000)  # Reduced delay
                print(f"Waiting {delay/1000}s before retry {attempt+1}...")
                await page.wait_for_timeout(delay)

            print(f"Navigation attempt {attempt+1}/{max_retries} to {url}")

            # Optimized navigation strategy with shorter timeout
            response = await page.goto(url, timeout=8000, wait_until="domcontentloaded")

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
async def find_all_privacy_links_js(page, context, unverified_result=None):
    """Optimized JavaScript-based privacy policy link finder with anti-bot protection handling."""
    print("\n=== Starting find_all_privacy_links_js ===")
    print("Searching for all privacy policy links using JavaScript...")

    try:
        # Shorter wait for page loading
        await page.wait_for_timeout(1500)

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
                // Look for footer elements, which often contain Privacy links
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
                        
                        // Look for privacy-related links
                        return text.includes('privacy') || 
                               text.includes('data') || 
                               text.includes('cookies') ||
                               text.includes('personal information') ||
                               href.includes('privacy') ||
                               href.includes('data-protection') ||
                               href.includes('gdpr');
                    })
                    .map(a => {
                        const text = a.textContent.trim().toLowerCase();
                        const href = a.href.toLowerCase();
                        
                        // Score the link
                        let score = 0;
                        if (text.includes('privacy policy') || text.includes('privacy notice')) score += 100;
                        else if (text.includes('privacy')) score += 80;
                        else if (text.includes('data protection')) score += 70;
                        else if (text.includes('personal information')) score += 60;
                        else if (text.includes('cookies')) score += 50;
                        
                        if (href.includes('privacy-policy') || href.includes('privacy_policy')) score += 50;
                        else if (href.includes('privacy') || href.includes('gdpr')) score += 40;
                        else if (href.includes('data-protection') || href.includes('data_protection')) score += 40;
                        else if (href.includes('cookie') || href.includes('personal-information')) score += 30;
                        
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

                # For any high-quality privacy link, follow redirects and return final URL
                best_link = footer_links[0]["href"]
                best_text = footer_links[0]["text"].lower()
                best_score = footer_links[0]["score"]
                
                if (("privacy notice" in best_text or "privacy policy" in best_text) and best_score >= 100) or best_score >= 140:
                    print(f"✅ Found high-quality privacy policy link in footer: {best_link}")
                    # Try to navigate to it to get final URL after any redirects
                    try:
                        await page.goto(best_link, timeout=10000, wait_until="domcontentloaded")
                        await page.wait_for_timeout(1500)
                        final_url = page.url  # Get URL after redirects
                        print(f"Final URL after redirect: {final_url}")
                        return final_url, page, final_url
                    except Exception as e:
                        print(f"Error navigating to best footer link: {e}")
                        # Fall back to returning the original URL
                        return best_link, page, best_link
                
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

        # Enhanced link detection script - specifically targeting common Privacy Policy links at bottom of page
        links = await page.evaluate(
            """(baseDomain) => {
            // Get all footer sections (most Privacy Policy links are in footers)
            const footerSelectors = [
                'footer', '.footer', '#footer', '[class*="footer"]', '[id*="footer"]',
                '.legal', '#legal', '.bottom', '.links', '.nav-bottom', '.site-info'
            ];
            
            // Get all links that might be Privacy Policy links
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
            
            // Privacy patterns to look for
            const privacyPatterns = [
                'privacy policy', 'privacy notice', 'privacy statement', 
                'privacy', 'data protection', 'gdpr', 'ccpa', 'cookies', 
                'personal information', 'data privacy'
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
                    return privacyPatterns.some(term => text.includes(term) || href.includes(term));
                })
                .map(link => {
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href.toLowerCase();
                    
                    // Score the link
                    let score = 0;
                    
                    // Text matching
                    if (text === 'privacy policy' || text === 'privacy notice') score += 100;
                    else if (text.includes('privacy policy') || text.includes('privacy notice')) score += 90;
                    else if (text.includes('privacy') && text.includes('statement')) score += 85;
                    else if (text.includes('data protection')) score += 80;
                    else if (text.includes('privacy')) score += 70;
                    else if (text === 'cookies' || text === 'cookie policy') score += 60;
                    else if (text.includes('personal') && text.includes('data')) score += 60;
                    
                    // URL matching
                    if (href.includes('privacy-policy') || 
                        href.includes('privacy-notice') || 
                        href.includes('privacy_policy') || 
                        href.includes('privacy_notice')) score += 50;
                    else if (href.includes('data-protection') || 
                             href.includes('data_protection')) score += 45;
                    else if (href.includes('gdpr') || 
                             href.includes('ccpa')) score += 45;
                    else if (href.includes('/privacy/') || 
                             href.includes('/privacypolicy/')) score += 40;
                    else if (href.includes('/privacy') || 
                             href.includes('/privacypolicy')) score += 35;
                    else if (href.includes('cookie') || 
                             href.includes('personal-information')) score += 30;
                    
                    // Add boost for links in the page footer or with legal in the path
                    const isInFooter = footerSelectors.some(sel => 
                        link.closest(sel) !== null);
                    
                    if (isInFooter) score += 20;
                    if (href.includes('/legal/') || href.includes('/policies/')) score += 15;
                
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

        # For high-scoring links that have "Privacy Policy" or "Privacy Notice", follow redirects
        for link in links[:3]:
            if (("privacy policy" in link['text'].lower() or "privacy notice" in link['text'].lower()) 
                and link['score'] >= 90):
                print(f"Found high-quality privacy link: {link['text']} - {link['href']} (Score: {link['score']})")
                try:
                    await page.goto(link['href'], timeout=8000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1500)
                    final_url = page.url  # This will be the redirected URL if any
                    
                    # Verify that this is a privacy page
                    verification = await verify_is_privacy_page(page)
                    if verification.get("isPrivacyPage", False):
                        print(f"✅ Verified as privacy policy after redirect: {final_url}")
                        return final_url, page, final_url
                    else:
                        print(f"❌ Not verified as privacy page after redirect: {final_url}")
                except Exception as e:
                    print(f"Error following high-quality link: {e}")
        
        # If no high-quality links were verified, use the highest scored one
        if links:
            best_link = links[0]["href"]

            # Set unverified result if none exists
            if not unverified_result:
                unverified_result = best_link

            # Try to navigate to the best link
            try:
                print(f"Navigating to best link: {best_link}")
                await page.goto(best_link, timeout=8000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)
                final_url = page.url  # Get final URL after any redirects

                # Verify this is actually a privacy page
                is_privacy_page = await page.evaluate(
                    """() => {
                    const text = document.body.innerText.toLowerCase();
                    const strongPrivacyMatchers = [
                        'privacy policy', 
                        'privacy notice', 
                        'privacy statement',
                        'data protection',
                        'information we collect',
                        'personal data',
                        'cookie policy'
                    ];
                    return strongPrivacyMatchers.some(term => text.includes(term));
                }"""
                )

                if is_privacy_page:
                    print(f"✅ Verified as privacy policy page: {final_url}")
                    return final_url, page, unverified_result
                else:
                    print("⚠️ Link does not appear to be a privacy page after inspection")
                    return None, page, best_link
            except Exception as e:
                print(f"Error navigating to best link: {e}")
                return None, page, best_link

        return None, page, unverified_result

    except Exception as e:
        print(f"Error in JavaScript link finder: {e}")
        return None, page, unverified_result
async def find_matching_privacy_link(page, context, unverified_result=None):
    """Find and click on privacy-related links with optimized performance."""
    try:
        # Use a more targeted selector for performance
        links = await page.query_selector_all(
            'footer a, .footer a, #footer a, a[href*="privacy"], a[href*="data-protection"], a[href*="gdpr"], a[href*="cookies"]'
        )

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
                if "privacy policy" in text or "privacy notice" in text:
                    score = 100
                elif "privacy" in text:
                    score = 80
                elif "data protection" in text:
                    score = 70
                elif "gdpr" in text or "ccpa" in text:
                    score = 65
                elif "cookies" in text:
                    score = 60
                elif "personal data" in text or "personal information" in text:
                    score = 50

                # Additional URL scoring
                if "/privacy-policy" in href or "/privacy_policy" in href:
                    score += 50
                elif "/privacy" in href or "/privacypolicy" in href:
                    score += 40
                elif "/data-protection" in href or "/data_protection" in href:
                    score += 40
                elif "/gdpr" in href or "/cookies" in href:
                    score += 30

                if score > 50:  # High confidence match
                    print(f"Found high confidence link: {text} ({score})")
                    success = await click_and_wait_for_navigation(
                        page, link, timeout=5000
                    )
                    if success:
                        return page.url, page, unverified_result
            except Exception as e:
                continue
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_privacy_link: {e}")
        return None, page, unverified_result


async def click_and_wait_for_navigation(page, element, timeout=5000):
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


async def smooth_scroll_and_click_privacy(
    page, context, unverified_result=None, step=200, delay=100
):
    """Optimized version of smooth scroll with faster execution time for privacy policies."""
    print("🔃 Starting smooth scroll with strong privacy term matching...")
    visited_url = None
    current_page = page

    # First check visible links before scrolling
    visited_url, current_page, unverified_result = await find_matching_privacy_link(
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
            await current_page.wait_for_timeout(300)

            visited_url, current_page, unverified_result = await find_matching_privacy_link(
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

                # Check for privacy links with faster query
                privacy_links = await current_page.evaluate(
                    """(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    const links = Array.from(footer.querySelectorAll('a'));
                    return links.filter(link => {
                        const text = link.textContent.toLowerCase();
                        const href = link.href.toLowerCase();
                        return text.includes('privacy') || 
                               text.includes('data protection') || 
                               text.includes('gdpr') || 
                               text.includes('cookies') || 
                               href.includes('privacy') || 
                               href.includes('data-protection') || 
                               href.includes('gdpr') || 
                               href.includes('cookies');
                    }).map(link => ({
                        text: link.textContent.trim(),
                        href: link.href
                    }));
                }""",
                    selector,
                )

                if privacy_links and len(privacy_links) > 0:
                    print(f"Found {len(privacy_links)} potential privacy links in footer")
                    # Limit to first 3 links for speed
                    for link in privacy_links[:3]:
                        try:
                            element = await current_page.query_selector(
                                f"a[href='{link['href']}']"
                            )
                            if element:
                                success = await click_and_wait_for_navigation(
                                    current_page, element, timeout=5000
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
async def verify_is_privacy_page(page):
    """
    Verifies if the page is likely a privacy policy.
    Returns a dict with isPrivacyPage (bool) and confidence (float).
    """
    print("\n=== Verifying if URL is privacy page ===")
    print(f"URL being verified: {page.url}")

    try:
        # Wait for content to load
        await page.wait_for_timeout(800)

        # Extract and analyze key page signals
        page_signals = await page.evaluate(
            """() => {
            // Get document content to analyze
            const title = document.title.toLowerCase();
            const bodyText = document.body.innerText.toLowerCase();
            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                .map(h => h.innerText.toLowerCase());
            const metaDesc = document.querySelector('meta[name="description"]')?.content?.toLowerCase() || '';
            
            // Get URL for analysis
            const currentUrl = window.location.href.toLowerCase();
            
            // Strong signal terms that indicate privacy content
            const privacyTitleTerms = [
                'privacy', 'privacy policy', 'privacy notice', 'data protection', 
                'data privacy', 'privacy statement', 'privacy information'
            ];
            
            const privacyContentTerms = [
                'personal data', 'information we collect', 'cookies', 
                'data processing', 'gdpr', 'ccpa', 'opt-out', 'opt out',
                'data protection', 'third parties', 'data sharing', 
                'privacy rights', 'data retention', 'data security',
                'personal information', 'collect information'
            ];
            
            const privacyHeadingTerms = [
                'information we collect', 'how we use your information', 
                'data sharing', 'your rights', 'cookies and tracking',
                'data storage', 'data security', 'children\\'s privacy',
                'consent', 'policy changes', 'contact us', 'opt-out',
                'our privacy practices', 'personal information'
            ];
            
            // Analyze the title (this often directly tells us if it's a privacy page)
            const titleScore = privacyTitleTerms.some(term => title.includes(term)) ? 50 : 0;
            
            // Check heading content for privacy sections
            let headingScore = 0;
            let privacyHeadingsCount = 0;
            
            headings.forEach(heading => {
                if (privacyTitleTerms.some(term => heading.includes(term))) {
                    headingScore += 20;
                    privacyHeadingsCount++;
                }
                else if (privacyHeadingTerms.some(term => heading.includes(term))) {
                    headingScore += 10;
                    privacyHeadingsCount++;
                }
            });
            
            // Cap the heading score
            headingScore = Math.min(headingScore, 40);
            
            // Check if the URL contains privacy indicators
            const urlScore = 
                currentUrl.includes('privacy-policy') || 
                currentUrl.includes('privacy_policy') || 
                currentUrl.includes('/privacy/') || 
                currentUrl.includes('/legal/privacy') ? 30 :
                currentUrl.includes('privacy') || 
                currentUrl.includes('data-protection') || 
                currentUrl.includes('gdpr') ? 20 : 0;
            
            // Check content for privacy-specific terms
            let contentScore = 0;
            
            // First, check for strong direct phrases
            if (bodyText.includes('privacy policy') || 
                bodyText.includes('privacy notice') || 
                bodyText.includes('privacy statement')) {
                contentScore += 30;
            }
            
            // Then check for combinations of terms that strongly suggest a privacy policy
            let keyTermsFound = 0;
            privacyContentTerms.forEach(term => {
                if (bodyText.includes(term)) {
                    keyTermsFound++;
                }
            });
            
            // Add to content score based on the number of key terms found
            if (keyTermsFound >= 7) contentScore += 40;
            else if (keyTermsFound >= 5) contentScore += 30;
            else if (keyTermsFound >= 3) contentScore += 20;
            else if (keyTermsFound >= 1) contentScore += 10;
            
            // Negative signals - terms that suggest it's NOT a privacy policy
            const negativeTerms = [
                'sign in', 'login', 'add to cart', 'shopping cart', 
                'search results', '404', 'page not found', 'error',
                'menu'
            ];
            
            const hasNegativeTerms = negativeTerms.some(term => 
                title.includes(term) || (headings.length > 0 && headings[0].includes(term))
            );
            
            // Apply negative score for negative signals, but only if we don't 
            // have strong privacy signals elsewhere
            const negativeScore = (hasNegativeTerms && titleScore === 0) ? -30 : 0;
            
            // Calculate the final score
            let totalScore = titleScore + headingScore + urlScore + contentScore + negativeScore;
            
            // Additional signal: privacy policies often have multiple sections and are longer
            if (privacyHeadingsCount >= 4 && bodyText.length > 3000) {
                totalScore += 20;
            }
            
            // Normalize to 0-100
            totalScore = Math.max(0, Math.min(100, totalScore));
            
            return {
                totalScore,
                titleContainsPrivacy: titleScore > 0,
                privacyHeadingsCount,
                urlContainsPrivacy: urlScore > 0,
                keyTermsCount: keyTermsFound,
                hasNegativeTerms,
                bodyTextLength: bodyText.length
            };
        }"""
        )

        # Lower threshold for privacy page verification
        threshold = 55  # Was 75 before, lowered for better recall

        print(f"Page signals: {page_signals}")
        
        is_privacy_page = page_signals["totalScore"] >= threshold
        
        # Additional check for very short pages
        if is_privacy_page and page_signals["bodyTextLength"] < 500 and page_signals["privacyHeadingsCount"] < 2:
            print("⚠️ Page seems too short to be a complete privacy policy")
            is_privacy_page = False
        
        result = {
            "isPrivacyPage": is_privacy_page,
            "confidence": page_signals["totalScore"] / 100,
            "signals": page_signals
        }

        if is_privacy_page:
            print(f"✅ Verification PASSED with confidence {result['confidence']:.2f}")
        else:
            print(f"❌ Verification FAILED with score {page_signals['totalScore']} (threshold: {threshold})")
            
        return result

    except Exception as e:
        print(f"Error verifying if page is privacy policy: {e}")
        return {"isPrivacyPage": False, "confidence": 0, "error": str(e)}
async def yahoo_search_fallback_privacy(domain, page):
    """Search for privacy policy using Yahoo Search."""
    try:
        print("Attempting search engine fallback with Yahoo...")

        # Create a search query for the domain with specific site constraint
        search_query = f'site:{domain} ("privacy policy" OR "privacy notice" OR "data protection" OR "privacy statement")'
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
                        
                        // Only include links to the target domain or corporate-style external links
                        if (!url.hostname.includes(domain) && !domain.includes(url.hostname)) {
                            const pathLower = url.pathname.toLowerCase();
                            // Allow corporate/external privacy links based on path keywords
                            const corpKeywords = ['company/', 'privacy-center', 'privacy-notice', '/legal/'];
                            if (!corpKeywords.some(k => pathLower.includes(k))) {
                                return false;
                            }
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
                    
                    // Scoring for title matches
                    if (titleLower.includes('privacy policy')) score += 70;
                    else if (titleLower.includes('privacy notice')) score += 65;
                    else if (titleLower.includes('privacy statement')) score += 60;
                    else if (titleLower.includes('data protection')) score += 55;
                    else if (titleLower.includes('privacy')) score += 30;
                    else if (titleLower.includes('gdpr') || titleLower.includes('ccpa')) score += 30;
                    else if (titleLower.includes('cookies')) score += 25;
                    
                    // Scoring for description matches
                    if (descLower.includes('privacy policy')) score += 30;
                    else if (descLower.includes('privacy notice')) score += 28;
                    else if (descLower.includes('data protection')) score += 30;
                    else if (descLower.includes('privacy statement')) score += 33;
                    else if (descLower.includes('gdpr') || descLower.includes('ccpa')) score += 24;
                    else if (descLower.includes('privacy')) score += 15; // Lower priority
                    
                    // URL-based scoring
                    if (urlLower.includes('privacy-policy') || urlLower.includes('privacy_policy')) score += 45;
                    else if (urlLower.includes('privacy-notice') || urlLower.includes('privacy_notice')) score += 43;
                    else if (urlLower.includes('data-protection')) score += 40;
                    else if (urlLower.includes('gdpr') || urlLower.includes('ccpa')) score += 38;
                    else if (urlLower.includes('privacy')) score += 35;
                    else if (urlLower.includes('cookies')) score += 20; // Lower priority
                    
                    return {
                        url: a.href,
                        title: title || "No Title",  // Fallback title
                        description: description,
                        score: score
                    };
                })
                .filter(result => 
                    result.score > 0 || 
                    result.url.toLowerCase().includes('privacy') || 
                    result.url.toLowerCase().includes('gdpr') || 
                    result.url.toLowerCase().includes('data-protection')
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
                    # Visit the page to verify it's a privacy page
                    print(f"Checking result: {best_result}")
                    await page.goto(best_result, timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)  # Increased wait time for full page load
                    final_url = page.url  # Get final URL after redirects

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
                        print(f"⚠️ Captcha/protection detected when accessing: {is_captcha['url']}")
                        # If this is a high-scoring result from the target domain, accept it even with captcha
                        original_url_lower = best_result.lower()
                        has_privacy_path = (
                            "policies" in original_url_lower
                            or "privacy-policy" in original_url_lower
                            or "privacy-notice" in original_url_lower
                            or "data-protection" in original_url_lower
                            or "privacy" in original_url_lower
                        )
                        # Enhanced domain check for major sites
                        from urllib.parse import urlparse
                        parsed_url = urlparse(original_url_lower)
                        domain = parsed_url.netloc.lower()
                        base_domain = domain.replace('www.', '')
                        
                        major_sites = ["amazon.com", "facebook.com", "apple.com", "google.com", 
                                    "microsoft.com", "twitter.com", "netflix.com", "instagram.com"]
                        is_major_site = any(site in base_domain for site in major_sites)

                        # More permissive acceptance for major sites with privacy in URL
                        if (domain in original_url_lower and has_privacy_path and search_results[result_index]["score"] >= 60) or (is_major_site and has_privacy_path and search_results[result_index]["score"] >= 50):
                            print(f"✅ Accepting URL from target domain despite protection: {best_result}")
                            return best_result

                    # Perform two-stage verification for more confidence
                    # First stage: comprehensive verification
                    verification = await verify_is_privacy_page(page)

                    if verification["isPrivacyPage"] and verification["confidence"] >= 60:
                        print(f"✅ First verification passed: {final_url} (score: {verification['confidence']})")
                        return final_url  # Return the final URL after redirects, not the original best_result
                        
                    # Second stage: Simple text check verification (more robust than complex JS evaluation)
                    page_text = await page.evaluate("document.body.innerText.toLowerCase()")
                    
                    # Check for key privacy indicators in a more straightforward way
                    privacy_title_present = any(term in page_text for term in [
                        'privacy policy', 'privacy notice', 'privacy statement'
                    ])
                    
                    data_collection_present = any(term in page_text for term in [
                        'information we collect', 'data we collect', 'collect personal information'
                    ])
                    
                    cookies_present = any(term in page_text for term in [
                        'cookies', 'tracking technologies'
                    ])
                    
                    rights_present = any(term in page_text for term in [
                        'your rights', 'your choices', 'opt out', 'opt-out'
                    ])

                    # Calculate second-stage confidence
                    second_stage_confidence = 0
                    if privacy_title_present:
                        second_stage_confidence += 25
                    if data_collection_present:
                        second_stage_confidence += 25
                    if cookies_present:
                        second_stage_confidence += 25
                    if rights_present:
                        second_stage_confidence += 25

                    if second_stage_confidence >= 50:  # At least two strong markers
                        print(f"✅✅ Complete verification passed (secondary score: {second_stage_confidence}/100)")
                        return final_url

                    print(f"⚠️ Secondary verification failed (score: {second_stage_confidence}/100)")
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

                    verification = await verify_is_privacy_page(page)
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

async def bing_search_fallback_privacy(domain, page):
    """Search for privacy policy using Bing Search."""
    try:
        print("Attempting search engine fallback with Bing...")

        # Create a search query for the domain with specific site constraint and exact term matches
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data protection" OR "privacy statement"'
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
                    
                    // Scoring for title matches
                    if (titleLower.includes('privacy policy')) score += 70;
                    else if (titleLower.includes('privacy notice')) score += 65;
                    else if (titleLower.includes('privacy statement')) score += 60;
                    else if (titleLower.includes('data protection')) score += 55;
                    else if (titleLower.includes('privacy')) score += 30;  // Lower priority for generic terms
                    else if (titleLower.includes('gdpr') || titleLower.includes('ccpa')) score += 30;
                    else if (titleLower.includes('cookies')) score += 25;
                    
                    // Boost URLs in common "privacy" directories
                    if (urlPath === '/privacy' || 
                        urlPath === '/privacy/' ||
                        urlPath === '/privacy-policy' || 
                        urlPath === '/privacy-policy/' ||
                        urlPath === '/privacy-notice' || 
                        urlPath === '/privacy-notice/') {
                        score += 80;  // High boost for exact matches
                    }
                    else if (urlPath === '/legal/privacy' || 
                             urlPath === '/legal/privacy/' ||
                             urlPath === '/policies/privacy' || 
                             urlPath === '/policies/privacy/') {
                        score += 75;  // Also high boost
                    }
                    else if (urlPath.startsWith('/legal/') || 
                             urlPath.startsWith('/policies/') ||
                             urlPath.startsWith('/about/privacy/')) {
                        score += 50;  // Medium boost for general legal directories
                    }
                    
                    // Scoring for description matches
                    if (descLower.includes('privacy policy')) score += 30;
                    else if (descLower.includes('privacy notice')) score += 28;
                    else if (descLower.includes('data protection')) score += 30;
                    else if (descLower.includes('privacy statement')) score += 33;
                    else if (descLower.includes('gdpr') || descLower.includes('ccpa')) score += 24;
                    else if (descLower.includes('privacy')) score += 15;
                    
                    // URL-based scoring
                    if (urlLower.includes('privacy-policy')) score += 40;
                    else if (urlLower.includes('privacy-notice')) score += 38;
                    else if (urlLower.includes('data-protection')) score += 40;
                    else if (urlLower.includes('gdpr') || urlLower.includes('ccpa')) score += 40;
                    else if (urlLower.includes('privacy')) score += 35;
                    else if (urlLower.includes('cookies')) score += 20;
                    
                    return {
                        url: a.href,
                        title: title || "No Title",  // Fallback title
                        description: description,
                        score: score
                    };
                })
                .filter(result => 
                    result.score > 0 || 
                    result.url.toLowerCase().includes('privacy') || 
                    result.url.toLowerCase().includes('gdpr') || 
                    result.url.toLowerCase().includes('data-protection')
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
                    # Visit the page to verify it's a privacy page
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
                            "facebook.com",
                            "meta.com",
                            "instagram.com",
                            "amazon.com",
                            "openai.com",
                            "apple.com",
                            "google.com",
                        ]

                        # Check if it's from a known domain and has privacy policy in URL
                        is_known_domain = any(
                            domain in original_url_lower
                            for domain in captcha_bypass_domains
                        )
                        has_privacy_path = (
                            "privacy-policy" in original_url_lower
                            or "privacy-notice" in original_url_lower
                            or "data-protection" in original_url_lower
                            or "privacy" in original_url_lower
                            or "policies" in original_url_lower
                        )

                        if (
                            is_known_domain
                            and has_privacy_path
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
                    verification = await verify_is_privacy_page(page)

                    if verification["isPrivacyPage"] and verification["confidence"] >= 60:
                        print(f"✅ Verified privacy page from Bing results: {page.url}")
                        return page.url
                    else:
                        print(
                            f"❌ Not a valid Privacy page (verification score: {verification['confidence']})"
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

                    verification = await verify_is_privacy_page(page)
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

async def duckduckgo_search_fallback_privacy(domain, page):
    """Search for privacy policy using DuckDuckGo Search."""
    try:
        print("Attempting search engine fallback with DuckDuckGo...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data protection" OR "privacy statement"'
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
                    if (titleLower.includes('privacy policy')) score += 50;
                    else if (titleLower.includes('privacy notice')) score += 48;
                    else if (titleLower.includes('privacy statement')) score += 45;
                    else if (titleLower.includes('data protection')) score += 40;
                    else if (titleLower.includes('privacy')) score += 30;
                    else if (titleLower.includes('gdpr') || titleLower.includes('ccpa')) score += 20;
                    if (descLower.includes('privacy policy')) score += 20;
                    else if (descLower.includes('privacy notice')) score += 18;
                    else if (descLower.includes('data protection')) score += 16;
                    else if (descLower.includes('privacy') && descLower.includes('data')) score += 15;
                    if (urlLower.includes('/privacy-policy') || urlLower.includes('/privacy-notice')) score += 60;
                    else if (urlLower.includes('/data-protection') || urlLower.includes('/gdpr')) score += 55;
                    else if (urlLower.includes('/privacy/') || urlLower.includes('/privacy')) score += 50;
                    else if (urlLower.includes('/legal/privacy')) score += 45;
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
                    verification = await verify_is_privacy_page(page)
                    if verification["isPrivacyPage"]:
                        print(f"✅ Verified privacy page from DuckDuckGo results: {page.url}")
                        return page.url
                    else:
                        print(f"Not a privacy page (score: {verification['confidence']})")
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


def handle_navigation_failure_privacy(url: str, unverified_result: str = None) -> PrivacyResponse:
    """Handle case where navigation to the URL failed for Privacy Policy."""
    if unverified_result:
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found potential Privacy Policy link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    return PrivacyResponse(
        url=url,
        pp_url=None,
        success=False,
        message="Failed to navigate to website and find Privacy Policy page",
        method_used="none"
    )


def handle_error_privacy(url: str, unverified_result: str, error: str) -> PrivacyResponse:
    """Simplified error handler for Privacy Policy."""
    if unverified_result:
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found potential Privacy Policy link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    return PrivacyResponse(
        url=url,
        pp_url=None,
        success=False,
        message=f"Error during browser automation: {error}",
        method_used="none"
    )


def prefer_main_domain(links, main_domain):
    # main_domain: e.g. "amazon.com"
    def is_main_domain(url):
        parsed = urlparse(url)
        # Accept www.amazon.com or amazon.com, but not foo.amazon.com
        return parsed.netloc == main_domain or parsed.netloc == f"www.{main_domain}"
    main_links = [l for l in links if is_main_domain(l)]
    # If we have any main domain links, return only those
    if main_links:
        return main_links
    # Otherwise, allow subdomains as fallback
    return links