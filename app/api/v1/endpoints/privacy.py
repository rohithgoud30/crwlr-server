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
    'data protection': 80,
    'privacy': 75,
    'data privacy': 70,
    'data processing': 65,
    'cookie policy': 60
}

# Priorities for partial match terms
partialMatchPriorities = {
    'data rights': 55,
    'data collection': 50,
    'personal information': 45,
    'data usage': 40,
    'information we collect': 35,
    'privacy practices': 30,
    'privacy preferences': 25,
    'data sharing': 20,
    'gdpr': 15,
    'ccpa': 10
}

# Define strong match terms for privacy policy detection
strong_privacy_matches = [
    'privacy policy', 'privacy notice', 'privacy statement',
    'data policy', 'data protection', 'privacy',
    'data privacy', 'data processing', 'cookie policy', 
    'personal data', 'gdpr', 'ccpa'
]

# Context words for enhanced privacy policy detection
context_words = [
    'full', 'detailed', 'complete', 'latest', 'updated', 'official', 
    'data', 'personal', 'information', 'cookie', 'gdpr', 'ccpa', 
    'collection', 'analytics', 'tracking', 'third-party'
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
    Setup browser with optimized configurations and maximum viewport size.
    """
    if not playwright:
        playwright = await async_playwright().start()
    try:
        # Use random user agent
        user_agent = random.choice(USER_AGENTS)
        
        # Launch browser with optimized settings and maximum window size
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
            ],
            chromium_sandbox=False,
            slow_mo=50  # Reduced delay
        )
        
        # Create context with maximum viewport size
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

async def try_all_methods(page: Page, context: BrowserContext, unverified_result=None) -> Tuple[Optional[str], str]:
    """Try all methods to find privacy policy link."""
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
                return result, method_name
        except Exception as e:
            print(f"Error in {method_name} method: {e}")
            continue
    
    return None, ""

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
                'cookie policy',
                'data policy',
                'data collection',
                'data protection'
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
                        if 'data' in text:
                            score += 15
                        if 'cookie' in text:
                            score += 15
                        
                        # Score based on URL
                        if 'privacy' in href:
                            score += 30
                        if 'policy' in href:
                            score += 15
                        if 'data' in href:
                            score += 10
                        if 'cookie' in href:
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
                            is_privacy = await verify_privacy_content(page)
                            if is_privacy:
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
    """Handle the case where navigation to the URL failed."""
    if unverified_result:
        # Return the unverified link but mark it as such
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found a potential privacy policy URL but couldn't verify it due to navigation issues",
            method_used="unverified_guess"
        )
    else:
        # Construct a common pattern as a fallback
        parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    likely_privacy_url = f"{base_url}/privacy"  # Most common pattern
    
    return PrivacyResponse(
        url=url,
            pp_url=likely_privacy_url,
        success=True,
            message="Based on common patterns, this might be the privacy policy URL",
            method_used="common_pattern_guess"
    )

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

def handle_error(url: str, unverified_result: Optional[str], error: str) -> PrivacyResponse:
    """Handle any errors that occur during processing."""
    # Parse the URL to get domain
    parsed_url = urlparse(url)
    
    if unverified_result:
        # Additional validation to filter out registration/login pages and other false positives
        if is_likely_registration_or_login_page(unverified_result):
            return PrivacyResponse(
                url=url,
                pp_url=None,
                success=False,
                message=f"Potential privacy link was rejected as it appears to be a registration/login page",
                method_used="validation_failed"
            )
            
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,
            message="Found potential privacy link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    
    # If no unverified result found, use a common path
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    likely_privacy_url = f"{base_url}/privacy"  # Most common path
    
    # Special case handling for certain error patterns
    if "timeout" in error.lower() or "navigation" in error.lower() or "403" in error:
        return PrivacyResponse(
            url=url,
            pp_url=likely_privacy_url,
            success=True,
            message="Generated likely privacy path after timeout/navigation error (unverified)",
            method_used="timeout_fallback_path"
        )
        
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
        # Apply validation to filter out registration/login pages
        if is_likely_registration_or_login_page(unverified_result):
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
            message="Found potential privacy link (unverified)",
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

async def find_all_links_js(page, context, unverified_result=None):
    print("\n=== Starting find_all_links_js ===")
    print("Searching for all links using JavaScript...")
    
    try:
        # Wait longer for modern SPAs to load
        await page.wait_for_timeout(5000)
        
        # Get the base domain for validation
        base_domain = await page.evaluate("""() => {
            try {
                return new URL(window.location.href).hostname;
            } catch (e) {
                return '';
            }
        }""")
        
        print(f"Base domain: {base_domain}")
        
        # Check for security challenge pages
        is_challenge = await page.evaluate("""() => {
            const html = document.documentElement.innerHTML.toLowerCase();
            return html.includes('challenge') || 
                html.includes('security check') || 
                html.includes('captcha') || 
                html.includes('verify your browser') ||
                html.includes('access denied') ||
                html.includes('403 forbidden');
        }""")
    
        if is_challenge:
            print("⚠️ Detected security challenge page, cannot extract reliable links")
            
            # Try parsing domain for common paths as fallback
            parsed_url = urlparse(page.url)
            domain = parsed_url.netloc
            potential_urls = await parse_domain_for_common_privacy_paths(domain, page.url)
            if potential_urls:
                print(f"Using fallback common paths since challenge page detected")
                # Use first potential URL as unverified result if none exists
                if not unverified_result and potential_urls:
                    unverified_result = potential_urls[0]
                    print(f"Setting unverified result to: {unverified_result}")
            
            return None, page, unverified_result
            
        # Enhanced link detection with modern web patterns
        links = await page.evaluate("""(baseDomain) => {
            // Strong indicators of privacy policy links
            const exactMatchPriorities = {
                'privacy policy': 100,
                'privacy notice': 95,
                'privacy statement': 90,
                'data policy': 85,
                'data protection': 80,
                'privacy': 75,
                'data privacy': 70,
                'data processing': 65,
                'cookie policy': 60
            };
            
            // Partial match priorities
            const partialMatchPriorities = {
                'data rights': 55,
                'cookie settings': 50,
                'data collection': 45,
                'personal information': 40,
                'data usage': 35,
                'information we collect': 30,
                'privacy practices': 25,
                'privacy preferences': 20,
                'data sharing': 15,
                'gdpr': 10,
                'ccpa': 5
            };
            
            // Helper function to check if a URL is from the same domain
            function isSameDomainOrSubdomain(url, baseDomain) {
                try {
                    const urlDomain = new URL(url).hostname;
                    return urlDomain === baseDomain || 
                           urlDomain.endsWith('.' + baseDomain) || 
                           baseDomain.endsWith('.' + urlDomain);
                } catch (e) {
                    return false;
                }
            }
            
            // Function to score link relevance
            function scoreLink(link, text) {
                const lowercaseText = text.toLowerCase().trim();
                const lowercaseHref = link.href.toLowerCase();
                let score = 0;
                const textSignals = [];
                const contextSignals = [];
                
                // Check exact matches (highest priority)
                for (const [term, priority] of Object.entries(exactMatchPriorities)) {
                    if (lowercaseText === term || 
                        lowercaseText.replace(/\\s+/g, '') === term.replace(/\\s+/g, '')) {
                        score += priority;
                        textSignals.push(`exact_match:${term}`);
                        break; // Only count highest priority exact match
                    }
                }
                
                // If no exact match, check partial matches
                if (score === 0) {
                    for (const [term, priority] of Object.entries(exactMatchPriorities)) {
                        if (lowercaseText.includes(term)) {
                            score += priority * 0.7; // 70% of the full score
                            textSignals.push(`contains:${term}`);
                            break; // Only count highest priority partial match
                        }
                    }
                }
                
                // Check partial match terms
                for (const [term, priority] of Object.entries(partialMatchPriorities)) {
                    if (lowercaseText.includes(term)) {
                        score += priority * 0.8; // 80% of the priority
                        textSignals.push(`partial:${term}`);
                    }
                }
                
                // URL structure analysis
                if (lowercaseHref.includes('/privacy-policy') || 
                    lowercaseHref.includes('/privacy_policy')) {
                    score += 50;
                    contextSignals.push('privacy_policy_in_url');
                } else if (lowercaseHref.includes('/privacy') || 
                           lowercaseHref.includes('/data-protection')) {
                    score += 45;
                    contextSignals.push('privacy_in_url');
                } else if (lowercaseHref.includes('/data-policy') || 
                           lowercaseHref.includes('/cookies') ||
                           lowercaseHref.includes('/gdpr') ||
                           lowercaseHref.includes('/ccpa')) {
                    score += 40;
                    contextSignals.push('data_policy_in_url');
                } else if (lowercaseHref.includes('/legal') || 
                           lowercaseHref.includes('/policies')) {
                    score += 30;
                    contextSignals.push('legal_in_url');
                }
                
                // HTML5 semantic tagging value
                const isInFooter = (() => {
                    let el = link;
                    while (el && el !== document.body) {
                        const tagName = el.tagName.toLowerCase();
                        const role = (el.getAttribute('role') || '').toLowerCase();
                        const className = (el.className || '').toLowerCase();
                        
                        if (tagName === 'footer' || 
                            role === 'contentinfo' || 
                            className.includes('footer')) {
                            return true;
                        }
                        el = el.parentElement;
                    }
                    return false;
                })();
                
                if (isInFooter) {
                    score += 20;
                    contextSignals.push('in_footer');
                }
                
                return {
                    href: link.href,
                    text: text,
                    score: score,
                    textSignals: textSignals,
                    contextSignals: contextSignals
                };
            }
            
            // Collect and score all links
            const allLinks = Array.from(document.querySelectorAll('a[href]'));
            const scoredLinks = allLinks
                .filter(link => {
                    // Filter out empty, javascript, mailto links
                    const href = link.href.toLowerCase();
                    if (!href || 
                        href.startsWith('javascript:') || 
                        href.startsWith('mailto:') ||
                        href.startsWith('tel:') ||
                        href === '#' ||
                        href.startsWith('#')) {
                        return false;
                    }
                    
                    // Must be same domain or subdomain
                    if (!isSameDomainOrSubdomain(href, baseDomain)) {
                        return false;
                    }
                    
                    return true;
                })
                .map(link => {
                    // Get visible text
                    const text = link.textContent.trim();
                    
                    // If link has no text but has image, try alt text or title
                    let effectiveText = text;
                    if (!effectiveText) {
                        const img = link.querySelector('img');
                        if (img) {
                            effectiveText = img.alt || img.title || '';
                        }
                    }
                    
                    // If still no text, use title attribute or aria-label
                    if (!effectiveText) {
                        effectiveText = link.title || link.getAttribute('aria-label') || '';
                    }
                    
                    return scoreLink(link, effectiveText);
                })
                .filter(link => link.score > 0)
                .sort((a, b) => b.score - a.score);
            
            return scoredLinks;
        }""", base_domain)

        # No links found
        if not links:
            print("No relevant links found using JavaScript method")
            return None, page, unverified_result
        
        # Process and print found links
        print(f"\n🔎 Found {len(links)} potential privacy links:")
        for i, link in enumerate(links[:10]):  # Show top 10
            print(f"{i+1}. Score: {link['score']}, URL: {link['href']}")
            print(f"   Text: '{link['text']}'")
            print(f"   Text signals: {link['textSignals']}")
            print(f"   Context signals: {link['contextSignals']}")
            
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
                    const strongPrivacyMatchers = [
                        'privacy policy', 
                        'privacy notice', 
                        'privacy statement',
                        'data we collect', 
                        'personal information',
                        'data protection',
                        'how we use your data',
                        'cookie policy',
                        'information we collect',
                        'gdpr',
                        'ccpa',
                        'your privacy rights',
                        'data processing',
                        'data sharing',
                        'data retention'
                    ];
                    
                    // Need at least 2 strong matchers to confirm it's a privacy page
                    const matchCount = strongPrivacyMatchers.filter(term => text.includes(term)).length;
                    return matchCount >= 2;
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
    """Find and click on privacy-related links."""
    try:
        links = await page.query_selector_all('a')
        for link in links:
            try:
                text = await link.text_content()
                if not text:
                    continue
                text = text.lower().strip()
                href = await link.get_attribute('href')
                if not href:
                    continue
                score = await score_link_text(text)
                if score > 50:  # High confidence match
                    print(f"Found high confidence link: {text} ({score})")
                    success = await click_and_wait_for_navigation(page, link)
                    if success:
                        is_privacy = await verify_privacy_content(page)
                        if is_privacy:
                            return page.url, page, unverified_result
                        else:
                            if not unverified_result:
                                unverified_result = page.url
            except Exception as e:
                print(f"Error processing link: {e}")
                continue
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_link: {e}")
        return None, page, unverified_result

async def score_link_text(text: str) -> int:
    """Score link text based on likelihood of being privacy link."""
    score = 0
    if text in ['privacy policy', 'privacy notice', 'privacy statement', 'data privacy policy']:
        return 100
    privacy_indicators = [
        ('privacy', 40),
        ('data', 30),
        ('cookie', 25),
        ('information', 20),
        ('policy', 20),
        ('personal', 15),
        ('gdpr', 25),
        ('ccpa', 25),
        ('protection', 15)
    ]
    for term, points in privacy_indicators:
        if term in text:
            score += points
    return score

async def click_and_wait_for_navigation(page, element, timeout=10000):
    """Click a link and wait for navigation."""
    try:
        async with page.expect_navigation(timeout=timeout):
            await element.click()
        return True
    except Exception as e:
        print(f"Navigation error: {e}")
        return False

async def smooth_scroll_and_click(page, context, unverified_result=None, step=150, delay=250):
    print("🔃 Starting smooth scroll with strong term matching...")
    visited_url = None
    current_page = page

    # First check visible links before scrolling
    visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
    if visited_url:
        return visited_url, current_page, unverified_result

    try:
        # Common footer selectors
        footer_selectors = ["footer", ".footer", "#footer", "[role='contentinfo']",
                            ".site-footer", ".page-footer", ".bottom", ".legal", 
                            ".footer-menu", ".footer-links", ".footer-nav"]

        # Get page height for scroll calculations
        page_height = await current_page.evaluate("""() => {
            return document.documentElement.scrollHeight;
        }""")

        # Positions to check before full scroll
        positions_to_check = [
            page_height,
            page_height * 0.9,
            page_height * 0.8,
            page_height * 0.7,
        ]

        for scroll_pos in positions_to_check:
            await current_page.wait_for_timeout(random.randint(500, 1500))
            await current_page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            await current_page.wait_for_timeout(500)

            visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
            if visited_url:
                return visited_url, current_page, unverified_result

        # Check footer area - most common place for privacy links
        for selector in footer_selectors:
            footer = await current_page.query_selector(selector)
            if footer:
                print(f"Found footer with selector: {selector}")
                await footer.scroll_into_view_if_needed()
                await current_page.wait_for_timeout(500)

                visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
                if visited_url:
                    return visited_url, current_page, unverified_result

                privacy_links = await current_page.evaluate("""(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    const links = Array.from(footer.querySelectorAll('a'));
                    return links.filter(link => {
                        const href = link.getAttribute('href') || '';
                        const text = link.textContent.toLowerCase();
                        return href.toLowerCase().includes('privacy') ||
                               href.toLowerCase().includes('data') ||
                               href.toLowerCase().includes('cookie') ||
                               href.toLowerCase().includes('gdpr') ||
                               text.includes('privacy') ||
                               text.includes('data policy') ||
                               text.includes('cookie') ||
                               text.includes('gdpr') ||
                               text.includes('ccpa');
                    }).map(link => ({
                    text: link.textContent.trim(),
                        href: link.getAttribute('href')
                    }));
                }""", selector)

                if privacy_links and len(privacy_links) > 0:
                    print(f"Found {len(privacy_links)} potential privacy links in footer")
                    
                    # Score and sort these links
                    scored_links = []
                    for link in privacy_links:
                        score = 0
                        text = link['text'].lower() if link['text'] else ''
                        href = link['href'].lower() if link['href'] else ''
                        
                        # Score based on text matches
                        if 'privacy policy' in text or 'privacy notice' in text:
                            score += 100
                        elif 'privacy' in text:
                            score += 70
                        elif 'data protection' in text:
                            score += 60
                        elif 'cookie' in text:
                            score += 50
                        elif 'gdpr' in text or 'ccpa' in text:
                            score += 80
                        
                        # Score based on URL patterns
                        if '/privacy-policy' in href or '/privacy_policy' in href:
                            score += 50
                        elif '/privacy' in href:
                            score += 40
                        elif '/data-protection' in href or '/data-policy' in href:
                            score += 40
                        elif '/gdpr' in href or '/ccpa' in href:
                            score += 45
                        
                        scored_links.append((link['href'], score, link['text']))
                    
                    # Sort by score
                    scored_links.sort(key=lambda x: x[1], reverse=True)
                    
                    # Try clicking on the best matches
                    for href, score, text in scored_links[:3]:  # Try top 3
                        if score < 30:  # Skip low confidence matches
                            continue
                            
                        print(f"Footer link: '{text}' → {href} (Score: {score})")
                        try:
                            element = await current_page.query_selector(f"a[href='{href}']")
                            if element:
                                print(f"🔗 Clicking on footer link: {text}")
                                success = await click_and_wait_for_navigation(current_page, element)
                                if success:
                                    # Verify this is a privacy page
                                    is_privacy = await verify_privacy_content(current_page)
                                    if is_privacy:
                                        print(f"✅ Verified as privacy page: {current_page.url}")
                                        return current_page.url, current_page, unverified_result
                                    else:
                                        # Still record as unverified if not confirmed
                                        if not unverified_result:
                                            unverified_result = current_page.url
                                            print(f"⚠️ Not verified as privacy page: {current_page.url}")
                                            
                                        # Go back to keep searching
                                        await current_page.go_back()
                                        await current_page.wait_for_timeout(1000)
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")
                    continue
        
        return None, current_page, unverified_result
        
    except Exception as e:
        print(f"Error in smooth_scroll_and_click: {e}")
        return None, current_page, unverified_result

async def parse_domain_for_common_privacy_paths(domain, url):
    """
    Parse the domain name to create a list of common privacy paths that might work.
    This is especially helpful for sites that block automated access.
    
    Args:
        domain: The domain name
        url: The original URL
        
    Returns:
        list: List of potential privacy URLs based on common patterns
    """
    potential_urls = []
    
    # Base domain
    base_url = f"https://{domain}"
    if not domain.startswith(('http://', 'https://')):
        base_url = f"https://{domain}"
    
    # Extract company name from domain for more specific paths
    company_name = domain.split('.')[0]
    if company_name in ['www', 'app', 'web', 'api', 'dev', 'staging', 'test', 'beta', 'alpha', 'portal', 'dashboard', 'account', 'login', 'auth', 'secure']:
        # Get the next part if the first part is a common subdomain
        parts = domain.split('.')
        if len(parts) > 2:
            company_name = parts[1]
    
    # Common paths to try
    common_paths = [
        '/privacy', 
        '/privacy-policy', 
        '/privacy-notice',
        '/privacy-statement',
        '/data-policy',
        '/data-protection',
        '/legal/privacy', 
        '/legal/privacy-policy',
        '/legal/data-protection',
        '/policies/privacy',
        '/policies/privacy-policy',
        '/policies/cookies',
        '/about/privacy',
        '/about/legal/privacy',
        '/help/privacy',
        '/help/legal/privacy',
        f'/{company_name}/privacy',
        f'/{company_name}/legal/privacy',
        f'/privacy-{company_name}',
        f'/data-privacy-{company_name}',
        '/data-privacy',
        '/personal-information',
        '/cookie-policy',
        '/cookie-notice',
        '/gdpr',
        '/ccpa',
        '/cookies',
        '/data-collection'
    ]
    
    # Language variations
    languages = ['en', 'en-us', 'en-gb', 'us', 'gb', 'global']
    language_paths = []
    for path in common_paths:
        for lang in languages:
            language_paths.append(f'/{lang}{path}')
            language_paths.append(f'{path}/{lang}')
    
    common_paths.extend(language_paths)
    
    # Add all common paths to potential URLs
    for path in common_paths:
        potential_urls.append(f"{base_url}{path}")
    
    # Try potential subdomain patterns
    subdomain_patterns = [
        f"https://privacy.{domain.split('www.', 1)[-1]}",
        f"https://policy.{domain.split('www.', 1)[-1]}/privacy",
        f"https://policies.{domain.split('www.', 1)[-1]}/privacy",
        f"https://data.{domain.split('www.', 1)[-1]}/privacy",
        f"https://legal.{domain.split('www.', 1)[-1]}/privacy",
        f"https://help.{domain.split('www.', 1)[-1]}/privacy",
        f"https://docs.{domain.split('www.', 1)[-1]}/privacy",
        f"https://about.{domain.split('www.', 1)[-1]}/privacy"
    ]
    
    potential_urls.extend(subdomain_patterns)
    
    # Add some dynamic patterns based on company name
    dynamic_patterns = [
        f"https://{domain}/legal/{company_name}-privacy",
        f"https://{domain}/{company_name}-privacy",
        f"https://{domain}/legal/{company_name}-privacy-policy",
        f"https://{domain}/{company_name}-data-policy",
        f"https://{domain}/about/{company_name}-privacy",
        # Common hub page patterns
        f"https://{domain}/legal",
        f"https://{domain}/policies",
        f"https://{domain}/privacy-center",
        f"https://{domain}/data-protection-center",
        f"https://{domain}/about/legal",
        f"https://{domain}/help/legal",
        # Common variations of privacy pages
        f"https://{domain}/privacy/policy",
        f"https://{domain}/privacy/cookies",
        f"https://{domain}/privacy/data-collection",
        f"https://{domain}/privacy/your-rights",
        f"https://{domain}/cookie-consent"
    ]
    
    # Add all dynamic patterns to potential URLs
    potential_urls.extend(dynamic_patterns)
    
    # Common path suffixes to try
    suffixes = ['.html', '.htm', '.php', '.aspx', '.jsp', '']
    suffix_urls = []
    
    # Add suffix variations to certain paths
    for url in potential_urls:
        if not any(url.endswith(ext) for ext in ['.html', '.htm', '.php', '.aspx', '.jsp']):
            for suffix in suffixes:
                if suffix:  # Skip empty suffix for URLs that already have one
                    suffix_urls.append(f"{url}{suffix}")

    potential_urls.extend(suffix_urls)

    # Remove duplicates while preserving order
    seen = set()
    unique_urls = []
    for url in potential_urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    # Sort by likelihood of being correct privacy policy URL
    scored_urls = []
    for url in unique_urls:
        score = 0
        url_lower = url.lower()
        
        # Score based on URL patterns
        if '/privacy-policy' in url_lower or '/privacy_policy' in url_lower:
            score += 100
        elif '/privacy' in url_lower:
            score += 80
        elif '/data-protection' in url_lower or '/data_protection' in url_lower:
            score += 70
        elif '/data-privacy' in url_lower or '/data_privacy' in url_lower:
            score += 65
        elif '/gdpr' in url_lower or '/ccpa' in url_lower:
            score += 60
        elif '/legal/privacy' in url_lower:
            score += 85
        elif '/cookies' in url_lower or '/cookie-policy' in url_lower:
            score += 50
        elif '/policies/privacy' in url_lower:
            score += 75
        elif '/privacy-center' in url_lower:
            score += 55
        elif '/legal' in url_lower:
            score += 40
        elif '/policies' in url_lower:
            score += 35
        
        # Prefer simpler paths
        path_segments = url_lower.split('/')
        if len(path_segments) <= 4:  # domain/privacy is simpler than domain/legal/terms/privacy
            score += 10
        
        # Prefer standard extensions
        if url_lower.endswith('.html'):
            score += 5
        elif url_lower.endswith('.php'):
            score += 3
        
        scored_urls.append((url, score))
    
    # Sort by score and return
    scored_urls.sort(key=lambda x: x[1], reverse=True)
    return [url for url, _ in scored_urls]

async def verify_privacy_content(page, unverified_result=None):
    """
    Verify if the current page appears to be a privacy policy page.
    
    Args:
        page: Playwright page object
        unverified_result: Optional fallback URL
        
    Returns:
        Boolean indicating if the page appears to be a privacy policy page
    """
    try:
        # Get the page title
        title = await page.title()
        title_lower = title.lower()
        
        # Check title for privacy indicators
        if any(term in title_lower for term in ['privacy', 'data', 'cookie', 'gdpr', 'ccpa']):
            print(f"Page title indicates privacy policy: {title}")
            return True
            
        # Check content for privacy indicators using JavaScript
        has_privacy_content = await page.evaluate("""
        () => {
            try {
                const bodyText = document.body.innerText.toLowerCase();
                
                // Check for privacy policy sections/headers
                const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                    .map(el => el.textContent.toLowerCase().trim());
                
                const privacyHeadings = headings.some(heading => 
                    heading.includes('privacy') || 
                    heading.includes('data protection') ||
                    heading.includes('personal information') ||
                    heading.includes('data policy') ||
                    heading.includes('gdpr') ||
                    heading.includes('ccpa')
                );
                
                // Check for common privacy policy phrases
                const privacyPhrases = [
                    'privacy policy',
                    'privacy notice',
                    'privacy statement',
                    'we collect',
                    'personal information',
                    'information we collect',
                    'how we use your data',
                    'use of cookies',
                    'data protection',
                    'data rights',
                    'your rights',
                    'gdpr',
                    'ccpa',
                    'california privacy rights',
                    'european data protection'
                ];
                
                // Check for legal sections specific to privacy policies
                const privacySections = [
                    'information collection',
                    'information usage',
                    'data sharing',
                    'data retention',
                    'cookie policy',
                    'user rights',
                    'gdpr',
                    'ccpa',
                    'third party access',
                    'data storage',
                    'consent',
                    'data security',
                    'data transfers',
                    'tracking technologies',
                    'opt-out',
                    'do not track',
                    'legal basis',
                    'data controller',
                    'data processor'
                ];
                
                // Count matches
                const privacyMatches = privacyPhrases.filter(phrase => bodyText.includes(phrase)).length;
                const sectionMatches = privacySections.filter(section => bodyText.includes(section)).length;
                
                // Get text length - privacy policies tend to be longer
                const textLength = bodyText.length;
                
                // Look for common data protection regulation references
                const hasRegulations = bodyText.includes('gdpr') || 
                                      bodyText.includes('ccpa') || 
                                      bodyText.includes('lgpd') || 
                                      bodyText.includes('pipeda') ||
                                      bodyText.includes('data protection act') ||
                                      bodyText.includes('california consumer privacy act') ||
                                      bodyText.includes('general data protection regulation');
                
                // Return true if enough indicators are found
                return {
                    hasPrivacyHeadings: privacyHeadings,
                    privacyMatchCount: privacyMatches,
                    sectionMatchCount: sectionMatches,
                    hasRegulations: hasRegulations,
                    textLength: textLength,
                    isPrivacyPage: privacyHeadings || 
                                 privacyMatches >= 2 || 
                                 sectionMatches >= 3 || 
                                 (privacyMatches + sectionMatches >= 3) ||
                                 (hasRegulations && (privacyMatches > 0 || sectionMatches > 0)) ||
                                 (textLength > 5000 && (privacyMatches > 0 || sectionMatches > 0))
                };
            } catch (e) {
                return {
                    error: e.toString(),
                    isPrivacyPage: false
                };
            }
        }
        """)
        
        # Check if the evaluation was successful
        if 'isPrivacyPage' in has_privacy_content:
            if has_privacy_content['isPrivacyPage']:
                print("✅ Page content confirms this is a privacy policy page")
                print(f"  - Has privacy headings: {has_privacy_content.get('hasPrivacyHeadings', False)}")
                print(f"  - Privacy phrases matched: {has_privacy_content.get('privacyMatchCount', 0)}")
                print(f"  - Section matches: {has_privacy_content.get('sectionMatchCount', 0)}")
                print(f"  - Has regulations: {has_privacy_content.get('hasRegulations', False)}")
                print(f"  - Content length: {has_privacy_content.get('textLength', 0)}")
                return True
            else:
                print("❌ Page content does not appear to be a privacy policy")
                print(f"  - Has privacy headings: {has_privacy_content.get('hasPrivacyHeadings', False)}")
                print(f"  - Privacy phrases matched: {has_privacy_content.get('privacyMatchCount', 0)}")
                print(f"  - Section matches: {has_privacy_content.get('sectionMatchCount', 0)}")
                print(f"  - Has regulations: {has_privacy_content.get('hasRegulations', False)}")
                print(f"  - Content length: {has_privacy_content.get('textLength', 0)}")
                
                if 'error' in has_privacy_content:
                    print(f"  - Error: {has_privacy_content['error']}")
        
        # Fallback check if JavaScript evaluation failed
        content = await page.content()
        content_lower = content.lower()
        
        # Perform a simple content check
        privacy_terms = ['privacy policy', 'privacy notice', 'personal information', 'data protection']
        if any(term in content_lower for term in privacy_terms):
            print("✅ Simple content check indicates this is a privacy policy page")
            return True
            
        return False
    except Exception as e:
        print(f"Error verifying privacy content: {e}")
        # More lenient in case of errors - default to accepting if we can't verify
        if unverified_result:
            print("⚠️ Error during verification, accepting as unverified result")
            return False

async def try_fallback_privacy_paths(url, page):
    """Try common fallback paths when direct navigation fails."""
    print("Trying fallback privacy paths...")
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    # Use dynamic discovery to generate potential paths
    potential_urls = await parse_domain_for_common_privacy_paths(domain, url)
    
    # Try each potential URL
    for potential_url in potential_urls[:10]:  # Limit to first 10 to avoid too many requests
        try:
            print(f"Trying fallback path: {potential_url}")
            response = await page.goto(potential_url, timeout=5000, wait_until="domcontentloaded")
            
            if response and response.ok:
                print(f"✅ Successfully accessed fallback path: {potential_url}")
                
                # Verify this is actually a privacy policy page
                is_privacy = await verify_privacy_content(page, potential_url)
                if is_privacy:
                    print(f"✅ Verified as privacy policy page: {potential_url}")
                    return potential_url
                else:
                    print(f"❌ Not a privacy policy page: {potential_url}")
        except Exception as e:
            print(f"❌ Error accessing fallback path {potential_url}: {e}")
            continue
    
    # If all else fails, return most likely path as unverified
    if potential_urls:
        print(f"⚠️ Returning most likely privacy path (unverified): {potential_urls[0]}")
        return potential_urls[0]
    
    return None

async def check_for_better_privacy_link(page, current_url):
    """
    Check if current page has links to more specific privacy pages.
    Sometimes a 'legal' or 'policies' page will link to the actual privacy policy.
    
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
        
        # Get all links and score them
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
                
            # Score the link
            score = 0
            text = link['text'].lower()
            url = link['href'].lower()
            
            # Score based on text
            if 'privacy policy' in text:
                score += 100
            elif 'privacy notice' in text or 'privacy statement' in text:
                score += 90
            elif 'privacy' in text:
                score += 80
            elif 'data protection' in text:
                score += 75
            elif 'data policy' in text:
                score += 70
            elif 'cookie policy' in text or 'cookies' in text:
                score += 60
            elif 'gdpr' in text or 'ccpa' in text:
                score += 85
            
            # Score based on URL
            if '/privacy-policy' in url:
                score += 80
            elif '/privacy' in url:
                score += 70
            elif '/data-protection' in url:
                score += 65
            elif '/gdpr' in url or '/ccpa' in url:
                score += 75
            elif '/cookies' in url:
                score += 50
            
            if score > 0:
                scored_links.append((link['href'], score, text))
            
        # Sort by score
        scored_links.sort(key=lambda x: x[1], reverse=True)
        
        # Check top scoring links
        for link_url, score, text in scored_links[:3]:  # Check top 3
            if score < 70:  # Only use high-confidence matches
                continue
                
            print(f"Found potential better privacy link: {text} → {link_url} (Score: {score})")
            
            try:
                # Navigate to the link
                await page.goto(link_url, timeout=10000, wait_until="domcontentloaded")
                
                # Verify if this is actually a privacy page
                is_privacy = await verify_privacy_content(page)
                if is_privacy:
                    print(f"✅ Verified better privacy page: {page.url}")
                    return page.url, page
                else:
                    print(f"❌ Not verified as privacy page: {page.url}")
                    
                    # Go back to keep checking other links
                    await page.go_back()
                    await page.wait_for_timeout(1000)
            except Exception as e:
                print(f"Error checking potential privacy link: {e}")
                continue
            
        return None, page
    except Exception as e:
        print(f"Error checking for better privacy links: {e}")
        return None, page

async def use_network_requests_for_privacy_discovery(page, context):
    """
    Analyze network requests to find privacy-related resources.
    Some sites load privacy policies from separate resource URLs.
    
    Returns:
        Tuple of (privacy_url, page, unverified_result)
    """
    print("Analyzing network requests for privacy-related resources...")
    
    try:
        # Get current base URL
        base_url = await page.evaluate("window.location.origin")
        
        # Extract base domain
        parsed_url = urlparse(base_url)
        base_domain = parsed_url.netloc
        
        # Listen for network requests
        privacy_requests = []
        
        # Define a request handler
        async def request_handler(request):
            url = request.url
            if 'privacy' in url.lower() or 'gdpr' in url.lower() or 'ccpa' in url.lower() or 'data-protection' in url.lower():
                # Only include same-domain resources
                req_domain = urlparse(url).netloc
                if req_domain == base_domain or req_domain.endswith('.' + base_domain) or base_domain.endswith('.' + req_domain):
                    privacy_requests.append(url)
        
        # Set up listener
        page.on('request', request_handler)
        
        # Refresh the page to capture requests
        await page.reload(wait_until="networkidle")
        
        # Wait for any delayed requests
        await page.wait_for_timeout(3000)
        
        # Remove listener
        page.remove_listener('request', request_handler)
        
        # Process found requests
        if privacy_requests:
            print(f"Found {len(privacy_requests)} privacy-related network requests:")
            
            # Score and filter requests
            scored_requests = []
            for url in privacy_requests:
                score = 0
                url_lower = url.lower()
                
                # Score based on URL patterns
                if '/privacy-policy' in url_lower or '/privacy_policy' in url_lower:
                    score += 100
                elif '/privacy' in url_lower:
                    score += 80
                elif '/data-protection' in url_lower:
                    score += 70
                elif '/gdpr' in url_lower or '/ccpa' in url_lower:
                    score += 90
                elif '/cookies' in url_lower:
                    score += 60
                
                # Consider resource type based on extension
                if url_lower.endswith('.html'):
                    score += 30
                elif url_lower.endswith('.pdf'):
                    score += 25
                elif url_lower.endswith('.txt'):
                    score += 20
                elif url_lower.endswith('.json'):
                    score -= 10  # Likely API data, not the policy itself
                elif url_lower.endswith('.jpg') or url_lower.endswith('.png') or url_lower.endswith('.gif'):
                    score -= 20  # Images are unlikely to be the policy
                
                if score > 0:
                    scored_requests.append((url, score))
            
            # Sort by score
            scored_requests.sort(key=lambda x: x[1], reverse=True)
            
            # Check top scoring requests
            for url, score in scored_requests[:3]:  # Check top 3
                if score < 50:  # Only use high-confidence matches
                    continue
                    
                print(f"Checking privacy-related resource: {url} (Score: {score})")
                
                try:
                    # Navigate to the resource URL
                    await page.goto(url, timeout=10000, wait_until="domcontentloaded")
                    
                    # Verify if this is a privacy page
                    is_privacy = await verify_privacy_content(page)
                    if is_privacy:
                        print(f"✅ Verified privacy page from network request: {page.url}")
                        return page.url, page, None
                    else:
                        print(f"❌ Resource doesn't appear to be a privacy policy: {page.url}")
                except Exception as e:
                    print(f"Error checking privacy resource: {e}")
                    continue
        
        return None, page, None
    except Exception as e:
        print(f"Error analyzing network requests: {e}")
        return None, page, None
    
async def check_cookie_banner_for_privacy_link(page):
    """
    Check if there's a cookie consent banner with privacy policy links.
    Many sites include privacy policy links in their cookie consent banners.
    
    Returns:
        Privacy policy URL if found, None otherwise
    """
    print("Checking for privacy links in cookie banners...")
    
    try:
        # Common cookie banner selectors
        cookie_banner_selectors = [
            "#cookie-banner", 
            "#cookie-consent", 
            "#cookie-notice",
            "#gdpr-banner",
            "#consent-banner",
            ".cookie-banner", 
            ".cookie-consent", 
            ".cookie-notice",
            ".gdpr-banner",
            ".consent-banner",
            "[aria-label*='cookie']",
            "[role='dialog'][aria-labelledby*='cookie']",
            "[data-purpose='cookie-banner']",
            "[class*='cookie']",
            "[class*='gdpr']",
            "[class*='consent']",
            "[class*='privacy']",
            "[id*='cookie']",
            "[id*='gdpr']",
            "[id*='consent']"
        ]
        
        # Check each potential banner selector
        for selector in cookie_banner_selectors:
            banner = await page.query_selector(selector)
            if banner:
                print(f"Found cookie banner with selector: {selector}")
                
                # Check for privacy links in the banner
                privacy_links = await page.evaluate("""(selector) => {
                    const banner = document.querySelector(selector);
                    if (!banner) return [];
                    
                    const links = Array.from(banner.querySelectorAll('a[href]'));
                    return links.filter(link => {
                        const href = link.getAttribute('href') || '';
                        const text = link.textContent.toLowerCase();
                        
                        return (text.includes('privacy') || 
                               text.includes('data policy') || 
                               text.includes('data protection') ||
                               href.toLowerCase().includes('privacy') ||
                               href.toLowerCase().includes('data-policy') ||
                               href.toLowerCase().includes('data-protection'));
                    }).map(link => ({
                        text: link.textContent.trim(),
                        href: link.href
                    }));
                }""", selector)
                
                if privacy_links and len(privacy_links) > 0:
                    print(f"Found {len(privacy_links)} privacy links in cookie banner")
                    
                    # Score and sort these links
                    scored_links = []
                    for link in privacy_links:
                        score = 0
                        text = link['text'].lower() if link['text'] else ''
                        href = link['href'].lower() if link['href'] else ''
                        
                        # Score based on text
                        if 'privacy policy' in text:
                            score += 100
                        elif 'privacy notice' in text or 'privacy statement' in text:
                            score += 90
                        elif 'privacy' in text:
                            score += 80
                        elif 'data protection' in text or 'data policy' in text:
                            score += 75
                        
                        # Score based on URL
                        if '/privacy-policy' in href or '/privacy_policy' in href:
                            score += 80
                        elif '/privacy' in href:
                            score += 70
                        elif '/data-protection' in href or '/data-policy' in href:
                            score += 60
                        
                        scored_links.append((link['href'], score, text))
                    
                    # Sort by score
                    scored_links.sort(key=lambda x: x[1], reverse=True)
                    
                    # Try the highest scored link
                    if scored_links:
                        best_link = scored_links[0][0]
                        print(f"Best privacy link from cookie banner: {best_link} (Score: {scored_links[0][1]}, Text: '{scored_links[0][2]}')")
                        
                        # Try to navigate to the link
                        try:
                            old_url = page.url
                            
                            # Check if the link is a fragment on the same page
                            if best_link.startswith('#') or best_link == old_url:
                                print("Link is a fragment or current page, skipping navigation")
                                return None
                                
                            await page.goto(best_link, timeout=10000, wait_until="domcontentloaded")
                            
                            # Verify this is a privacy page
                            is_privacy = await verify_privacy_content(page)
                            if is_privacy:
                                print(f"✅ Verified privacy page from cookie banner: {page.url}")
                                return page.url
                            else:
                                print(f"❌ Link from cookie banner is not a privacy page: {page.url}")
                                
                                # Go back
                                await page.goto(old_url)
                                await page.wait_for_timeout(1000)
                        except Exception as e:
                            print(f"Error navigating to privacy link from cookie banner: {e}")
                            return None
        
        return None
    except Exception as e:
        print(f"Error checking cookie banner for privacy links: {e}")
        return None

async def analyze_page_structure_for_privacy_section(page):
    """
    Some sites embed privacy information directly in the page
    instead of linking to a separate page.
    
    Returns:
        True if privacy section found on page, False otherwise
    """
    print("Analyzing page structure for embedded privacy sections...")
    
    try:
        # Check for privacy-related sections in the page
        embedded_privacy = await page.evaluate("""() => {
            // Look for sections that might contain privacy information
            const potentialSections = [
                // By ID
                document.getElementById('privacy'),
                document.getElementById('privacy-policy'),
                document.getElementById('data-policy'),
                document.getElementById('data-protection'),
                document.getElementById('privacy-notice'),
                
                // By class
                document.querySelector('.privacy-policy'),
                document.querySelector('.privacy-notice'),
                document.querySelector('.data-policy'),
                
                // By attribute
                document.querySelector('[data-section="privacy"]'),
                document.querySelector('[data-content="privacy"]'),
                
                // By heading followed by content
                ...Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                    .filter(h => 
                        h.textContent.toLowerCase().includes('privacy') ||
                        h.textContent.toLowerCase().includes('data protection') ||
                        h.textContent.toLowerCase().includes('data policy')
                    )
                    .map(h => h.parentElement)
            ].filter(Boolean); // Remove null/undefined
            
            // If no sections found, return false
            if (potentialSections.length === 0) {
                return { found: false };
            }
            
            // For each potential section, check content
            for (const section of potentialSections) {
                const textContent = section.textContent.toLowerCase();
                
                // Privacy policy indicators
                const privacyIndicators = [
                    'information we collect',
                    'personal information',
                    'data collection',
                    'use of information',
                    'cookies',
                    'third parties',
                    'analytics',
                    'gdpr',
                    'ccpa',
                    'data sharing',
                    'data storage',
                    'data security'
                ];
                
                // Count how many indicators are present
                const matchCount = privacyIndicators.filter(indicator => 
                    textContent.includes(indicator)
                ).length;
                
                // If enough indicators, consider it a privacy section
                if (matchCount >= 3 && textContent.length > 1000) {
                    // Get heading if available
                    const heading = section.querySelector('h1, h2, h3, h4, h5, h6');
                    const headingText = heading ? heading.textContent.trim() : 'Unknown section';
                    
                    return {
                        found: true,
                        heading: headingText,
                        indicators: matchCount,
                        textLength: textContent.length
                    };
                }
            }
            
            return { found: false };
        }""")
        
        if embedded_privacy and embedded_privacy.get('found', False):
            print(f"✅ Found embedded privacy section: '{embedded_privacy.get('heading', 'Unknown')}'")
            print(f"  - Matched indicators: {embedded_privacy.get('indicators', 0)}")
            print(f"  - Content length: {embedded_privacy.get('textLength', 0)} characters")
            return True
        else:
            print("❌ No embedded privacy section found on page")
            return False
    except Exception as e:
        print(f"Error analyzing page for embedded privacy sections: {e}")
        return False

def is_valid_privacy_page(content):
    """Validate if page content matches privacy policy criteria.
    
    Args:
        content: Dict with text and headings from page
        
    Returns:
        bool: True if valid privacy page
    """
    # Required terms that should be present
    required_terms = ['privacy', 'information', 'data', 'collect']
    
    # Privacy sections that indicate a privacy policy
    privacy_sections = [
        'information collection',
        'information usage', 
        'data sharing', 
        'data retention',
        'cookie policy',
        'user rights',
        'gdpr',
        'ccpa',
        'third party',
        'data storage',
        'data security',
        'analytics',
        'tracking technologies'
    ]
    
    # Privacy phrases that indicate privacy content
    privacy_phrases = [
        'privacy policy',
        'privacy notice',
        'privacy statement',
        'we collect',
        'personal information',
        'information we collect',
        'how we use your data',
        'use of cookies',
        'data protection',
        'your rights',
        'opt out',
        'third parties',
        'data controller',
        'data processor'
    ]
    
    if not content or 'text' not in content:
        return False
        
    text = content.get('text', '').lower()
    headings = content.get('headings', [])
    
    # Check for a minimum content length (privacy policies tend to be long)
    if len(text) < 2000:  # Reduced threshold for better coverage
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
        if 'privacy' in h or 'data' in h or 'cookies' in h or 'gdpr' in h or 'ccpa' in h
    )
    
    # Calculate score based on content
    score = 0
    if terms_count >= 3:
        score += 30
    if section_count >= 2:
        score += 20
    if phrase_count >= 3:
        score += 30
    if has_privacy_heading:
        score += 20
    
    # More lenient threshold - if enough privacy indicators, it's likely a privacy page
    if score >= 60 or (phrase_count >= 3 and terms_count >= 2):
        return True
    
    # If page has substantial privacy phrases and is long enough, it's likely a privacy page
    if len(text) > 5000 and phrase_count >= 2 and terms_count >= 2:
        return True
    
    return False

