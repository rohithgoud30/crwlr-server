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
            js_result, page, js_unverified = await find_all_privacy_links_js(page, domain)
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

        # Sort by score: prioritize is_privacy True, then internal domain, then confidence
        plain_domain = domain.replace('www.', '')
        scored_links.sort(
            key=lambda x: (
                x["is_privacy"],
                urlparse(x["url"]).netloc.replace('www.', '').endswith(plain_domain),
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
async def find_all_privacy_links_js(page, domain):
    """Find all potential privacy policy links using JavaScript evaluation."""
    try:
        # Wait for the page to fully load (reduced wait time)
        await page.wait_for_timeout(400)  # Reduced from 800ms

        # Quick-check: Look for immediate privacy links first without complex evaluations
        quick_found = await page.evaluate("""() => {
            try {
                // First try direct matches - these are high confidence links
                const quickMatches = [
                    // Clear privacy policy text patterns (case insensitive)
                    ...[...document.querySelectorAll('a')].filter(a => {
                        const text = a.innerText.toLowerCase().trim();
                        const href = a.href.toLowerCase();
                        
                        // Very clear privacy indicators in text
                        return (text === 'privacy policy' || 
                                text === 'privacy notice' || 
                                text === 'privacy' ||
                                text === 'data protection policy') &&
                               // With matching URL patterns
                               (href.includes('/privacy') || 
                                href.includes('/policies') ||
                                href.includes('/legal'));
                    }),
                    
                    // Clear privacy policy URL patterns
                    ...[...document.querySelectorAll('a[href*="privacy-policy"], a[href*="privacy_policy"]')],
                    ...[...document.querySelectorAll('a[href*="/privacy"][href*=".html"], a[href*="/privacy"][href*=".php"]')]
                ];
                
                if (quickMatches.length > 0) {
                    // Sort by priority (text clarity and URL pattern)
                    const sorted = quickMatches.sort((a, b) => {
                        const aText = a.innerText.toLowerCase().trim();
                        const bText = b.innerText.toLowerCase().trim();
                        const aHref = a.href.toLowerCase();
                        const bHref = b.href.toLowerCase();
                        
                        // Exact matches get highest priority
                        if (aText === 'privacy policy' && bText !== 'privacy policy') return -1;
                        if (bText === 'privacy policy' && aText !== 'privacy policy') return 1;
                        
                        // Next, prefer links with privacy in both text and URL
                        const aHasPrivacyBoth = aText.includes('privacy') && aHref.includes('privacy');
                        const bHasPrivacyBoth = bText.includes('privacy') && bHref.includes('privacy');
                        if (aHasPrivacyBoth && !bHasPrivacyBoth) return -1;
                        if (bHasPrivacyBoth && !aHasPrivacyBoth) return 1;
                        
                        // Finally prefer shorter URLs
                        return aHref.length - bHref.length;
                    });
                    
                    // Format results to match expected structure
                    return sorted.slice(0, 3).map(link => ({
                        text: link.innerText.trim() || link.textContent.trim() || "No text",
                        href: link.href,
                        confidence: 85, // High confidence for direct matches
                        isExternal: !link.href.includes(window.location.hostname),
                        targetBlank: link.getAttribute('target') === '_blank',
                        rel: link.getAttribute('rel') || ''
                    }));
                }
                return null;
            } catch (e) {
                console.error("Error in quick privacy link detection:", e);
                return null;
            }
        }""")
        
        if quick_found and len(quick_found) > 0:
            print(f"✅ Found {len(quick_found)} clear privacy links with quick detection!")
            top_quick_link = quick_found[0]
            
            # Skip links that clearly open in new tab/window to avoid timeouts
            if top_quick_link.get("targetBlank") or "noopener" in top_quick_link.get("rel", ""):
                print("⚠️ Top link opens in new tab/window, returning without navigation")
                return quick_found
                
            # Try to navigate to the top link to get final URL
            try:
                await click_and_wait_for_navigation(
                    page, 
                    f"""document.querySelector('a[href="{top_quick_link['href']}"]')""",
                    timeout=4000  # Reduced from 5000ms
                )
                await page.wait_for_timeout(300)  # Reduced from 500ms
                top_quick_link["href"] = page.url  # Update with final URL after redirects
            except Exception as e:
                print(f"Navigation failed for quick link: {e}")
                
            return quick_found

        # Check if we're likely facing anti-bot protection
        anti_bot_check = await page.evaluate(
            """() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            return {
                isAntiBot: html.includes('captcha') || 
                           html.includes('recaptcha') ||
                           html.includes('cloudflare') ||
                           html.includes('security check') ||
                           html.includes('ddos') ||
                           html.includes('please verify') ||
                           html.includes('challenge') ||
                           html.includes('just a moment') ||
                           document.title.toLowerCase().includes('just a moment') ||
                           document.title.toLowerCase().includes('please wait'),
                title: document.title
            };
        }"""
        )

        if anti_bot_check.get("isAntiBot", False):
            print(f"⚠️ Anti-bot protection detected: {anti_bot_check.get('title', 'Unknown Title')}")
            print("Trying alternative approach focusing on footer links...")

            # When anti-bot is detected, specifically target footer links
            footer_links = await page.evaluate(
                """() => {
                // Find all potential footers
                const potentialFooters = [
                    ...document.querySelectorAll('footer'),
                    ...document.querySelectorAll('[class*="footer"]'),
                    ...document.querySelectorAll('[id*="footer"]'),
                    ...document.querySelectorAll('[role="contentinfo"]'),
                    ...document.querySelectorAll('.bottom'),
                    ...document.querySelectorAll('.legal'),
                    ...document.querySelectorAll('.links')
                ];
                
                if (potentialFooters.length === 0) {
                    // If no clear footer, check the bottom 20% of the page
                    const bodyHeight = document.body.offsetHeight;
                    const bottomThreshold = bodyHeight * 0.8;
                    
                    // Get all links in bottom 20% of page
                    const allLinks = [...document.querySelectorAll('a[href]')];
                    const bottomLinks = allLinks.filter(link => {
                        const rect = link.getBoundingClientRect();
                        const linkYPosition = rect.top + window.scrollY;
                        return linkYPosition > bottomThreshold;
                    });
                    
                    if (bottomLinks.length > 0) {
                        // Score and filter links
                        return getPrivacyLinksWithScores(bottomLinks);
                    }
                }
                
                // Get all links from potential footers
                const footerLinks = [];
                potentialFooters.forEach(footer => {
                    if (footer) {
                        const links = footer.querySelectorAll('a[href]');
                        footerLinks.push(...links);
                    }
                });
                
                // Score and filter links
                return getPrivacyLinksWithScores(footerLinks);
                
                function getPrivacyLinksWithScores(links) {
                    const privacyLinks = [];
                    
                    links.forEach(link => {
                        const href = link.href.toLowerCase().trim();
                        const text = (link.innerText || link.textContent).toLowerCase().trim();
                        
                        // Skip if empty text or hash-only link
                        if (!text || href === '#' || href.startsWith('javascript:')) return;
                        
                        // Skip navigation/pagination links
                        if (text.match(/^(\d+|next|prev|previous|back|forward)$/)) return;
                        
                        // Calculate score
                        let score = 0;
                        
                        // Text-based scoring
                        if (text === 'privacy policy') score += 100;
                        else if (text === 'privacy notice') score += 95;
                        else if (text === 'privacy') score += 90;
                        else if (text === 'privacy statement') score += 90;
                        else if (text.includes('privacy policy')) score += 85;
                        else if (text.includes('privacy notice')) score += 80;
                        else if (text.includes('data protection')) score += 75;
                        else if (text.includes('data policy')) score += 75;
                        else if (text.includes('privacy statement')) score += 70;
                        else if (text.includes('privacy rights')) score += 70;
                        else if (text === 'privacy & cookies') score += 65;
                        else if (text.includes('privacy')) score += 60;
                        else if (text.includes('cookies policy')) score += 55;
                        else if (text.includes('cookie policy')) score += 55;
                        else if (text.includes('cookie notice')) score += 50;
                        else if (text.includes('cookies')) score += 45;
                        else if (text.includes('terms')) score += 30;
                        else if (text.includes('legal')) score += 25;
                        
                        // URL-based scoring
                        if (href.includes('privacy-policy')) score += 90;
                        else if (href.includes('privacy_policy')) score += 90;
                        else if (href.includes('privacy-notice')) score += 85;
                        else if (href.includes('privacy_notice')) score += 85;
                        else if (href.includes('privacy-statement')) score += 80;
                        else if (href.includes('privacy_statement')) score += 80;
                        else if (href.includes('data-privacy')) score += 75;
                        else if (href.includes('data_privacy')) score += 75;
                        else if (href.includes('data-protection')) score += 75;
                        else if (href.includes('data_protection')) score += 75;
                        else if (href.includes('/privacy/')) score += 70;
                        else if (href.includes('/privacy')) score += 65;
                        else if (href.includes('/datenschutz')) score += 60; // German
                        else if (href.includes('cookie-policy')) score += 55;
                        else if (href.includes('cookie_policy')) score += 55;
                        else if (href.includes('/cookies')) score += 50;
                        else if (href.includes('/legal/')) score += 45;
                        else if (href.includes('/terms/')) score += 40;
                        
                        // Additional boost for user-specific privacy links
                        if (text.includes('user') || href.includes('user')) score += 100;
                        // Additional boost for customer-specific privacy links
                        if (text.includes('customer') || href.includes('customer')) score += 100;
                        
                        // Penalty for job/career/talent privacy links
                        if ((text.includes('job') || href.includes('job') || 
                             text.includes('career') || href.includes('career') ||
                             text.includes('talent') || href.includes('talent')) && 
                            (text.includes('privacy') || href.includes('privacy'))) {
                            score -= 50;
                        }
                        
                        // Penalty for California-specific links
                        if ((text.includes('california') || href.includes('california') || 
                             text.includes('ccpa')) && 
                            (text.includes('privacy') || href.includes('privacy'))) {
                            score -= 20;
                        }
                        
                        // Only include if score above minimum threshold
                        if (score >= 25) {
                            privacyLinks.push({
                                text: text,
                                href: href,
                                confidence: score,
                                isExternal: !href.includes(window.location.hostname),
                                targetBlank: link.getAttribute('target') === '_blank',
                                rel: link.getAttribute('rel') || ''
                            });
                        }
                    });
                    
                    // Sort by confidence score (highest first)
                    privacyLinks.sort((a, b) => b.confidence - a.confidence);
                    
                    // Return top 3 links only
                    return privacyLinks.slice(0, 3);
                }
            }"""
            )

            if footer_links and len(footer_links) > 0:
                return footer_links
            else:
                print("No privacy links found in footer with anti-bot protection")
                return []

        # Original comprehensive evaluation for no anti-bot case
        privacy_links = await page.evaluate(
            """() => {
            // Find all links in the document
            const allLinks = Array.from(document.querySelectorAll('a[href]'));
            
            // Filter and score links based on text content and URL
            const privacyLinks = allLinks
                .map(link => {
                    const text = (link.innerText || link.textContent).toLowerCase().trim();
                    const href = link.href.toLowerCase();
                    
                    // Skip if empty text or hash-only link
                    if (!text || href === '#' || href.startsWith('javascript:')) {
                        return null;
                    }
                    
                    // Skip navigation/pagination links
                    if (text.match(/^(\d+|next|prev|previous|back|forward)$/)) {
                        return null;
                    }
                    
                    // Calculate confidence score
                    let score = 0;
                    
                    // Text-based scoring
                    if (text === 'privacy policy') score += 100;
                    else if (text === 'privacy notice') score += 95;
                    else if (text === 'privacy') score += 90;
                    else if (text === 'privacy statement') score += 90;
                    else if (text.includes('privacy policy')) score += 85;
                    else if (text.includes('privacy notice')) score += 80;
                    else if (text.includes('data protection')) score += 75;
                    else if (text.includes('data policy')) score += 75;
                    else if (text.includes('privacy statement')) score += 70;
                    else if (text.includes('privacy rights')) score += 70;
                    else if (text === 'privacy & cookies') score += 65;
                    else if (text.includes('privacy')) score += 60;
                    else if (text.includes('cookies policy')) score += 55;
                    else if (text.includes('cookie policy')) score += 55;
                    else if (text.includes('cookie notice')) score += 50;
                    else if (text.includes('cookies')) score += 45;
                    else if (text.includes('terms')) score += 30;
                    else if (text.includes('legal')) score += 25;
                    
                    // URL-based scoring
                    if (href.includes('privacy-policy')) score += 90;
                    else if (href.includes('privacy_policy')) score += 90;
                    else if (href.includes('privacy-notice')) score += 85;
                    else if (href.includes('privacy_notice')) score += 85;
                    else if (href.includes('privacy-statement')) score += 80;
                    else if (href.includes('privacy_statement')) score += 80;
                    else if (href.includes('data-privacy')) score += 75;
                    else if (href.includes('data_privacy')) score += 75;
                    else if (href.includes('data-protection')) score += 75;
                    else if (href.includes('data_protection')) score += 75;
                    else if (href.includes('/privacy/')) score += 70;
                    else if (href.includes('/privacy')) score += 65;
                    else if (href.includes('/datenschutz')) score += 60; // German
                    else if (href.includes('cookie-policy')) score += 55;
                    else if (href.includes('cookie_policy')) score += 55;
                    else if (href.includes('/cookies')) score += 50;
                    else if (href.includes('/legal/')) score += 45;
                    else if (href.includes('/terms/')) score += 40;
                    
                    // Additional boost for user-specific privacy links
                    if (text.includes('user') || href.includes('user')) score += 100;
                    // Additional boost for customer-specific privacy links
                    if (text.includes('customer') || href.includes('customer')) score += 100;
                    
                    // Penalty for job/career/talent privacy links
                    if ((text.includes('job') || href.includes('job') || 
                         text.includes('career') || href.includes('career') ||
                         text.includes('talent') || href.includes('talent')) && 
                        (text.includes('privacy') || href.includes('privacy'))) {
                        score -= 50;
                    }
                    
                    // Penalty for California-specific links
                    if ((text.includes('california') || href.includes('california') || 
                         text.includes('ccpa')) && 
                        (text.includes('privacy') || href.includes('privacy'))) {
                        score -= 20;
                    }
                    
                    // Skip links with very low scores
                    if (score < 25) {
                        return null;
                    }
                    
                    // Check if link will likely open in a new tab/window
                    const targetBlank = link.getAttribute('target') === '_blank';
                    const rel = link.getAttribute('rel') || '';
                    
                    return {
                        text: text,
                        href: href,
                        confidence: score,
                        isExternal: !href.includes(window.location.hostname),
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
                    await page.wait_for_timeout(1000)  # Allow time for page to load

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
                    await page.wait_for_timeout(1000)  # Reduced wait time for full page load

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

async def find_matching_link_privacy(page, context, unverified_result=None):
    """Find and click on privacy-related links with optimized performance."""
    try:
        # Use a more targeted selector for performance
        links = await page.query_selector_all(
            'footer a, .footer a, #footer a, a[href*="privacy"], a[href*="data"], a[href*="legal"]'
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
                elif "data" in text and ("protection" in text or "privacy" in text):
                    score = 70
                elif "legal" in text:
                    score = 50

                # Additional URL scoring
                if "/privacy-policy" in href or "/privacy_policy" in href:
                    score += 50
                elif "/privacy" in href or "/data-privacy" in href:
                    score += 40
                elif "/legal" in href:
                    score += 30

                if score > 50:  # High confidence match
                    print(f"Found high confidence privacy link: {text} ({score})")
                    success = await click_and_wait_for_navigation(
                        page, link, timeout=5000
                    )
                    if success:
                        return page.url, page, unverified_result
            except Exception as e:
                continue
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_link_privacy: {e}")
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
    visited_url, current_page, unverified_result = await find_matching_link_privacy(
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

            visited_url, current_page, unverified_result = await find_matching_link_privacy(
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
                               text.includes('data') || 
                               href.includes('privacy') || 
                               href.includes('data-privacy');
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

async def bing_search_fallback_privacy(domain, page):
    """Search for privacy policy using Bing Search."""
    try:
        print("Attempting search engine fallback with Bing...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data protection" OR "privacy statement"'
        bing_search_url = f"https://www.bing.com/search?q={search_query}"

        await page.goto(bing_search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        search_results = await page.evaluate("""(domain) => {
            const results = [];
            const links = Array.from(document.querySelectorAll('#b_results .b_algo h2 a, #b_results .b_algo .b_title a, #b_results .b_algo a.tilk'));
            
            for (const link of links) {
                try {
                    // Skip if link doesn't have proper URL or doesn't contain domain
                    const href = link.href.toLowerCase();
                    if (!href || !href.includes(domain.toLowerCase())) continue;
                    
                    // Skip search engine links
                    if (href.includes('bing.com') || 
                        href.includes('google.com') || 
                        href.includes('yahoo.com') ||
                        href.includes('duckduckgo.com')) continue;
                    
                    // Get title and snippet
                    const title = link.textContent.trim();
                    let description = '';
                    let parentElem = link.closest('.b_algo');
                    if (parentElem) {
                        const descElem = parentElem.querySelector('.b_caption p');
                        if (descElem) description = descElem.textContent.trim();
                    }
                    
                    // Score the result
                    let score = 0;
                    const titleLower = title.toLowerCase();
                    const descLower = description.toLowerCase();
                    const urlLower = href.toLowerCase();
                    
                    // Title scoring
                    if (titleLower.includes('privacy policy')) score += 50;
                    else if (titleLower.includes('privacy notice')) score += 48;
                    else if (titleLower.includes('privacy statement')) score += 45;
                    else if (titleLower.includes('data protection')) score += 40;
                    else if (titleLower.includes('privacy')) score += 30;
                    else if (titleLower.includes('gdpr') || titleLower.includes('ccpa')) score += 20;
                    
                    // Description scoring
                    if (descLower.includes('privacy policy')) score += 20;
                    else if (descLower.includes('privacy notice')) score += 18;
                    else if (descLower.includes('data protection')) score += 16;
                    else if (descLower.includes('privacy') && descLower.includes('data')) score += 15;
                    
                    // URL scoring
                    if (urlLower.includes('/privacy-policy') || urlLower.includes('/privacy-notice')) score += 60;
                    else if (urlLower.includes('/data-protection') || urlLower.includes('/gdpr')) score += 55;
                    else if (urlLower.includes('/privacy/') || urlLower.includes('/privacy')) score += 50;
                    else if (urlLower.includes('/legal/privacy')) score += 45;
                    else if (urlLower.includes('/legal/')) score += 30;
                    
                    // Corporate/official domains boost
                    const corpKeywords = ['company/', 'privacy-center', 'privacy-notice', '/legal/', '/about/', '/corporate/'];
                    if (corpKeywords.some(kw => urlLower.includes(kw))) {
                        score += 15;
                    }
                    
                    // Filter to include corporate privacy links even if hostname doesn't match
                    const currentHostname = new URL(href).hostname.replace('www.', '');
                    const mainDomain = domain.replace('www.', '');
                    
                    // Always include if it's the same domain
                    let shouldInclude = currentHostname.includes(mainDomain) || mainDomain.includes(currentHostname);
                    
                    if (!shouldInclude) {
                        // Include external privacy links with corporate-style paths
                        const path = new URL(href).pathname.toLowerCase();
                        shouldInclude = corpKeywords.some(kw => path.includes(kw)) && 
                                       (urlLower.includes('privacy') || urlLower.includes('data-protection'));
                    }
                    
                    if (shouldInclude) {
                        results.push({
                            url: href,
                            title: title || 'No Title',
                            description: description || 'No Description',
                            score: score
                        });
                    }
                } catch (e) {
                    // Skip problematic links
                    continue;
                }
            }
            
            // Deduplicate by URL
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
            print(f"Found {len(search_results)} potential results from Bing search")
            
            # Sort by score
            search_results.sort(key=lambda x: x["score"], reverse=True)
            
            # Store the best result in case all verifications fail
            best_result_url = search_results[0]["url"]
            best_result_score = search_results[0]["score"]
            
            # Display top results for debugging
            for i, result in enumerate(search_results[:3]):
                print(f"Result #{i+1}: {result['title']} - {result['url']} (Score: {result['score']})")
            
            # Check the top 5 results
            for result_index in range(min(5, len(search_results))):
                best_result = search_results[result_index]["url"]
                
                try:
                    # Visit the page to verify it's a privacy page
                    print(f"Checking result: {best_result}")
                    await page.goto(best_result, timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)  # Allow time for page to load
                    
                    # Check if we hit a captcha
                    is_captcha = await page.evaluate("""() => {
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
                    }""")
                    
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
                        is_known_domain = any(domain in original_url_lower for domain in captcha_bypass_domains)
                        has_privacy_path = (
                            "privacy-policy" in original_url_lower or
                            "privacy-notice" in original_url_lower or
                            "data-protection" in original_url_lower or
                            "privacy" in original_url_lower or
                            "policies" in original_url_lower
                        )
                        
                        if is_known_domain and has_privacy_path and search_results[result_index]["score"] >= 60:
                            print(f"✅ Accepting high-scoring URL from known domain despite captcha: {best_result}")
                            return best_result
                        else:
                            print(f"❌ Not accepting captcha-protected URL as it doesn't meet criteria")
                    
                    # Perform verification
                    verification = await verify_is_privacy_page(page)
                    
                    if verification["isPrivacyPage"] and verification["confidence"] >= 60:
                        print(f"✅ Verified privacy page from Bing results: {page.url}")
                        return page.url
                    else:
                        print(f"❌ Not a valid Privacy page (verification score: {verification['confidence']})")
                except Exception as e:
                    print(f"Error checking Bing search result: {e}")
            
            # If we checked all results but none were verified, consider the highest scored result
            # with a minimum score threshold
            if len(search_results) > 0 and search_results[0]["score"] >= 70:
                print(f"⚠️ No verified pages found. Checking highest-scored result: {search_results[0]['url']} (Score: {search_results[0]['score']})")
                try:
                    await page.goto(search_results[0]["url"], timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)  # Reduced wait time for full page load
                    
                    verification = await verify_is_privacy_page(page)
                    if verification["confidence"] >= 50:  # Higher minimum threshold
                        print(f"⚠️ Final verification passed with sufficient confidence: {verification['confidence']}")
                        return page.url
                    else:
                        print(f"❌ Final verification failed with confidence score: {verification['confidence']}")
                        # Return the highest-scored result even if verification fails
                        print(f"⚠️ Returning highest-scored result as last resort: {search_results[0]['url']}")
                        return search_results[0]["url"]
                except Exception as e:
                    print(f"Error in final verification: {e}")
                    # Return the highest-scored result even if verification fails due to error
                    print(f"⚠️ Verification failed with error, returning highest-scored result: {search_results[0]['url']}")
                    return search_results[0]["url"]
            
            # Return the best result anyway if it has a decent score (60+)
            if best_result_score >= 60:
                print(f"⚠️ All verification methods failed, returning best unverified result: {best_result_url} (Score: {best_result_score})")
                return best_result_url
        
        print("No relevant Bing search results found")
        return None
    except Exception as e:
        print(f"Error in Bing search fallback: {e}")
        return None

async def yahoo_search_fallback_privacy(domain, page):
    """Search for privacy policy using Yahoo Search."""
    try:
        print("Attempting search engine fallback with Yahoo...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data protection" OR "privacy statement"'
        yahoo_search_url = f"https://search.yahoo.com/search?p={search_query}"

        await page.goto(yahoo_search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        search_results = await page.evaluate("""(domain) => {
            const results = [];
            // Yahoo search result selectors
            const resultSelectors = [
                'div.algo-sr a', // Standard results
                'div.algo a',     // Alternative result format
                'h3.title a',     // Another format
                '#web a.d-ib'     // Another format
            ];
            
            // Try each selector until we find some results
            let links = [];
            for (const selector of resultSelectors) {
                links = Array.from(document.querySelectorAll(selector));
                if (links.length > 0) break;
            }
            
            for (const link of links) {
                try {
                    // Skip if link doesn't have proper URL or doesn't contain domain
                    const href = link.href.toLowerCase();
                    if (!href || !href.includes(domain.toLowerCase())) continue;
                    
                    // Skip search engine links
                    if (href.includes('yahoo.com') || 
                        href.includes('google.com') || 
                        href.includes('bing.com') ||
                        href.includes('duckduckgo.com')) continue;
                    
                    // Get title and snippet
                    const title = link.textContent.trim();
                    let description = '';
                    
                    // Look for description in parent elements
                    let parentElem = link.closest('div.algo') || link.closest('div.algo-sr');
                    if (parentElem) {
                        const descElem = parentElem.querySelector('p.fz-ms') || 
                                        parentElem.querySelector('p') ||
                                        parentElem.querySelector('div.compText');
                        if (descElem) description = descElem.textContent.trim();
                    }
                    
                    // Score the result
                    let score = 0;
                    const titleLower = title.toLowerCase();
                    const descLower = description.toLowerCase();
                    const urlLower = href.toLowerCase();
                    
                    // Title scoring
                    if (titleLower.includes('privacy policy')) score += 50;
                    else if (titleLower.includes('privacy notice')) score += 48;
                    else if (titleLower.includes('privacy statement')) score += 45;
                    else if (titleLower.includes('data protection')) score += 40;
                    else if (titleLower.includes('privacy')) score += 30;
                    else if (titleLower.includes('gdpr') || titleLower.includes('ccpa')) score += 20;
                    
                    // Description scoring
                    if (descLower.includes('privacy policy')) score += 20;
                    else if (descLower.includes('privacy notice')) score += 18;
                    else if (descLower.includes('data protection')) score += 16;
                    else if (descLower.includes('privacy') && descLower.includes('data')) score += 15;
                    
                    // URL scoring
                    if (urlLower.includes('/privacy-policy') || urlLower.includes('/privacy-notice')) score += 60;
                    else if (urlLower.includes('/data-protection') || urlLower.includes('/gdpr')) score += 55;
                    else if (urlLower.includes('/privacy/') || urlLower.includes('/privacy')) score += 50;
                    else if (urlLower.includes('/legal/privacy')) score += 45;
                    else if (urlLower.includes('/legal/')) score += 30;
                    
                    // Corporate/official domains boost
                    const corpKeywords = ['company/', 'privacy-center', 'privacy-notice', '/legal/', '/about/', '/corporate/'];
                    if (corpKeywords.some(kw => urlLower.includes(kw))) {
                        score += 15;
                    }
                    
                    // Filter to include corporate privacy links even if hostname doesn't match
                    const currentHostname = new URL(href).hostname.replace('www.', '');
                    const mainDomain = domain.replace('www.', '');
                    
                    // Always include if it's the same domain
                    let shouldInclude = currentHostname.includes(mainDomain) || mainDomain.includes(currentHostname);
                    
                    if (!shouldInclude) {
                        // Include external privacy links with corporate-style paths
                        const path = new URL(href).pathname.toLowerCase();
                        shouldInclude = corpKeywords.some(kw => path.includes(kw)) && 
                                       (urlLower.includes('privacy') || urlLower.includes('data-protection'));
                    }
                    
                    if (shouldInclude) {
                        results.push({
                            url: href,
                            title: title || 'No Title',
                            description: description || 'No Description',
                            score: score
                        });
                    }
                } catch (e) {
                    // Skip problematic links
                    continue;
                }
            }
            
            // Deduplicate by URL
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
            print(f"Found {len(search_results)} potential results from Yahoo search")
            
            # Sort by score
            search_results.sort(key=lambda x: x["score"], reverse=True)
            
            # Display top results for debugging
            for i, result in enumerate(search_results[:3]):
                print(f"Result #{i+1}: {result['title']} - {result['url']} (Score: {result['score']})")
            
            # Check the top 3 results
            for result in search_results[:3]:
                try:
                    await page.goto(result["url"], timeout=10000, wait_until="domcontentloaded")
                    await page.wait_for_timeout(1000)
                    
                    verification = await verify_is_privacy_page(page)
                    if verification["isPrivacyPage"]:
                        print(f"✅ Verified privacy page from Yahoo results: {page.url}")
                        return page.url
                    else:
                        print(f"Not a privacy page (score: {verification['confidence']})")
                except Exception as e:
                    print(f"Error checking Yahoo search result: {e}")
            
            # If no verification succeeded but we have high-confidence results, return the best one
            if search_results[0]["score"] >= 60:
                print(f"⚠️ Returning unverified high-confidence result: {search_results[0]['url']}")
                return search_results[0]["url"]
            
        print("No relevant Yahoo search results found")
        return None
    except Exception as e:
        print(f"Error in Yahoo search fallback: {e}")
        return None