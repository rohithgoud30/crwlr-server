import asyncio
import re
import random
import logging
import sys
from typing import Optional, Tuple, Dict, List, Any
from urllib.parse import urlparse, urljoin

from fastapi import APIRouter, HTTPException, Depends, status
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, TimeoutError as PlaywrightTimeoutError

# Define User_Agents list for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

from app.models.privacy import PrivacyRequest, PrivacyResponse
import requests
from bs4 import BeautifulSoup

router = APIRouter()

# Priorities for exact match terms
exactMatchPriorities = {
    'privacy policy': 100,
    'privacy notice': 95,
    'privacy statement': 90,
    'data policy': 85,
    'data protection policy': 80,
    'privacy information': 75,
    'data privacy': 70
}

# Priorities for partial match terms
partialMatchPriorities = {
    'privacy': 60,
    'data protection': 55,
    'personal data': 50,
    'data collection': 45,
    'information collection': 40,
    'cookie policy': 35,
    'cookie notice': 30,
    'how we use your data': 25,
    'data usage': 20
}

# Define your strong match terms here
strong_privacy_matches = [
    'privacy policy', 'privacy notice', 'privacy statement',
    'data policy', 'data protection', 'privacy', 'data privacy',
    'personal information', 'cookie policy'
]

# Define dynamic scoring weights for link evaluation
LINK_EVALUATION_WEIGHTS = {
    'text_match': 0.4,
    'url_structure': 0.3,
    'context': 0.2,
    'position': 0.1
}

@router.post("/privacy", response_model=PrivacyResponse)
async def find_privacy(request: PrivacyRequest) -> PrivacyResponse:
    """Find Privacy Policy page for a given URL."""
    url = request.url
    
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    result = None
    method_used = ""
    browser = None
    playwright = None
    unverified_result = None  # Initialize unverified_result here
    
    # Reduced set of common paths to try
    common_paths = [
        '/privacy', '/privacy-policy', '/privacy-notice', 
        '/legal/privacy'
    ]
    
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    
    try:
        # Get playwright instance first and pass it to setup_browser
        playwright = await async_playwright().start()
        browser, context, page, random_delay = await setup_browser(playwright)
        
        success, response, patterns = await navigate_with_retry(page, url, max_retries=2)  # Reduced retries
        if not success:
            # Try common paths before giving up (fewer paths, faster checks)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            for path in common_paths:
                common_url = base_url + path
                print(f"Trying common path: {common_url}")
                try:
                    success, response, patterns = await navigate_with_retry(page, common_url, max_retries=1)  # Only 1 retry
                    if success and response.ok:
                        unverified_result = common_url
                        break
                except Exception as e:
                    print(f"Error trying common path {common_url}: {e}")
                    continue
            
            return handle_navigation_failure(url, unverified_result)
        
        # Optimized method ordering: Try fastest methods first
        methods = [
            (find_all_links_js, "javascript"),
            (smooth_scroll_and_click, "scroll"),
            (find_matching_link, "standard"),
            (analyze_landing_page, "content_analysis")
        ]
        
        for method_func, method_name in methods:
            try:
                result, page, unverified_result = await method_func(page, context, unverified_result)
                if result:
                    method_used = method_name
                    break  # Exit early once we found a result
            except Exception as e:
                print(f"Error in {method_name} method: {e}")
                continue
        
        # If no result found, try a very limited set of common paths
        if not result and not unverified_result:
            # Ensure these variables are defined in this scope
            parsed_url = urlparse(url)
            common_paths = [
                '/privacy', '/privacy-policy', '/privacy-notice', 
                '/legal/privacy'
            ]
            
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            for path in common_paths[:2]:  # Only try first 2 paths for speed
                common_url = base_url + path
                print(f"Trying common path: {common_url}")
                try:
                    success, response, patterns = await navigate_with_retry(page, common_url, max_retries=1)
                    if success and response.ok:
                        unverified_result = common_url
                        break
                except Exception as e:
                    print(f"Error trying common path {common_url}: {e}")
                    continue
        
        return create_response(url, result, unverified_result, method_used)
        
    except Exception as e:
        print(f"Error during browser automation: {e}")
        return handle_error(url, unverified_result, str(e))
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
        
        # Launch browser with optimized settings
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--disable-infobars',
                '--window-size=3840,2160',  # 4K resolution for maximum size
                '--disable-extensions',
                '--disable-audio',  # Disable audio for faster loading
                '--disable-features=site-per-process',  # For memory optimization
                '--js-flags=--lite-mode',  # Optimize JS execution
            ],
            chromium_sandbox=False,
            slow_mo=50  # Reduced delay
        )
        
        # Create context with optimized settings
        context = await browser.new_context(
            viewport={'width': 3840, 'height': 2160},  # 4K resolution viewport
            user_agent=user_agent,
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
            }
        )
        
        # Add minimal stealth script
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        # Create a page
        page = await context.new_page()
        
        # Random delay function with shorter times
        async def random_delay(min_ms=200, max_ms=1000):
            delay = random.randint(min_ms, max_ms)
            await page.wait_for_timeout(delay)
            
        # Set reasonable timeouts
        page.set_default_timeout(30000)  # Reduced from 60s to 30s
        
        return browser, context, page, random_delay

    except Exception as e:
        if 'playwright' in locals():
            await playwright.stop()
        print(f"Error setting up browser: {e}")
        raise