async def main():
    """Test function for development purposes."""
    import sys
    if len(sys.argv) < 2:
        print("Usage: python privacy.py URL")
        return
    
    url = sys.argv[1]
    print(f"Testing privacy policy finder with URL: {url}")
    
    request = PrivacyRequest(url=url)
    
    try:
        response = await find_privacy(request)
        print("=" * 50)
        print("Results:")
        print(f"URL: {response.url}")
        print(f"Success: {response.success}")
        print(f"Method used: {response.method_used}")
        print(f"Message: {response.message}")
        print(f"Privacy Policy URL: {response.pp_url if response.pp_url else 'Not found'}")
        print("=" * 50)
        
        return response.pp_url
        
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

# Advanced privacy policy content analysis functions

async def analyze_privacy_policy_content(page):
    """
    Analyze the content of a privacy policy page to extract key information.
    This can be used for further verification or content analysis.
    
    Returns:
        Dict containing analysis results
    """
    try:
        print("Analyzing privacy policy content...")
        
        analysis_result = await page.evaluate("""() => {
            // Helper function to extract text from a section
            function getTextFromSection(heading) {
                const headingElem = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                    .find(h => h.textContent.toLowerCase().includes(heading.toLowerCase()));
                
                if (!headingElem) return null;
                
                // Get next heading to determine section end
                let nextHeading = headingElem;
                let sectionText = '';
                
                // Loop through next elements until we find another heading
                while (nextHeading && nextHeading.nextElementSibling) {
                    nextHeading = nextHeading.nextElementSibling;
                    
                    // Stop if we hit another heading
                    if (nextHeading.tagName.match(/^H[1-6]$/i)) {
                        break;
                    }
                    
                    // Add text content
                    sectionText += nextHeading.textContent + ' ';
                }
                
                return sectionText.trim();
            }
            
            // Get all headings
            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                .map(h => h.textContent.trim());
            
            // Get full text
            const fullText = document.body.innerText;
            
            // Extract key sections
            const dataCollectionText = getTextFromSection('information we collect') || 
                                       getTextFromSection('data we collect') || 
                                       getTextFromSection('personal information') ||
                                       getTextFromSection('data collection');
            
            const dataSharingText = getTextFromSection('sharing') || 
                                    getTextFromSection('third part') || 
                                    getTextFromSection('disclosure') ||
                                    getTextFromSection('information sharing');
            
            const cookieText = getTextFromSection('cookie') || 
                               getTextFromSection('tracking technologies') ||
                               getTextFromSection('tracking tools');
            
            const rightsText = getTextFromSection('your rights') || 
                               getTextFromSection('user rights') || 
                               getTextFromSection('access rights') ||
                               getTextFromSection('data subject rights');
            
            // Extract privacy regulations mentioned
            const regulations = [];
            if (fullText.toLowerCase().includes('gdpr')) regulations.push('GDPR');
            if (fullText.toLowerCase().includes('ccpa')) regulations.push('CCPA');
            if (fullText.toLowerCase().includes('cpra')) regulations.push('CPRA');
            if (fullText.toLowerCase().includes('lgpd')) regulations.push('LGPD');
            if (fullText.toLowerCase().includes('pipeda')) regulations.push('PIPEDA');
            
            // Extract last updated date
            const lastUpdatedMatch = fullText.match(/last (?:updated|modified|revised|changed)\\s*:\\s*([A-Za-z0-9,\\s]+\\d{4})/i) ||
                                    fullText.match(/(?:updated|modified|revised|changed)\\s*(?:on|at|date)?\\s*:\\s*([A-Za-z0-9,\\s]+\\d{4})/i) ||
                                    fullText.match(/(?:effective|update) date\\s*:\\s*([A-Za-z0-9,\\s]+\\d{4})/i);
            
            const lastUpdated = lastUpdatedMatch ? lastUpdatedMatch[1].trim() : null;
            
            // Check if it's a privacy policy and not TOS/other legal document
            const isPrivacyPolicy = fullText.toLowerCase().includes('privacy policy') || 
                                    fullText.toLowerCase().includes('privacy notice') ||
                                    fullText.toLowerCase().includes('privacy statement');
            
            return {
                isPrivacyPolicy: isPrivacyPolicy,
                headings: headings,
                lastUpdated: lastUpdated,
                regulations: regulations,
                sections: {
                    dataCollection: dataCollectionText ? dataCollectionText.substring(0, 200) + '...' : null,
                    dataSharing: dataSharingText ? dataSharingText.substring(0, 200) + '...' : null,
                    cookies: cookieText ? cookieText.substring(0, 200) + '...' : null,
                    rights: rightsText ? rightsText.substring(0, 200) + '...' : null
                },
                textLength: fullText.length
            };
        }""")
        
        print("\n=== Privacy Policy Analysis ===")
        print(f"Is privacy policy: {analysis_result.get('isPrivacyPolicy', False)}")
        print(f"Last updated: {analysis_result.get('lastUpdated', 'Not found')}")
        print(f"Regulations mentioned: {', '.join(analysis_result.get('regulations', []))}")
        print(f"Content length: {analysis_result.get('textLength', 0)} characters")
        
        print("\nKey sections:")
        sections = analysis_result.get('sections', {})
        for section_name, section_text in sections.items():
            if section_text:
                print(f"- {section_name}: Found")
            else:
                print(f"- {section_name}: Not found")
        
        return analysis_result
    except Exception as e:
        print(f"Error analyzing privacy policy content: {e}")
        return {
            'isPrivacyPolicy': False,
            'error': str(e)
        }

