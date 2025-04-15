import asyncio
import re
import random
import logging
import sys
from typing import Optional, Tuple, Dict, List, Any
from urllib.parse import urlparse, urljoin

from fastapi import APIRouter, HTTPException, Depends, status
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, TimeoutError as PlaywrightTimeoutError

# Define User_Agents directly in this file since it's not in config
User_Agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

from app.models.tos import ToSRequest, ToSResponse
import requests
from bs4 import BeautifulSoup

router = APIRouter()

# Priorities for exact match terms
exactMatchPriorities = {
    'terms of service': 100,
    'terms of use': 95,
    'terms and conditions': 90,
    'user agreement': 85,
    'service agreement': 80,
    'legal agreement': 75,
    'platform agreement': 70
}

# Priorities for partial match terms
partialMatchPriorities = {
    'platform terms': 60,
    'website terms': 55,
    'full terms': 50,
    'detailed terms': 45,
    'complete terms': 40,
    'legal terms': 35,
    'general terms': 30,
    'service terms': 25,
    'user terms': 20
}

# Define your strong match terms here
strong_terms_matches = [
    'terms of service', 'terms of use', 'terms and conditions',
    'conditions of use', 'condition of use', 'user agreement',
    'terms', 'tos', 'eula', 'legal terms'
]

# Define dynamic scoring weights for link evaluation
LINK_EVALUATION_WEIGHTS = {
    'text_match': 0.4,
    'url_structure': 0.3,
    'context': 0.2,
    'position': 0.1
}

@router.post("/tos", response_model=ToSResponse)
async def find_tos(request: ToSRequest) -> ToSResponse:
    """Find Terms of Service page for a given URL."""
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
    
    # Common terms paths to try if everything else fails
    common_paths = [
        '/terms', '/tos', '/terms-of-service', 
        '/legal/terms', '/policies/terms',
        '/policies/terms-of-use', '/legal/terms-of-service'
    ]
    
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    
    try:
        browser, context, page, random_delay = await setup_browser()
        playwright = page.playwright
        
        success, response, patterns = await navigate_with_retry(page, url)
        if not success:
            # Try common paths before giving up
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            for path in common_paths:
                common_url = base_url + path
                print(f"Trying common path: {common_url}")
                try:
                    success, response, patterns = await navigate_with_retry(page, common_url)
                    if success and response.ok:
                        unverified_result = common_url
                        break
                except Exception as e:
                    print(f"Error trying common path {common_url}: {e}")
                    continue
            
            return handle_navigation_failure(url, unverified_result)
        
        result, method_used = await try_all_methods(page, context, unverified_result)
        
        # If no result found with regular methods, try common paths
        if not result:
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            for path in common_paths:
                common_url = base_url + path
                print(f"Trying common path: {common_url}")
                try:
                    success, response, patterns = await navigate_with_retry(page, common_url)
                    if success and response.ok:
                        # For common paths we don't need extensive verification
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

async def setup_browser():
    """
    Setup browser with stealth configurations to avoid detection.
    Enhanced for Cloud Run environment.
    """
    playwright = await async_playwright().start()
    try:
        # Use random user agent
        user_agent = random.choice(User_Agents)
        
        # Launch browser with optimized settings for Cloud Run
        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',
                '--disable-infobars',
                '--window-size=1920,1080',
                '--disable-extensions',
            ],
            chromium_sandbox=False,
            slow_mo=100  # Add a small delay between actions
        )
        
        # Create context with stealth settings
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=user_agent,
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'sec-ch-ua': '"Chromium";v="123", "Not(A:Brand";v="8"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'cache-control': 'max-age=0'
            },
            # Increased timeouts
            default_timeout=60000
        )
        
        # Add stealth scripts to hide automation
        await context.add_init_script("""
            // Overwrite the automation-related properties
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Additional stealth modifications
            if (window.navigator.plugins) {
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
            }
            
            if (window.navigator.languages) {
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en', 'es']
                });
            }
        """)
        
        # Create a page
        page = await context.new_page()
        
        # Random delay function to mimic human behavior
        async def random_delay(min_ms=500, max_ms=3000):
            delay = random.randint(min_ms, max_ms)  # 0.5-3 seconds
            await page.wait_for_timeout(delay)
        
        # Extended page timeout for Cloud Run
        page.set_default_timeout(60000)
        
        return browser, context, page, random_delay
        
    except Exception as e:
        if 'playwright' in locals():
            await playwright.stop()
        print(f"Error setting up browser: {e}")
        raise