async def navigate_with_retry(page, url, max_retries=2):
    """Navigate to URL with optimized retry logic."""
    for attempt in range(max_retries):
        try:
            # Add shorter random delay between attempts
            if attempt > 0:
                delay = random.randint(1000, 2000)  # Reduced delay
                print(f"Waiting {delay/1000}s before retry {attempt+1}...")
                await page.wait_for_timeout(delay)
            
            print(f"Navigation attempt {attempt+1}/{max_retries} to {url}")
            
            # Optimized navigation strategy
            response = await page.goto(url, timeout=10000, wait_until="domcontentloaded")
            
            # Quick check for anti-bot measures
            is_anti_bot, patterns = await detect_anti_bot_patterns(page)
            if is_anti_bot:
                if attempt < max_retries - 1:
                    print(f"Detected anti-bot protection, trying alternative approach...")
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
        anti_bot_patterns = await page.evaluate("""() => {
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
    }""")
    
        if anti_bot_patterns['isAntiBot']:
            print(f"\n⚠️ Detected anti-bot protection: recaptcha")
            print(f"  URL: {anti_bot_patterns['url']}")
            print(f"  Title: {anti_bot_patterns['title']}")
            return True, ['bot_protection']
        
        return False, []
    except Exception as e:
        print(f"Error detecting anti-bot patterns: {e}")
        return False, []

async def find_all_links_js(page, context, unverified_result=None):
    """Optimized JavaScript-based link finder."""
    print("\n=== Starting find_all_links_js ===")
    print("Searching for all links using JavaScript...")
    
    try:
        # Shorter wait for page loading
        await page.wait_for_timeout(2000)  # Reduced from 5000
        
        # Get the base domain for validation
        base_domain = await page.evaluate("""() => {
            try {
                return new URL(window.location.href).hostname;
            } catch (e) {
                return '';
            }
        }""")
        
        print(f"Base domain: {base_domain}")
        
        # Optimized link detection script - faster and more targeted
        links = await page.evaluate("""(baseDomain) => {
            // Simplified priorities
            const termMatches = [
                {term: 'privacy policy', score: 100},
                {term: 'privacy notice', score: 95},
                {term: 'privacy statement', score: 90},
                {term: 'privacy', score: 80},
                {term: 'data policy', score: 80},
                {term: 'data protection', score: 75},
                {term: 'cookie policy', score: 70},
                {term: 'personal information', score: 65}
            ];
            
            // Helper function to check domain
            function isSameDomain(url, baseDomain) {
                try {
                    const urlDomain = new URL(url).hostname;
                    return urlDomain === baseDomain || 
                           urlDomain.endsWith('.' + baseDomain) || 
                           baseDomain.endsWith('.' + urlDomain);
                } catch (e) {
                    return false;
                }
            }
            
            // Get all links, but limit to 100 for performance
            const allLinks = Array.from(document.querySelectorAll('a[href]')).slice(0, 100);
            
            // Process links with faster evaluation
            const scoredLinks = allLinks
                .filter(link => {
                    const href = link.href;
                    return href && 
                           !href.startsWith('javascript:') && 
                           !href.startsWith('mailto:') &&
                           !href.startsWith('tel:') &&
                           href !== '#' &&
                           !href.startsWith('#') &&
                           isSameDomain(href, baseDomain);
                })
                .map(link => {
                    // Get link text
                    const text = link.textContent.trim().toLowerCase();
                let score = 0;
                    
                    // Score based on text match
                    for (const {term, score: matchScore} of termMatches) {
                        if (text === term) {
                            score += matchScore;
                            break;
                        } else if (text.includes(term)) {
                            score += matchScore * 0.7;
                            break;
                        }
                    }
                    
                    // URL scoring
                    const href = link.href.toLowerCase();
                    if (href.includes('/privacy-policy')) score += 50;
                    else if (href.includes('/privacy')) score += 45;
                    else if (href.includes('/data-policy')) score += 40;
                    else if (href.includes('/data-protection')) score += 40;
                    else if (href.includes('/legal')) score += 30;
                    
                    // Is in footer bonus
                    const inFooter = Boolean(
                        link.closest('footer') || 
                        link.closest('[class*="footer"]') ||
                        link.closest('#footer')
                    );
                    if (inFooter) score += 20;
                
                return {
                    href: link.href,
                    text: text,
                        score: score
                    };
                })
                .filter(link => link.score > 0)
                .sort((a, b) => b.score - a.score);
            
            return scoredLinks;
        }""", base_domain)

        # No links found
        if not links:
            print("No relevant links found using JavaScript method")
            return None, page, unverified_result
        
        # Process and print found links (limited output)
        print(f"\n🔎 Found {len(links)} potential privacy links")
            
        # If links were found, use the highest scored one
        if links:
            best_link = links[0]['href']
            
            # Set unverified result if none exists
            if not unverified_result:
                unverified_result = best_link
            
            print(f"\n🏆 Selected best link: {best_link}")
            
            # Attempt to navigate to best link and verify it's actually a privacy page
            try:
                print(f"Navigating to selected link: {best_link}")
                await page.goto(best_link, timeout=10000, wait_until="domcontentloaded")
                
                # Verify this is actually a privacy page
                is_privacy_page = await page.evaluate("""() => {
                    const text = document.body.innerText.toLowerCase();
                    const strongTermMatchers = [
                        'privacy policy', 
                        'privacy notice', 
                        'privacy statement',
                        'personal information', 
                        'information we collect',
                        'data we collect',
                        'how we use your information',
                        'cookie policy',
                        'data protection'
                    ];
                    
                    return strongTermMatchers.some(term => text.includes(term));
                            }""")
                            
                if is_privacy_page:
                    print("✅ Confirmed this is a privacy page")
                    return best_link, page, unverified_result
                else:
                    print("❌ Page doesn't look like a privacy page, keeping as fallback")
                    return None, page, unverified_result
            except Exception as e:
                print(f"Error navigating to privacy page: {e}")
                return None, page, unverified_result
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_all_links_js: {e}")
        return None, page, unverified_result