async def extract_company_contact_from_privacy(page):
    """
    Extract company contact information from privacy policy.
    Many privacy policies include contact details for data protection officers.
    
    Returns:
        Dict containing contact information
    """
    try:
        print("Extracting contact information from privacy policy...")
        
        contact_info = await page.evaluate("""() => {
            const text = document.body.innerText.toLowerCase();
            
            // Helper function to extract contact section
            function getContactSection() {
                const contactHeadings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'))
                    .filter(h => 
                        h.textContent.toLowerCase().includes('contact') ||
                        h.textContent.toLowerCase().includes('reach us') ||
                        h.textContent.toLowerCase().includes('data protection officer') ||
                        h.textContent.toLowerCase().includes('dpo') ||
                        h.textContent.toLowerCase().includes('get in touch')
                    );
                    
                if (contactHeadings.length === 0) return null;
                
                // Get the first contact heading and its content
                const heading = contactHeadings[0];
                let nextElem = heading.nextElementSibling;
                let content = '';
                
                // Get content until next heading or max 5 elements
                let count = 0;
                while (nextElem && count < 5 && !nextElem.tagName.match(/^H[1-6]$/i)) {
                    content += nextElem.textContent + ' ';
                    nextElem = nextElem.nextElementSibling;
                    count++;
                }
                
                return content.trim();
            }
            
            // Extract emails
            const emailRegex = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}/g;
            const emails = text.match(emailRegex) || [];
            
            // Extract phone numbers (basic pattern)
            const phoneRegex = /(?:\\+?\\d{1,3}[-.\\s]?)?\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}/g;
            const phones = text.match(phoneRegex) || [];
            
            // Extract addresses (look for postal/zip codes)
            const addressRegex = /(?:\\d+\\s+[a-zA-Z]+\\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|way|court|ct|plaza|square|sq|parkway|pkwy)\\s*,?\\s*)[a-zA-Z]+\\s*,?\\s*[a-zA-Z]{2}\\s*\\d{5}(?:-\\d{4})?/gi;
            const addresses = text.match(addressRegex) || [];
            
            // Get contact section if available
            const contactSection = getContactSection();
            
            // Check for data protection officer
            const hasDPO = text.includes('data protection officer') || text.includes('dpo');
            
            return {
                emails: emails,
                phones: phones,
                addresses: addresses,
                contactSection: contactSection,
                hasDPO: hasDPO
            };
        }""")
        
        print("\n=== Contact Information ===")
        if contact_info.get('emails', []):
            print(f"Emails: {', '.join(contact_info.get('emails', []))}")
        else:
            print("Emails: None found")
            
        if contact_info.get('phones', []):
            print(f"Phone numbers: {', '.join(contact_info.get('phones', []))}")
        else:
            print("Phone numbers: None found")
            
        if contact_info.get('hasDPO', False):
            print("Data Protection Officer: Mentioned")
        else:
            print("Data Protection Officer: Not mentioned")
        
        return contact_info
    except Exception as e:
        print(f"Error extracting contact information: {e}")
        return {
            'error': str(e)
        }

# Run the script
if __name__ == "__main__":
    import asyncio
    result = asyncio.run(main())
    sys.exit(0 if result else 1)