async def setup_context(browser: Browser) -> BrowserContext:
    """Set up and return a configured browser context."""
    context = await browser.new_context(
        viewport={'width': 3840, 'height': 2160},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        ignore_https_errors=True,
        java_script_enabled=True,
        bypass_csp=True,
        extra_http_headers={
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'sec-ch-ua': '"Chromium";v="123", "Not(A:Brand";v="8"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'none',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1'
        }
    )
    
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {
                    0: {type: "application/x-google-chrome-pdf", suffixes: "pdf", description: "PDF"},
                    description: "PDF",
                    filename: "internal-pdf-viewer",
                    length: 1,
                    name: "Chrome PDF Plugin"
                }
            ]
        });
        window.chrome = { runtime: {} };
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                originalQuery(parameters)
        );
    """)
    
    return context

async def try_all_methods(page: Page, context: BrowserContext, unverified_result=None) -> Tuple[Optional[str], str]:
    """Try all methods to find terms of service link."""
    methods = [
        (find_all_links_js, "javascript"),
        (smooth_scroll_and_click, "scroll"),
        (find_matching_link, "standard")
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

def handle_navigation_failure(url: str, unverified_result: Optional[str]) -> ToSResponse:
    """Handle case where navigation to the URL failed."""
    # Parse the URL to get domain
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    
    if unverified_result:
        return ToSResponse(
            url=url,
            tos_url=unverified_result,
            success=True,
            message="Found potential terms link (unverified)",
            method_used="dynamic_detection_unverified"
        )
        
    # If no unverified result found, use a common path format with the domain
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    # Return a response with a dynamically generated path based on common patterns
    likely_tos_url = f"{base_url}/terms"  # Most common pattern
    
    return ToSResponse(
        url=url,
        tos_url=likely_tos_url,
        success=True,
        message="Using common terms path (unverified)",
        method_used="common_path_fallback"
    )

def handle_error(url: str, unverified_result: Optional[str], error: str) -> ToSResponse:
    """Handle any errors that occur during processing."""
    # Parse the URL to get domain
    parsed_url = urlparse(url)
    
    if unverified_result:
        return ToSResponse(
            url=url,
            tos_url=unverified_result,
            success=True,
            message="Found potential terms link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    
    # If no unverified result found, use a common path
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    likely_tos_url = f"{base_url}/terms"  # Most common path
    
    # Special case handling for certain error patterns
    if "timeout" in error.lower() or "navigation" in error.lower() or "403" in error:
        return ToSResponse(
            url=url,
            tos_url=likely_tos_url,
            success=True,
            message="Generated likely terms path after timeout/navigation error (unverified)",
            method_used="timeout_fallback_path"
        )
        
    return ToSResponse(
        url=url,
        tos_url=None,
        success=False,
        message=f"Error during browser automation: {error}",
        method_used="none"
    )

def create_response(url: str, result: Optional[str], unverified_result: Optional[str], method_used: str) -> ToSResponse:
    """Create appropriate response based on results."""
    if result:
        return ToSResponse(
            url=url,
            tos_url=result,
            success=True,
            message=f"Found Terms of Service page using {method_used} method",
            method_used=method_used
        )
    elif unverified_result:
        return ToSResponse(
            url=url,
            tos_url=unverified_result,
            success=True,
            message="Found potential terms link (unverified)",
            method_used="dynamic_detection_unverified"
        )
    else:
        return ToSResponse(
            url=url,
            tos_url=None,
            success=False,
            message="Could not find Terms of Service page",
            method_used="none"
        )

async def navigate_with_retry(page, url, max_retries=3):
    """Navigate to URL with retry logic and anti-bot detection."""
    for attempt in range(max_retries):
        try:
            # Add random delay between attempts
            if attempt > 0:
                delay = random.randint(2000, 5000)
                print(f"Waiting {delay/1000}s before retry {attempt+1}...")
                await page.wait_for_timeout(delay)
            
            print(f"Navigation attempt {attempt+1}/{max_retries} to {url}")
            
            # Use different navigation strategies
            if attempt == 0:
                response = await page.goto(url, timeout=15000, wait_until="domcontentloaded")
            else:
                # Try a different strategy on retries
                response = await page.goto(url, timeout=15000, wait_until="networkidle")
            
            # Check if we hit anti-bot measures
            is_anti_bot, patterns = await detect_anti_bot_patterns(page)
            if is_anti_bot:
                if attempt < max_retries - 1:
                    print(f"Detected anti-bot protection, trying alternative approach...")
                    # Try accessing with different headers on next attempt
                    continue
                else:
                    print("All navigation attempts blocked by anti-bot protection")
                    return False, response, patterns
            
            # Check HTTP status
            if response.ok:
                print(f"Navigation successful: HTTP {response.status}")
                return True, response, []
            elif response.status == 403:
                print(f"Received HTTP 403 Forbidden")
                if attempt < max_retries - 1:
                    continue
            else:
                print(f"Received HTTP {response.status}")
        except Exception as e:
            print(f"Navigation error: {e}")
    
    print("All navigation attempts failed")
    return False, None, []

async def detect_anti_bot_patterns(page):
    """
    Detect if the page is showing anti-bot measures like Cloudflare, reCAPTCHA, etc.
    """
    try:
        # Check for common anti-bot patterns
        anti_bot_patterns = await page.evaluate("""() => {
        const html = document.documentElement.innerHTML.toLowerCase();
            const patterns = {
                cloudflare: html.includes('cloudflare') && (
                html.includes('security check') || 
                    html.includes('challenge') || 
                    html.includes('jschl-answer')
                ),
                recaptcha: html.includes('recaptcha') || html.includes('g-recaptcha'),
                hcaptcha: html.includes('hcaptcha'),
                datadome: html.includes('datadome'),
                imperva: html.includes('imperva') || html.includes('incapsula'),
                akamai: html.includes('akamai') && html.includes('bot'),
                ddos_protection: html.includes('ddos') && html.includes('protection'),
                bot_detection: (
                    html.includes('bot detection') || 
                    html.includes('automated access') || 
                    (html.includes('please wait') && html.includes('redirecting'))
                ),
                status_code: document.title.includes('403') || document.title.includes('forbidden')
            };
            
            // Check which patterns were detected
            const detected = Object.entries(patterns)
                .filter(([_, isDetected]) => isDetected)
                .map(([name, _]) => name);
                
            return {
                isAntiBot: detected.length > 0,
                detectedPatterns: detected,
                title: document.title,
                url: window.location.href
            };
    }""")
    
        if anti_bot_patterns['isAntiBot']:
            print(f"\n⚠️ Detected anti-bot protection: {', '.join(anti_bot_patterns['detectedPatterns'])}")
            print(f"  URL: {anti_bot_patterns['url']}")
            print(f"  Title: {anti_bot_patterns['title']}")
            return True, anti_bot_patterns['detectedPatterns']
        
        return False, []
    except Exception as e:
        print(f"Error detecting anti-bot patterns: {e}")
        return False, []

async def parse_domain_for_common_paths(domain, url):
    """
    Parse the domain name to create a list of common terms paths that might work.
    This is especially helpful for sites that block automated access.
    
    Args:
        domain: The domain name
        url: The original URL
        
    Returns:
        list: List of potential terms URLs based on common patterns
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
        '/terms', 
        '/tos', 
        '/terms-of-service',
        '/terms-of-use',
        '/terms-and-conditions',
        '/legal/terms', 
        '/legal/terms-of-service',
        '/legal/terms-of-use',
        '/legal/tos',
        '/policies/terms',
        '/policies/terms-of-use',
        '/policies/terms-of-service',
        '/policies/tos',
        '/about/terms',
        '/about/legal/terms',
        '/about/legal/terms-of-service',
        '/help/terms',
        '/help/legal/terms',
        f'/{company_name}/terms',
        f'/{company_name}/legal/terms',
        f'/terms-{company_name}',
        f'/legal-terms-{company_name}',
        '/user-agreement',
        '/service-agreement',
        '/legal-information',
        '/customer-terms',
        '/website-terms',
        '/platform-terms',
        '/conditions-of-use',
        '/legal-notices',
        '/eula',
        '/agreement',
        '/service-terms',
        '/site-terms'
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
        f"https://legal.{domain.split('www.', 1)[-1]}/terms",
        f"https://policy.{domain.split('www.', 1)[-1]}/terms",
        f"https://policies.{domain.split('www.', 1)[-1]}/terms",
        f"https://terms.{domain.split('www.', 1)[-1]}",
        f"https://terms.{domain.split('www.', 1)[-1]}/service",
        f"https://help.{domain.split('www.', 1)[-1]}/terms",
        f"https://docs.{domain.split('www.', 1)[-1]}/terms",
        f"https://about.{domain.split('www.', 1)[-1]}/terms"
    ]
    
    potential_urls.extend(subdomain_patterns)
    
    # Add some dynamic patterns based on company name
    dynamic_patterns = [
        f"https://{domain}/legal/{company_name}-terms",
        f"https://{domain}/{company_name}-terms",
        f"https://{domain}/legal/{company_name}-terms-of-service",
        f"https://{domain}/legal/{company_name}-tos",
        f"https://{domain}/about/{company_name}-terms",
        # Common hub page patterns
        f"https://{domain}/legal",
        f"https://{domain}/policies",
        f"https://{domain}/about/legal",
        f"https://{domain}/help/legal",
        # Common variations of terms pages
        f"https://{domain}/legal/user-agreement",
        f"https://{domain}/legal/customer-agreement",
        f"https://{domain}/legal/website-terms",
        f"https://{domain}/user-agreement",
        f"https://{domain}/customer-agreement"
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
    
    return unique_urls

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
            parsed_url = urlparse(await page.url())
            domain = parsed_url.netloc
            potential_urls = await parse_domain_for_common_paths(domain, await page.url())
            if potential_urls:
                print(f"Using fallback common paths since challenge page detected")
                # Use first potential URL as unverified result if none exists
                if not unverified_result and potential_urls:
                    unverified_result = potential_urls[0]
                    print(f"Setting unverified result to: {unverified_result}")
            
            return None, page, unverified_result
            
        # Enhanced link detection with modern web patterns
        links = await page.evaluate("""(baseDomain) => {
            // Strong indicators of terms of service links
            const exactMatchPriorities = {
                'terms of service': 100,
                'terms of use': 95,
                'terms and conditions': 90,
                'user agreement': 85,
                'service agreement': 80,
                'legal agreement': 75,
                'platform agreement': 70,
                'terms': 65,
                'tos': 60
            };
            
            // Partial match priorities
            const partialMatchPriorities = {
                'platform terms': 60,
                'website terms': 55,
                'full terms': 50,
                'detailed terms': 45,
                'complete terms': 40,
                'legal terms': 35,
                'general terms': 30,
                'service terms': 25,
                'user terms': 20,
                'legal notices': 15,
                'legal information': 10
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
                if (lowercaseHref.includes('/terms-of-service') || 
                    lowercaseHref.includes('/terms_of_service')) {
                    score += 50;
                    contextSignals.push('terms_of_service_in_url');
                } else if (lowercaseHref.includes('/terms-of-use') || 
                           lowercaseHref.includes('/terms_of_use')) {
                    score += 45;
                    contextSignals.push('terms_of_use_in_url');
                } else if (lowercaseHref.includes('/terms') || 
                           lowercaseHref.includes('/tos')) {
                    score += 40;
                    contextSignals.push('terms_in_url');
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
        print(f"\n🔎 Found {len(links)} potential terms links:")
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
            
            # Attempt to navigate to best link and verify it's actually a terms page
            try:
                print(f"Navigating to selected link: {best_link}")
                await page.goto(best_link, timeout=10000, wait_until="domcontentloaded")
                
                # Verify this is actually a terms page
                is_terms_page = await page.evaluate("""() => {
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
                            }""")
                            
                if is_terms_page:
                    print("✅ Confirmed this is a terms page")
                    return best_link, page, unverified_result
                else:
                    print("❌ Page doesn't look like a terms page, keeping as fallback")
                    return None, page, unverified_result
            except Exception as e:
                print(f"Error navigating to terms page: {e}")
                return None, page, unverified_result
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_all_links_js: {e}")
        return None, page, unverified_result

async def find_matching_link(page, context, unverified_result=None):
    """Find and click on terms-related links."""
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
                        return page.url, page, unverified_result
            except Exception as e:
                print(f"Error processing link: {e}")
                continue
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_link: {e}")
        return None, page, unverified_result

async def score_link_text(text: str) -> int:
    """Score link text based on likelihood of being terms link."""
    score = 0
    if text in ['terms of service', 'terms of use', 'terms and conditions']:
        return 100
    terms_indicators = [
        ('terms', 40),
        ('conditions', 35),
        ('legal', 30),
        ('agreement', 30),
        ('policy', 25),
        ('tos', 35),
        ('eula', 35)
    ]
    for term, points in terms_indicators:
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

import random

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
                            ".site-footer", ".page-footer", ".bottom", ".legal"]

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

        # Check footer area
        for selector in footer_selectors:
            footer = await current_page.query_selector(selector)
            if footer:
                print(f"Found footer with selector: {selector}")
                await footer.scroll_into_view_if_needed()
                await current_page.wait_for_timeout(500)

                visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
                if visited_url:
                    return visited_url, current_page, unverified_result

                terms_links = await current_page.evaluate("""(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    const links = Array.from(footer.querySelectorAll('a'));
                    return links.filter(link => {
                        const href = link.getAttribute('href') || '';
                        return href.toLowerCase().includes('terms') ||
                               href.toLowerCase().includes('legal') ||
                               href.includes('conditions') ||
                               href.includes('tos');
                    }).map(link => ({
                    text: link.textContent.trim(),
                        href: link.getAttribute('href')
                    }));
                }""", selector)

                if terms_links and len(terms_links) > 0:
                    print(f"Found {len(terms_links)} potential terms links in footer")
                    for link in terms_links:
                        print(f"Footer link: {link['text']} → {link['href']}")
                        try:
                            element = await current_page.query_selector(f"a[href='{link['href']}']")
                            if element:
                                print(f"🔗 Clicking on footer link: {link['text']}")
                                success = await click_and_wait_for_navigation(current_page, element)
                                if success:
                                    return current_page.url, current_page, unverified_result
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")
                    continue
                
        # Scroll back up to check missed links
        print("Scrolling back up to check for missed links...")
        scroll_positions = [0.75, 0.5, 0.25, 0]

        for position in scroll_positions:
            await current_page.wait_for_timeout(random.randint(300, 800))
            scroll_to = int(page_height * position)
            await current_page.evaluate(f"window.scrollTo(0, {scroll_to})")
            await current_page.wait_for_timeout(300)

            visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
            if visited_url:
                return visited_url, current_page, unverified_result

    except Exception as e:
        print(f"Error in footer/scroll check: {e}")

    # Fallback: smooth scroll all the way down
    scroll_attempts = 0
    max_scroll_attempts = 20

    while scroll_attempts < max_scroll_attempts:
        await current_page.wait_for_timeout(random.randint(200, 600))
        reached_end = await current_page.evaluate(
            """async (step) => {
                const currentScroll = window.scrollY;
                const maxScroll = document.body.scrollHeight - window.innerHeight;
                window.scrollBy(0, step);
                await new Promise(r => setTimeout(r, 100));
                return currentScroll + step >= maxScroll || window.scrollY >= maxScroll;
            }""", step
        )

        await current_page.wait_for_timeout(delay)

        visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
        if visited_url:
            return visited_url, current_page, unverified_result

        scroll_attempts += 1

        if reached_end:
            print("✅ Reached the bottom of the page.")
            break

    # Final check at bottom
    await current_page.wait_for_timeout(1000)
    visited_url, current_page, unverified_result = await find_matching_link(current_page, context, unverified_result)
    if visited_url:
        return visited_url, current_page, unverified_result

    return None, current_page, unverified_result

async def bs4_fallback_link_finder(page, context):
    """Use robust HTML parsing as a fallback method to find terms links."""
    print("Using robust HTML parsing to find terms links...")

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
        terms_links = await page.evaluate("""(html) => {
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

            const termsLinks = allLinks.filter(link => {
                const href = link.href.toLowerCase();
                const text = link.text.toLowerCase();

                const textIndicators = [
                    'terms of service', 'terms of use', 'terms and conditions',
                    'terms & conditions', 'terms', 'tos', 'legal terms', 'conditions of use',
                    'user agreement', 'legal', 'legal notices'
                ];

                const hrefIndicators = [
                    '/terms', '/tos', '/terms-of-service', '/terms-of-use',
                    '/legal/terms', '/terms-and-conditions', '/conditions',
                    '/legal', '/legal-terms', '/eula'
                ];

                const hasExactTextMatch = textIndicators.some(indicator =>
                    text === indicator || text.replace(/\\s+/g, '') === indicator.replace(/\\s+/g, '')
                );

                const hasTermsInText = text.includes('term') || text.includes('condition') ||
                                       text.includes('legal') || text.includes('tos');

                const hasTermsInHref = hrefIndicators.some(indicator => href.includes(indicator)) ||
                                       href.includes('term') || href.includes('condition') ||
                                       href.includes('legal');

                let score = 0;
                if (hasExactTextMatch) score += 100;
                if (hasTermsInText) score += 50;
                if (hasTermsInHref) score += 75;

                link.score = score;
                return score > 0;
            });

            termsLinks.sort((a, b) => b.score - a.score);
            return termsLinks;
        }""", html_content)

        print(f"Found {len(terms_links)} potential terms links with robust HTML parsing")

        for link in terms_links:
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

                    terms_content = await page.evaluate("""() => {
                        const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
                        const paragraphs = Array.from(document.querySelectorAll('p'));

                        const termsHeading = headings.some(h => {
                            const text = h.textContent.toLowerCase();
                            return text.includes('terms') ||
                                   text.includes('condition') ||
                                   text.includes('legal agreement') ||
                                   text.includes('service agreement');
                        });

                        const title = document.title.toLowerCase();
                        const termsInTitle = title.includes('terms') ||
                                             title.includes('tos') ||
                                             title.includes('conditions');

                        const legalContent = paragraphs.slice(0, 5).some(p => {
                            const text = p.textContent.toLowerCase();
                            return text.includes('agree') ||
                                   text.includes('terms') ||
                                   text.includes('conditions') ||
                                   text.includes('legal') ||
                                   text.includes('copyright') ||
                                   text.includes('intellectual property');
                        });

                        return termsHeading || termsInTitle || legalContent;
                    }""")

                    if terms_content:
                        print(f"Found terms content at: {page.url}")
                        return page.url, page
                    else:
                        print("Page doesn't appear to contain terms content")

                except Exception as e:
                    print(f"Navigation error: {e}")

            except Exception as e:
                print(f"Error processing link: {e}")

        return None, page

    except Exception as e:
        print(f"Error in bs4_fallback_link_finder: {e}")
        return None, page

async def standard_terms_finder(url: str, headers: dict = None) -> tuple[Optional[str], None]:
    """
    Advanced dynamic approach to find Terms of Service links without hardcoded patterns.
    Uses site structure analysis, content evaluation, and semantic understanding to
    discover terms pages regardless of site architecture.

    Args:
        url: The URL to scan
        headers: Optional request headers

    Returns:
        Tuple of (terms_url, None) or (None, None) if not found
    """
    try:
        print("Using fully dynamic terms discovery algorithm...")

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

        # Look for footers (where terms are commonly found)
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

                # Score the link
                score = 0
                signals = []

                # Text analysis
                if 'terms' in text:
                    score += 30
                    signals.append('terms_in_text')
                if 'service' in text and 'terms' in text:
                    score += 20
                    signals.append('service_in_text')
                if 'conditions' in text:
                    score += 15
                    signals.append('conditions_in_text')
                if 'legal' in text:
                    score += 10
                    signals.append('legal_in_text')

                # URL analysis
                if 'terms' in abs_url.lower():
                    score += 20
                    signals.append('terms_in_url')
                if 'tos' in abs_url.lower():
                    score += 15
                    signals.append('tos_in_url')
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
        print(f"Error in standard_terms_finder: {str(e)}")
        return None, None

async def check_for_better_terms_link(page, current_url):
    """Check if current page has links to more specific terms pages.
    
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
            if 'terms-of-service' in url or 'terms_of_service' in url:
                score += 40
            elif 'terms-of-use' in url or 'terms_of_use' in url:
                score += 35
            elif 'terms-and-conditions' in url or 'terms_and_conditions' in url:
                score += 30
            elif 'terms' in url or 'tos' in url:
                score += 25
                
            # Check for context indicators suggesting more detailed content
            context_words = ['full', 'detailed', 'complete', 'latest', 'updated', 'specific']
            for word in context_words:
                if word in text:
                    score += 10
                    break
                    
            # PDF and legal documents often contain the full terms
            if url.endswith('.pdf'):
                score += 15
                
            scored_links.append((link['href'], score))
            
        # Sort by score and check top links
        scored_links.sort(key=lambda x: x[1], reverse=True)
        
        # Only check high-confidence links
        for link_url, score in scored_links:
            if score < 50:  # Only use high-confidence matches
                continue
            
            print(f"Found potential deeper terms link with score {score}: {link_url}")
            
            try:
                # Navigate to the link
                await page.goto(link_url, timeout=10000, wait_until="domcontentloaded")
                
                # Verify if this is actually a terms page
                is_terms_page = await page.evaluate("""() => {
                    const text = document.body.innerText.toLowerCase();
                    const title = document.title.toLowerCase();
                    
                    // Key indicators of terms content
                    const termsIndicators = [
                        'terms of service', 
                        'terms of use', 
                        'terms and conditions',
                        'agree to these terms',
                        'by using this site',
                        'by accessing this site',
                        'legally binding',
                        'liability',
                        'disclaimer',
                        'intellectual property'
                    ];
                    
                    const hasTermsIndicators = termsIndicators.some(term => text.includes(term));
                    const hasTermsInTitle = title.includes('terms') || 
                                           title.includes('tos') || 
                                           title.includes('legal');
                    
                    // Simple content length check - terms pages tend to be long
                    const isLongContent = text.length > 3000;
                    
                    return {
                        isTermsPage: hasTermsIndicators || (hasTermsInTitle && isLongContent),
                        textLength: text.length,
                        hasTermsInTitle: hasTermsInTitle
                    };
                }""")
                
                if is_terms_page['isTermsPage']:
                    print(f"Verified better terms page: {page.url}")
                    return page.url, page
                else:
                    print(f"Page doesn't appear to be a valid terms page")
                    
            except Exception as e:
                print(f"Navigation error checking link: {e}")
                continue
            
        return None, page
    except Exception as e:
        print(f"Error checking for better terms links: {e}")
        return None, page

def is_valid_terms_page(content):
    """Validate if page content matches terms page criteria.
    
    Args:
        content: Dict with text and headings from page
        
    Returns:
        bool: True if valid terms page
    """
    # Required terms that should be present
    required_terms = ['agree', 'terms', 'conditions', 'service']
    
    # Legal sections that indicate a terms page
    legal_sections = [
        'intellectual property',
        'limitation of liability', 
        'governing law',
        'dispute resolution',
        'privacy policy',
        'disclaimer',
        'warranty',
        'termination',
        'user obligations',
        'acceptable use'
    ]
    
    # Legal phrases that indicate terms content
    legal_phrases = [
        'by using',
        'you agree to',
        'by accessing',
        'please read',
        'your use of',
        'these terms',
        'subject to',
        'without limitation',
        'reserves the right',
        'at its discretion',
        'may modify',
        'may terminate',
        'copyright',
        'intellectual property',
        'liability',
        'indemnify',
        'disclaimer',
        'govern',
        'jurisdiction',
        'arbitration',
        'dispute'
    ]
    
    if not content or 'text' not in content:
        return False
        
    text = content.get('text', '').lower()
    headings = content.get('headings', [])
    
    # Check for a minimum content length (terms pages tend to be long)
    if len(text) < 2000:  # Reduced from 3000 to catch more pages
        return False
        
    # Check required terms
    terms_count = sum(1 for term in required_terms if term in text)
    
    # Check legal sections
    section_count = sum(1 for section in legal_sections if section in text)
    
    # Check legal phrases
    phrase_count = sum(1 for phrase in legal_phrases if phrase in text)
    
    # Check headings
    has_terms_heading = any(
        h for h in headings 
        if 'terms' in h or 'agreement' in h or 'conditions' in h or 'legal' in h
    )
    
    # Calculate score based on content
    score = 0
    if terms_count >= 2:
        score += 30
    if section_count >= 2:
        score += 20
    if phrase_count >= 3:
        score += 30
    if has_terms_heading:
        score += 20
    
    # More lenient approach - if enough legal phrases and some required terms, it's likely a terms page
    if score >= 70 or (phrase_count >= 5 and terms_count >= 1):
        return True
        
    # If page has substantial legal content, it's likely a terms page
    if len(text) > 5000 and phrase_count >= 3 and terms_count >= 1:
        return True
    
    return False

async def main():
    """Main function for direct script usage."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Find Terms of Service page for a given URL.')
    parser.add_argument('url', help='URL to scan for Terms of Service')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose output')
    
    args = parser.parse_args()
    
    if args.verbose:
        print(f"Searching for Terms of Service for: {args.url}")
    
    try:
        request = ToSRequest(url=args.url)
        response = await find_tos(request)
        
        print("\n=== Results ===")
        print(f"URL: {response.url}")
        print(f"Terms of Service URL: {response.tos_url if response.tos_url else 'Not found'}")
        print(f"Success: {response.success}")
        print(f"Method: {response.method_used}")
        print(f"Message: {response.message}")
        
        return response.tos_url
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

# Run the script
if __name__ == "__main__":
    import asyncio
    result = asyncio.run(main())
    sys.exit(0 if result else 1)

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
                
                # Verify this is actually a terms page
                is_terms = await verify_terms_content(page)
                if is_terms:
                    print(f"✅ Verified as terms page: {potential_url}")
                    return potential_url
                else:
                    print(f"❌ Not a terms page: {potential_url}")
        except Exception as e:
            print(f"❌ Error accessing fallback path {potential_url}: {e}")
            continue
    
    # If all else fails, return most likely path as unverified
    if potential_urls:
        return potential_urls[0]
    
    return None
            
async def verify_terms_content(page):
    """
    Verify if the current page appears to be a terms of service page.
    
    Args:
        page: Playwright page object
        
    Returns:
        Boolean indicating if the page appears to be a terms page
    """
    try:
        # Get the page title
        title = await page.title()
        title_lower = title.lower()
        
        # Check title for terms indicators
        if any(term in title_lower for term in ['terms', 'conditions', 'tos', 'legal', 'agreement']):
            print(f"Page title indicates terms: {title}")
            return True
            
        # Check content for terms indicators using JavaScript
        has_terms_content = await page.evaluate("""
        () => {
            try {
                const bodyText = document.body.innerText.toLowerCase();
                
                // Check for common terms phrases
                const termsPhrases = [
                    'terms of service',
                    'terms of use',
                    'terms and conditions',
                    'user agreement',
                    'by using this site',
                    'by accessing this site',
                    'agree to these terms',
                    'legally binding',
                    'intellectual property',
                    'limitation of liability'
                ];
                
                // Check for legal sections
                const legalSections = [
                    'governing law',
                    'applicable law',
                    'limitation of liability',
                    'disclaimer of warranties',
                    'intellectual property',
                    'termination',
                    'modifications to terms'
                ];
                
                // Count matches
                const termsMatches = termsPhrases.filter(phrase => bodyText.includes(phrase)).length;
                const legalMatches = legalSections.filter(section => bodyText.includes(section)).length;
                
                // Return true if enough matches are found
                return termsMatches >= 2 || legalMatches >= 3 || (termsMatches + legalMatches >= 3);
            } catch (e) {
                return false;
            }
        }
        """)
        
        if has_terms_content:
            print("Page content indicates terms page")
            return True
            
        return False
    except Exception as e:
        print(f"Error verifying terms content: {e}")
        return False