async def find_matching_link(page, context, unverified_result=None):
    """Find and click on privacy-related links with optimized performance."""
    try:
        # Use a more targeted selector for performance
        links = await page.query_selector_all('footer a, .footer a, #footer a, a[href*="privacy"], a[href*="data-policy"], a[href*="legal"]')
        
        for link in links:
            try:
                text = await link.text_content()
                if not text:
                    continue
                text = text.lower().strip()
                href = await link.get_attribute('href')
                if not href:
                    continue
                
                # Simplified scoring for speed
                score = 0
                if 'privacy policy' in text or 'privacy notice' in text:
                    score = 100
                elif 'privacy' in text:
                    score = 80
                elif 'data protection' in text:
                    score = 70
                elif 'personal information' in text:
                    score = 60
                elif 'data policy' in text:
                    score = 50
                
                # Additional URL scoring
                if '/privacy-policy' in href or '/privacy_policy' in href:
                    score += 50
                elif '/privacy' in href:
                    score += 40
                elif '/data-policy' in href or '/data-protection' in href:
                    score += 40
                elif '/legal' in href:
                    score += 30
                
                if score > 50:  # High confidence match
                    print(f"Found high confidence link: {text} ({score})")
                    success = await click_and_wait_for_navigation(page, link, timeout=5000)
                    if success:
                        return page.url, page, unverified_result
            except Exception as e:
                continue
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_link: {e}")
        return None, page, unverified_result

async def click_and_wait_for_navigation(page, element, timeout=5000):
    """Click a link and wait for navigation with shorter timeout."""
    try:
        async with page.expect_navigation(timeout=timeout, wait_until="domcontentloaded"):
            await element.click()
        return True
    except Exception as e:
        print(f"Navigation error: {e}")
        return False

async def smooth_scroll_and_click(page, context, unverified_result=None, step=200, delay=100):
    """Optimized version of smooth scroll with faster execution time."""
    print("🔃 Starting smooth scroll with strong term matching...")
    visited_url = None
    current_page = page

    # First check visible links before scrolling
    visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
    if visited_url:
        return visited_url, current_page, unverified_result

    try:
        # Simplified footer selectors
        footer_selectors = ["footer", ".footer", "#footer"]

        # Get page height more efficiently
        page_height = await current_page.evaluate("""() => document.documentElement.scrollHeight""")

        # Use fewer positions to check
        positions_to_check = [
            page_height,
            page_height * 0.5,
        ]

        for scroll_pos in positions_to_check:
            await current_page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            await current_page.wait_for_timeout(300)

            visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
            if visited_url:
                return visited_url, current_page, unverified_result

        # Check footer area with simplified approach
        for selector in footer_selectors:
            footer = await current_page.query_selector(selector)
            if footer:
                print(f"Found footer with selector: {selector}")
                await footer.scroll_into_view_if_needed()

                # Check for privacy links with faster query
                privacy_links = await current_page.evaluate("""(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    const links = Array.from(footer.querySelectorAll('a'));
                    return links.filter(link => {
                        const text = link.textContent.toLowerCase();
                        const href = link.href.toLowerCase();
                        return text.includes('privacy') || 
                               text.includes('personal data') || 
                               href.includes('privacy') || 
                               href.includes('data-policy');
                    }).map(link => ({
                    text: link.textContent.trim(),
                        href: link.href
                    }));
                }""", selector)

                if privacy_links and len(privacy_links) > 0:
                    print(f"Found {len(privacy_links)} potential privacy links in footer")
                    # Limit to first 3 links for speed
                    for link in privacy_links[:3]:
                        try:
                            element = await current_page.query_selector(f"a[href='{link['href']}']")
                            if element:
                                success = await click_and_wait_for_navigation(current_page, element, timeout=5000)
                                if success:
                                    return current_page.url, current_page, unverified_result
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")
                    
        # Skip scrolling back up to save time
        print("✅ Reached the bottom of the page.")

    except Exception as e:
        print(f"Error in footer/scroll check: {e}")

    return None, current_page, unverified_result

async def analyze_landing_page(page, context, unverified_result=None):
    """
    Analyze landing page content to detect mentions of privacy policy.
    Sometimes pages mention privacy in the content but don't have direct links.
    """
    print("\n=== Starting landing page analysis ===")
    
    try:
        # Look for text patterns that might indicate privacy policy info
        privacy_mentions = await page.evaluate("""() => {
            // Get page text
            const pageText = document.body.innerText.toLowerCase();
            
            // Look for privacy-related phrases
            const privacyPhrases = [
                'privacy policy',
                'privacy notice',
                'privacy statement',
                'data policy',
                'data protection',
                'information we collect',
                'personal information',
                'cookie policy'
            ];
            
            const mentions = [];
            let context = '';
            
            // Find mentions of privacy in text
            for (const phrase of privacyPhrases) {
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
        }""")
        
        if privacy_mentions and len(privacy_mentions) > 0:
            print(f"Found {len(privacy_mentions)} privacy mentions in content")
            
            # Look for URLs in the context of these mentions
            for mention in privacy_mentions:
                print(f"Privacy mention: '{mention['phrase']}' in context: '{mention['context']}'")
                
                # Try to find nearby links
                nearby_links = await page.evaluate("""(searchPhrase) => {
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
                }""", mention['phrase'])
                
                if nearby_links and len(nearby_links) > 0:
                    print(f"Found {len(nearby_links)} links near the privacy mention")
                    
                    # Score and sort these links
                    scored_links = []
                    for link in nearby_links:
                        score = 0
                        text = link['text'].lower() if link['text'] else ''
                        href = link['href'].lower()
                        
                        # Score based on text
                        if 'privacy' in text:
                            score += 40
                        if 'policy' in text:
                            score += 20
                        if 'statement' in text:
                            score += 15
                        if 'notice' in text:
                            score += 15
                        
                        # Score based on URL
                        if 'privacy' in href:
                            score += 30
                        if 'data-policy' in href:
                            score += 25
                        if 'legal' in href:
                            score += 10
                        
                        scored_links.append((link['href'], score, link['text']))
                    
                    # Sort by score
                    scored_links.sort(key=lambda x: x[1], reverse=True)
                    
                    if scored_links and scored_links[0][1] >= 40:  # Good confidence threshold
                        best_link = scored_links[0][0]
                        print(f"Best link from context: {best_link} (score: {scored_links[0][1]}, text: '{scored_links[0][2]}')")
                        
                        # Try to navigate to verify
                        try:
                            await page.goto(best_link, timeout=10000, wait_until="domcontentloaded")
                            is_privacy_page = await page.evaluate("""() => {
                                const text = document.body.innerText.toLowerCase();
                                const strongPrivacyMatchers = [
                                    'privacy policy', 
                                    'privacy notice', 
                                    'privacy statement',
                                    'personal information', 
                                    'information we collect',
                                    'data we collect',
                                    'how we use your information',
                                    'cookie policy',
                                    'data protection'
                                ];
                                
                                return strongPrivacyMatchers.some(term => text.includes(term));
                            }""")
                            
                            if is_privacy_page:
                                print(f"✅ Verified as privacy policy page: {page.url}")
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

def handle_navigation_failure(url: str, unverified_result: Optional[str]) -> PrivacyResponse:
    """Handle case where navigation to the URL failed."""
    parsed_url = urlparse(url)
    
    if unverified_result:
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found potential privacy policy link (unverified)",
            method_used="dynamic_detection_unverified"
        )
        
    # Return a simple common path response
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    likely_pp_url = f"{base_url}/privacy"
    
    return PrivacyResponse(
        url=url,
        pp_url=likely_pp_url,
        success=True,
        message="Using common privacy path (unverified)",
        method_used="common_path_fallback"
    )

def handle_error(url: str, unverified_result: Optional[str], error: str) -> PrivacyResponse:
    """Simplified error handler."""
    parsed_url = urlparse(url)
    
    if unverified_result:
        # Additional validation to filter out registration/login pages and other false positives
        unverified_lower = unverified_result.lower()
        
        # Check for common patterns that indicate NOT a privacy page
        suspicious_patterns = [
            '/register', '/signup', '/login', '/signin', '/registration',
            '/create-account', '/join', '/auth/', '/openid', '/ap/register',
            'registration', 'signup', 'create_account', 'createaccount',
            'authentication', 'returnto', 'redirect', 'callback'
        ]
        
        # Check if the URL contains suspicious patterns
        if any(pattern in unverified_lower for pattern in suspicious_patterns):
            return PrivacyResponse(
                url=url,
                pp_url=None,
                success=False,
                message=f"Potential privacy link was rejected as it appears to be a registration/login page",
                method_used="validation_failed"
            )
        
        # Continue with original logic for validated URLs
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found potential privacy policy link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    
    # Simple default response
    return PrivacyResponse(
        url=url,
        pp_url=None,
        success=False,
        message=f"Error during browser automation: {error}",
        method_used="none"
    )

def create_response(url: str, result: Optional[str], unverified_result: Optional[str], method_used: str) -> PrivacyResponse:
    """Create appropriate response based on results."""
    if result:
        return PrivacyResponse(
            url=url,
            pp_url=result,
            success=True,
            message=f"Found Privacy Policy page using {method_used} method",
            method_used=method_used
        )
    elif unverified_result:
        # Apply the same validation as in handle_error
        unverified_lower = unverified_result.lower()
        
        # Check for common patterns that indicate NOT a privacy page
        suspicious_patterns = [
            '/register', '/signup', '/login', '/signin', '/registration',
            '/create-account', '/join', '/auth/', '/openid', '/ap/register',
            'registration', 'signup', 'create_account', 'createaccount',
            'authentication', 'returnto', 'redirect', 'callback'
        ]
        
        # Check if the URL contains suspicious patterns
        if any(pattern in unverified_lower for pattern in suspicious_patterns):
            return PrivacyResponse(
                url=url,
                pp_url=None,
                success=False,
                message="Potential privacy link was rejected as it appears to be a registration/login page",
                method_used="validation_failed"
            )
            
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found potential privacy policy link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    else:
        return PrivacyResponse(
            url=url,
            pp_url=None,
            success=False,
            message="Could not find Privacy Policy page",
            method_used="none"
        )

async def main():
    """Main function for direct script usage."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Find Privacy Policy page for a given URL.')
    parser.add_argument('url', help='URL to scan for Privacy Policy')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    
    args = parser.parse_args()
    
    if args.verbose:
        print(f"Searching for Privacy Policy for: {args.url}")
    
    try:
        request = PrivacyRequest(url=args.url)
        response = await find_privacy(request)
        
        print("\n=== Results ===")
        print(f"URL: {response.url}")
        print(f"Privacy Policy URL: {response.pp_url if response.pp_url else 'Not found'}")
        print(f"Success: {response.success}")
        print(f"Method: {response.method_used}")
        print(f"Message: {response.message}")
        
        return response.pp_url
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

# Run the script
if __name__ == "__main__":
    import asyncio
    result = asyncio.run(main())
    sys.exit(0 if result else 1)

# Add missing utility functions below main()

async def try_fallback_paths(url, page):
    """Try common fallback paths when direct navigation fails."""
    print("Trying fallback paths...")
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    # Use dynamic discovery to generate potential paths
    potential_urls = await parse_domain_for_common_paths(domain, url)
    
    # Try each potential URL
    for potential_url in potential_urls[:10]:  # Limit to first 10 to avoid too many requests
        try:
            print(f"Trying fallback path: {potential_url}")
            response = await page.goto(potential_url, timeout=5000, wait_until="domcontentloaded")
            
            if response and response.ok:
                print(f"✅ Successfully accessed fallback path: {potential_url}")
                
                # Verify this is actually a privacy page
                is_privacy = await verify_privacy_content(page)
                if is_privacy:
                    print(f"✅ Verified as privacy page: {potential_url}")
                    return potential_url
                else:
                    print(f"❌ Not a privacy page: {potential_url}")
        except Exception as e:
            print(f"❌ Error accessing fallback path {potential_url}: {e}")
            continue
    
    # If all else fails, return most likely path as unverified
    if potential_urls:
        return potential_urls[0]
    
    return None
            
async def verify_privacy_content(page):
    """
    Verify if the current page appears to be a privacy policy page.
    
    Args:
        page: Playwright page object
        
    Returns:
        Boolean indicating if the page appears to be a privacy page
    """
    try:
        # Get the page title
        title = await page.title()
        title_lower = title.lower()
        
        # Check title for privacy indicators
        if any(term in title_lower for term in ['privacy', 'data policy', 'data protection', 'cookie policy']):
            print(f"Page title indicates privacy: {title}")
            return True
            
        # Check content for privacy indicators using JavaScript
        has_privacy_content = await page.evaluate("""
        () => {
            try {
                const bodyText = document.body.innerText.toLowerCase();
                
                // Check for common privacy phrases
                const privacyPhrases = [
                    'privacy policy',
                    'privacy notice',
                    'privacy statement',
                    'data policy',
                    'data protection policy',
                    'personal information',
                    'information we collect',
                    'cookie policy'
                ];
                
                // Check for privacy content sections
                const privacySections = [
                    'personal data',
                    'information collection',
                    'data processing',
                    'third parties',
                    'cookies and tracking',
                    'your rights',
                    'gdpr',
                    'opt out',
                    'data security'
                ];
                
                // Count matches
                const privacyMatches = privacyPhrases.filter(phrase => bodyText.includes(phrase)).length;
                const sectionMatches = privacySections.filter(section => bodyText.includes(section)).length;
                
                // Return true if enough matches are found
                return privacyMatches >= 2 || sectionMatches >= 3 || (privacyMatches + sectionMatches >= 3);
            } catch (e) {
                return false;
            }
        }
        """)
        
        if has_privacy_content:
            print("Page content indicates privacy page")
            return True
            
        return False
    except Exception as e:
        print(f"Error verifying privacy content: {e}")
        return False

async def parse_domain_for_common_paths(domain, url):
    """Generate potential paths based on domain patterns for privacy pages."""
    
    # Base common paths for any website
    common_paths = [
        '/privacy',
        '/privacy-policy',
        '/privacy-notice',
        '/privacy-statement',
        '/data-policy',
        '/data-protection',
        '/legal/privacy',
        '/legal/data-protection',
        '/about/privacy',
        '/about/legal/privacy',
        '/help/privacy',
        '/policies/privacy',
        '/corporate/privacy',
        '/info/privacy',
        '/site/privacy',
        '/privacy.html',
        '/privacy-policy.html',
        '/legal.html'
    ]
    
    # Extract domain parts
    domain_parts = domain.split('.')
    base_name = domain_parts[0] if len(domain_parts) > 1 else domain
    
    # Add domain-specific patterns
    domain_specific = [
        f'/{base_name}/privacy',
        f'/{base_name}/legal',
        f'/about/{base_name}/privacy',
        f'/legal/{base_name}/privacy',
    ]
    
    # Combine all paths
    all_paths = common_paths + domain_specific
    
    # Build full URLs
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    return [f"{base_url}{path}" for path in all_paths]

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
        '/register', '/signup', '/login', '/signin', '/registration',
        '/create-account', '/join', '/auth/', '/openid', '/ap/register',
        'registration', 'signup', 'create_account', 'createaccount',
        'authentication', 'returnto', 'redirect', 'callback'
    ]
    
    # Check if URL contains any suspicious patterns
    return any(pattern in url_lower for pattern in suspicious_patterns)

async def bs4_fallback_link_finder(page, context):
    """Use robust HTML parsing as a fallback method to find privacy links."""
    print("Using robust HTML parsing to find privacy links...")

    try:
        # Get the page HTML
        html_content = await page.content()

        # Check for Cloudflare challenge page
        is_cloudflare_challenge = await page.evaluate("""(html) => {
            return (html.includes('cloudflare') && 
                    (html.includes('challenge') || 
                     html.includes('security check') || 
                     html.includes('captcha') || 
                     html.includes('verify your browser')));
        }""", html_content)

        if is_cloudflare_challenge:
            print("Detected Cloudflare challenge page, cannot extract reliable links")
            return None, page

        # Use robust regex-based parsing to find links
        privacy_links = await page.evaluate("""(html) => {
            function extractLinks(html) {
                const links = [];
                const anchorRegex = /<a\\s+([^>]*)>(.*?)<\\/a>/gi;
                let match;

                while ((match = anchorRegex.exec(html)) !== null) {
                    const attributes = match[1];
                    const linkText = match[2].replace(/<[^>]*>/g, '').trim();
                    const hrefMatch = attributes.match(/href=["']([^"']*)["']/i);
                    const href = hrefMatch ? hrefMatch[1] : '';
                    const classMatch = attributes.match(/class=["']([^"']*)["']/i);
                    const idMatch = attributes.match(/id=["']([^"']*)["']/i);
                    const dataAttributes = {};

                    const dataAttrMatches = attributes.matchAll(/data-([\\w-]+)=["']([^"']*)["']/gi);
                    for (const dataMatch of dataAttrMatches) {
                        dataAttributes[dataMatch[1]] = dataMatch[2];
                    }

                    links.push({
                        text: linkText,
                        href: href,
                        className: classMatch ? classMatch[1] : '',
                        id: idMatch ? idMatch[1] : '',
                        dataAttributes: dataAttributes,
                        rawAttributes: attributes
                    });
                }

                return links;
            }

            const allLinks = extractLinks(html);

            const privacyLinks = allLinks.filter(link => {
                const href = link.href.toLowerCase();
                const text = link.text.toLowerCase();

                const textIndicators = [
                    'privacy policy', 'privacy notice', 'privacy statement',
                    'data policy', 'data protection', 'privacy', 
                    'personal information', 'cookie policy', 'your data'
                ];

                const hrefIndicators = [
                    '/privacy', '/privacy-policy', '/privacy-notice', '/privacy-statement',
                    '/data-policy', '/data-protection', '/personal-information',
                    '/legal/privacy', '/cookie-policy', '/cookies'
                ];

                const hasExactTextMatch = textIndicators.some(indicator =>
                    text === indicator || text.replace(/\\s+/g, '') === indicator.replace(/\\s+/g, '')
                );

                const hasPrivacyInText = text.includes('privacy') || text.includes('data protection') ||
                                       text.includes('personal') || text.includes('cookie');

                const hasPrivacyInHref = hrefIndicators.some(indicator => href.includes(indicator)) ||
                                       href.includes('privacy') || href.includes('data-policy') ||
                                       href.includes('data-protection');

                let score = 0;
                if (hasExactTextMatch) score += 100;
                if (hasPrivacyInText) score += 50;
                if (hasPrivacyInHref) score += 75;

                link.score = score;
                return score > 0;
            });

            privacyLinks.sort((a, b) => b.score - a.score);
            return privacyLinks;
        }""", html_content)

        print(f"Found {len(privacy_links)} potential privacy links with robust HTML parsing")

        for link in privacy_links:
            score_display = link['score'] if 'score' in link else 0
            print(f"Link found: '{link['text']}' → {link['href']} [Score: {score_display}]")

            try:
                href = link['href']
                if href and not href.startswith('http'):
                    if href.startswith('/'):
                        base_url = '/'.join(page.url.split('/')[:3])
                        href = base_url + href
                    else:
                        base_url = page.url.split('?')[0].split('#')[0]
                        if base_url.endswith('/'):
                            href = base_url + href
                        else:
                            href = base_url + '/' + href

                if not href or href.startswith('javascript:'):
                    continue

                print(f"Navigating directly to: {href}")
                try:
                    await page.goto(href, timeout=3000)
                    await page.wait_for_load_state('domcontentloaded')

                    privacy_content = await page.evaluate("""() => {
                        const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
                        const paragraphs = Array.from(document.querySelectorAll('p'));

                        const privacyHeading = headings.some(h => {
                            const text = h.textContent.toLowerCase();
                            return text.includes('privacy') ||
                                   text.includes('data policy') ||
                                   text.includes('personal information') ||
                                   text.includes('cookie policy');
                        });

                        const title = document.title.toLowerCase();
                        const privacyInTitle = title.includes('privacy') ||
                                             title.includes('data') ||
                                             title.includes('personal information');

                        const privacyContent = paragraphs.slice(0, 5).some(p => {
                            const text = p.textContent.toLowerCase();
                            return text.includes('collect') ||
                                   text.includes('personal') ||
                                   text.includes('information') ||
                                   text.includes('data') ||
                                   text.includes('cookie') ||
                                   text.includes('privacy') ||
                                   text.includes('gdpr');
                        });

                        return privacyHeading || privacyInTitle || privacyContent;
                    }""")

                    if privacy_content:
                        print(f"Found privacy content at: {page.url}")
                        return page.url, page
                    else:
                        print("Page doesn't appear to contain privacy content")

                except Exception as e:
                    print(f"Navigation error: {e}")

            except Exception as e:
                print(f"Error processing link: {e}")

        return None, page

    except Exception as e:
        print(f"Error in bs4_fallback_link_finder: {e}")
        return None, page

async def standard_privacy_finder(url: str, headers: dict = None) -> tuple[Optional[str], None]:
    """
    Advanced dynamic approach to find Privacy Policy links without hardcoded patterns.
    Uses site structure analysis, content evaluation, and semantic understanding to
    discover privacy pages regardless of site architecture.

    Args:
        url: The URL to scan
        headers: Optional request headers

    Returns:
        Tuple of (privacy_url, None) or (None, None) if not found
    """
    try:
        print("Using fully dynamic privacy discovery algorithm...")

        # Create a session to maintain cookies across requests
        session = requests.Session()

        # Store best candidate for fallback
        best_candidate = None
        best_score = 0

        # Default headers if none provided
        if not headers:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            }

        # Parse the target URL
        parsed = urlparse(url)
        base_domain = f"{parsed.scheme}://{parsed.netloc}"

        print(f"Analyzing site structure at {base_domain}...")

        # Get the main page content
        response = session.get(base_domain, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Failed to access {base_domain}: Status {response.status_code}")
            return None, None

        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')

        # Look for footers (where privacy policies are commonly found)
        footer_sections = soup.select(
            'footer, .footer, #footer, [class*="footer"], [id*="footer"], .legal, .bottom, [class*="bottom"]'
        )

        # Extract links from footers
        candidates = []
        for footer in footer_sections:
            for link in footer.find_all('a', href=True):
                href = link.get('href')
                text = link.get_text().strip().lower()

                if not href or href.startswith(('javascript:', 'mailto:', 'tel:')):
                    continue

                # Create absolute URL
                abs_url = href if href.startswith(('http://', 'https://')) else urljoin(base_domain, href)
                
                # Skip registration/login pages
                if is_likely_registration_or_login_page(abs_url):
                    print(f"Skipping likely registration/login page: {abs_url}")
                    continue

                # Score the link
                score = 0
                signals = []

                # Text analysis
                if 'privacy' in text:
                    score += 30
                    signals.append('privacy_in_text')
                if 'policy' in text and 'privacy' in text:
                    score += 20
                    signals.append('policy_in_text')
                if 'data protection' in text:
                    score += 15
                    signals.append('data_protection_in_text')
                if 'personal' in text:
                    score += 10
                    signals.append('personal_in_text')

                # URL analysis
                if 'privacy' in abs_url.lower():
                    score += 20
                    signals.append('privacy_in_url')
                if 'data-policy' in abs_url.lower():
                    score += 15
                    signals.append('data_policy_in_url')
                if 'legal' in abs_url.lower():
                    score += 10
                    signals.append('legal_in_url')

                # Add to candidates if score is above threshold
                if score >= 20:
                    candidates.append({
                        'url': abs_url,
                        'text': text,
                        'score': score,
                        'signals': signals
                    })

        # Sort by score
        candidates.sort(key=lambda x: x['score'], reverse=True)

        # Get the best candidate
        if candidates:
            best_candidate = candidates[0]['url']
            best_score = candidates[0]['score']
            return best_candidate, None

        return None, None

    except Exception as e:
        print(f"Error in standard_privacy_finder: {str(e)}")
        return None, None

async def check_for_better_privacy_link(page, current_url):
    """Check if current page has links to more specific privacy pages.
    
    Args:
        page: Playwright page object
        current_url: URL of current page
        
    Returns:
        Tuple of (better_url, new_page) or (None, current_page)
    """
    try:
        # Extract base domain for validation
        parsed_url = urlparse(current_url)
        base_domain = parsed_url.netloc
        
        # Get all links and score them using our dynamic approach
        links = await page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href]'));
            return links.map(link => ({
                text: link.textContent.toLowerCase().trim(),
                href: link.href
            }))
            .filter(link => 
                link.href && 
                !link.href.startsWith('javascript:') && 
                !link.href.startsWith('mailto:') &&
                link.href !== window.location.href
            );
        }""")
        
        scored_links = []
        for link in links:
            if not link['href']:
                continue
        
            # Skip links to external domains
            link_domain = urlparse(link['href']).netloc
            if not (link_domain == base_domain or 
                    link_domain.endswith('.' + base_domain) or 
                    base_domain.endswith('.' + link_domain)):
                continue
            
            # Skip registration/login pages
            if is_likely_registration_or_login_page(link['href']):
                print(f"Skipping likely registration/login page: {link['href']}")
                continue
                
            # Dynamic scoring based on text and URL patterns
            score = 0
            text = link['text'].lower()
            url = link['href'].lower()
            
            # Score based on text indicators
            for term, priority in exactMatchPriorities.items():
                if term in text:
                    score += priority
                    break
                    
            # Check URL patterns
            if 'privacy-policy' in url or 'privacy_policy' in url:
                score += 40
            elif 'privacy' in url:
                score += 35
            elif 'data-protection' in url or 'data_protection' in url:
                score += 30
            elif 'personal-information' in url:
                score += 25
                
            # Check for context indicators suggesting more detailed content
            context_words = ['full', 'detailed', 'complete', 'latest', 'updated', 'specific']
            for word in context_words:
                if word in text:
                    score += 10
                    break
                    
            # PDF and legal documents often contain the full privacy policy
            if url.endswith('.pdf'):
                score += 15
                
            scored_links.append((link['href'], score))
            
        # Sort by score and check top links
        scored_links.sort(key=lambda x: x[1], reverse=True)
        
        # Only check high-confidence links
        for link_url, score in scored_links:
            if score < 50:  # Only use high-confidence matches
                continue
            
            print(f"Found potential deeper privacy link with score {score}: {link_url}")
            
            try:
                # Navigate to the link
                await page.goto(link_url, timeout=10000, wait_until="domcontentloaded")
                
                # Verify if this is actually a privacy page
                is_privacy_page = await page.evaluate("""() => {
                    const text = document.body.innerText.toLowerCase();
                    const title = document.title.toLowerCase();
                    
                    // Key indicators of privacy content
                    const privacyIndicators = [
                        'privacy policy', 
                        'privacy notice', 
                        'privacy statement',
                        'information we collect',
                        'personal information',
                        'data we collect',
                        'third parties',
                        'cookies',
                        'gdpr',
                        'data protection'
                    ];
                    
                    const hasPrivacyIndicators = privacyIndicators.some(term => text.includes(term));
                    const hasPrivacyInTitle = title.includes('privacy') || 
                                           title.includes('data policy') || 
                                           title.includes('personal information');
                    
                    // Simple content length check - privacy pages tend to be long
                    const isLongContent = text.length > 3000;
                    
                    return {
                        isPrivacyPage: hasPrivacyIndicators || (hasPrivacyInTitle && isLongContent),
                        textLength: text.length,
                        hasPrivacyInTitle: hasPrivacyInTitle
                    };
                }""")
                
                if is_privacy_page['isPrivacyPage']:
                    print(f"Verified better privacy page: {page.url}")
                    return page.url, page
                else:
                    print(f"Page doesn't appear to be a valid privacy page")
                    
            except Exception as e:
                print(f"Navigation error checking link: {e}")
                continue
            
        return None, page
    except Exception as e:
        print(f"Error checking for better privacy links: {e}")
        return None, page

def is_valid_privacy_page(content):
    """Validate if page content matches privacy page criteria.
    
    Args:
        content: Dict with text and headings from page
        
    Returns:
        bool: True if valid privacy page
    """
    # Required terms that should be present
    required_terms = ['privacy', 'information', 'data', 'collect', 'use']
    
    # Privacy sections that indicate a privacy page
    privacy_sections = [
        'information we collect',
        'personal information', 
        'data protection',
        'third parties',
        'cookies',
        'your rights',
        'gdpr',
        'data sharing',
        'data security',
        'data retention'
    ]
    
    # Legal phrases that indicate privacy content
    privacy_phrases = [
        'we collect',
        'we use',
        'we share',
        'may collect',
        'privacy policy',
        'personal data',
        'personal information',
        'information about you',
        'cookies',
        'third party',
        'third parties',
        'tracking',
        'analytics',
        'advertising',
        'marketing',
        'opt out',
        'unsubscribe',
        'data subject',
        'data rights',
        'contact us'
    ]
    
    if not content or 'text' not in content:
        return False
        
    text = content.get('text', '').lower()
    headings = content.get('headings', [])
    
    # Check for a minimum content length (privacy pages tend to be long)
    if len(text) < 2000:  # Reduced from 3000 to catch more pages
        return False
        
    # Check required terms
    terms_count = sum(1 for term in required_terms if term in text)
    
    # Check privacy sections
    section_count = sum(1 for section in privacy_sections if section in text)
    
    # Check privacy phrases
    phrase_count = sum(1 for phrase in privacy_phrases if phrase in text)
    
    # Check headings
    has_privacy_heading = any(
        h for h in headings 
        if 'privacy' in h or 'data' in h or 'information' in h or 'personal' in h
    )
    
    # Calculate score based on content
    score = 0
    if terms_count >= 2:
        score += 30
    if section_count >= 2:
        score += 20
    if phrase_count >= 3:
        score += 30
    if has_privacy_heading:
        score += 20
    
    # More lenient approach - if enough privacy phrases and some required terms, it's likely a privacy page
    if score >= 70 or (phrase_count >= 5 and terms_count >= 1):
        return True
        
    # If page has substantial legal content, it's likely a privacy page
    if len(text) > 5000 and phrase_count >= 3 and terms_count >= 1:
        return True
    
    return False
