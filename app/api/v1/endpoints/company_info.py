from fastapi import APIRouter, Response, HTTPException, status
import logging
import requests
import asyncio
import random
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import re
from typing import Optional, Tuple, Dict, Any
from urllib.parse import urlparse, urljoin
from playwright.async_api import async_playwright
import string
import warnings
import time
import aiohttp
import tldextract

from app.models.company_info import CompanyInfoRequest, CompanyInfoResponse

# Suppress XML parsed-as-HTML warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Define consistent user agent
CONSISTENT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

def get_base_url(url: str) -> str:
    """Extract the base URL from a given URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

def sanitize_url(url: str) -> str:
    """
    Sanitize and validate URLs to ensure they are valid.
    
    If the URL is severely malformed or clearly invalid, returns an empty string
    instead of attempting to fix it.
    """
    if not url:
        logger.warning("Empty URL provided")
        return ""
        
    # Trim whitespace and control characters
    url = url.strip().strip('\r\n\t')
    
    # Log the original URL for debugging
    logger.info(f"Validating URL: {url}")
    
    try:
        # Fix only the most common minor issues
        # Add protocol if missing
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        
        # Validate the URL structure
        parsed = urlparse(url)
        
        # Check for severely malformed URLs
        if not parsed.netloc or '.' not in parsed.netloc:
            logger.warning(f"Invalid domain in URL: {url}")
            return ""
            
        # Check for nonsensical URL patterns that indicate a malformed URL
        if re.match(r'https?://[a-z]+s?://', url):
            # Invalid patterns like https://ttps://
            logger.warning(f"Malformed URL with invalid protocol pattern: {url}")
            return ""
            
        # Additional validation to ensure domain has a valid TLD
        domain_parts = parsed.netloc.split('.')
        if len(domain_parts) < 2 or len(domain_parts[-1]) < 2:
            logger.warning(f"Domain lacks valid TLD: {url}")
            return ""
            
        logger.info(f"URL validated: {url}")
        return url
    except Exception as e:
        logger.error(f"Error validating URL {url}: {str(e)}")
        return ""

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

def is_app_store_url(url: str) -> bool:
    """Check if the URL is from Apple App Store."""
    return "apps.apple.com" in url or "itunes.apple.com" in url

def is_play_store_url(url: str) -> bool:
    """Check if the URL is from Google Play Store."""
    return "play.google.com" in url

async def extract_app_store_info(page) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract app name and logo URL from Apple App Store page.
    
    Args:
        page: Playwright page object
        
    Returns:
        Tuple of (app_name, logo_url)
    """
    try:
        logger.info("Extracting info from App Store page")
        
        # Extract app name from title element
        app_name = await page.evaluate("""
            () => {
                // Try the product header title first (most reliable)
                const titleEl = document.querySelector('.product-header__title, .app-header__title');
                if (titleEl) {
                    // Get only the text content without any child elements (like badges)
                    let title = '';
                    for (const node of titleEl.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            title += node.textContent;
                        }
                    }
                    return title.trim();
                }
                
                // Try dedicated h1 with app name
                const h1 = document.querySelector('h1.product-header__title');
                if (h1) {
                    let title = '';
                    for (const node of h1.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            title += node.textContent;
                        }
                    }
                    return title.trim();
                }
                
                // Fallbacks
                // 1. Try meta title
                const metaTitle = document.querySelector('meta[name="apple:title"]');
                if (metaTitle && metaTitle.content) {
                    return metaTitle.content.trim();
                }
                
                // 2. Try open graph title
                const ogTitle = document.querySelector('meta[property="og:title"]');
                if (ogTitle && ogTitle.content) {
                    const title = ogTitle.content.replace(' on the App Store', '');
                    return title.trim();
                }
                
                // 3. Use page title
                if (document.title) {
                    const title = document.title.replace(' on the App Store', '');
                    return title.trim();
                }
                
                return null;
            }
        """)
        
        # Make sure we're not returning just "Apps" as the name
        if app_name == "Apps":
            app_name = await page.evaluate("""
                () => {
                    // Try to find a better app name if we got "Apps"
                    
                    // Check if there's an app name in the URL path
                    const pathParts = window.location.pathname.split('/');
                    // App name is usually after /app/ in the URL
                    const appNameIndex = pathParts.indexOf('app');
                    if (appNameIndex >= 0 && appNameIndex < pathParts.length - 1) {
                        // Convert app-name-with-dashes to App Name With Dashes
                        const nameFromUrl = pathParts[appNameIndex + 1]
                            .split('-')
                            .map(word => word.charAt(0).toUpperCase() + word.slice(1))
                            .join(' ');
                        if (nameFromUrl && nameFromUrl.length > 1) {
                            return nameFromUrl;
                        }
                    }
                    
                    // Try other metadata elements
                    const developerInfo = document.querySelector('.product-header__identity');
                    if (developerInfo) {
                        return developerInfo.textContent.trim();
                    }
                    
                    // Try app subtitle
                    const subtitle = document.querySelector('.product-header__subtitle');
                    if (subtitle) {
                        return subtitle.textContent.trim();
                    }
                    
                    return document.title.replace(' on the App Store', '').trim();
                }
            """)
        
        # Extract logo URL
        logo_url = await page.evaluate("""
            () => {
                // Try multiple selectors for app icon
                const selectors = [
                    // Primary selectors for app icon
                    '.we-artwork--ios-app-icon source[srcset]',
                    '.product-hero__artwork source[srcset]',
                    '.we-artwork--ios-app-icon img[src]',
                    '.product-hero__artwork img[src]',
                    
                    // Fallbacks
                    'picture.we-artwork--ios-app-icon source',
                    'meta[property="og:image"]',
                    'link[rel="apple-touch-icon"]',
                    '.ember-view .we-artwork--downloaded img'
                ];
                
                for (const selector of selectors) {
                    const elements = document.querySelectorAll(selector);
                    for (const el of elements) {
                        // Handle srcset (preferred for high quality)
                        if (el.srcset) {
                            // Parse srcset to get the largest image
                            const srcsetParts = el.srcset.split(',').map(s => s.trim());
                            if (srcsetParts.length > 0) {
                                // Find the image with the largest width
                                let largestWidth = 0;
                                let largestUrl = '';
                                
                                for (const part of srcsetParts) {
                                    const [url, widthStr] = part.split(' ');
                                    if (url && widthStr) {
                                        const width = parseInt(widthStr.replace('w', ''));
                                        if (width > largestWidth) {
                                            largestWidth = width;
                                            largestUrl = url;
                                        }
                                    }
                                }
                                
                                if (largestUrl) {
                                    return largestUrl;
                                }
                                
                                // If we couldn't parse widths, just use the last image
                                const lastSrc = srcsetParts[srcsetParts.length - 1];
                                return lastSrc.split(' ')[0]; // Get URL part only
                            }
                        }
                        
                        // Try src for images
                        if (el.src && el.src !== '/assets/artwork/1x1-42817eea7ade52607a760cbee00d1495.gif') {
                            return el.src;
                        }
                        
                        // Try content for meta tags
                        if (el.getAttribute('content')) {
                            return el.getAttribute('content');
                        }
                        
                        // Try href for link tags
                        if (el.getAttribute('href')) {
                            return el.getAttribute('href');
                        }
                    }
                }
                
                return null;
            }
        """)
        
        logger.info(f"Extracted App Store info - Name: {app_name}, Logo: {logo_url}")
        return app_name, logo_url
    except Exception as e:
        logger.error(f"Error extracting App Store info: {e}")
        return None, None

async def extract_play_store_info(page) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract app name and logo URL from Google Play Store page.
    
    Args:
        page: Playwright page object
        
    Returns:
        Tuple of (app_name, logo_url)
    """
    try:
        logger.info("Extracting info from Play Store page")
        
        # Extract app name
        app_name = await page.evaluate("""
            () => {
                // Try app name from header
                const titleEl = document.querySelector('h1[itemprop="name"]');
                if (titleEl) {
                    return titleEl.textContent.trim();
                }
                
                // Fallback to generic h1
                const h1 = document.querySelector('h1');
                if (h1) {
                    return h1.textContent.trim();
                }
                
                // Try meta title
                const metaTitle = document.querySelector('meta[name="title"]');
                if (metaTitle && metaTitle.content) {
                    return metaTitle.content.replace(' - Apps on Google Play', '').trim();
                }
                
                // Try open graph title
                const ogTitle = document.querySelector('meta[property="og:title"]');
                if (ogTitle && ogTitle.content) {
                    return ogTitle.content.replace(' - Apps on Google Play', '').trim();
                }
                
                // Use page title
                if (document.title) {
                    const title = document.title.replace(' - Apps on Google Play', '');
                    return title.trim();
                }
                
                return null;
            }
        """)
        
        # Extract logo URL
        logo_url = await page.evaluate("""
            () => {
                // Try app icon selectors
                const selectors = [
                    'img[itemprop="image"]',
                    '.Mqg6jd img',  // Common Play Store app icon class
                    '.T75of img',   // Alternative icon class
                    'meta[property="og:image"]',
                    '.hkhL9e img', // Another common class
                    'img.R1d3vc'    // Yet another icon class
                ];
                
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (el) {
                        if (el.src) {
                            return el.src;
                        }
                        if (el.getAttribute('content')) {
                            return el.getAttribute('content');
                        }
                    }
                }
                
                return null;
            }
        """)
        
        logger.info(f"Extracted Play Store info - Name: {app_name}, Logo: {logo_url}")
        return app_name, logo_url
    except Exception as e:
        logger.error(f"Error extracting Play Store info: {e}")
        return None, None

def get_random_user_agent():
    """
    Returns a consistent user agent string.
    """
    return CONSISTENT_USER_AGENT

async def setup_stealth_browser():
    """Setup Playwright browser with anti-detection measures"""
    playwright = await async_playwright().start()
    
    # Launch with realistic viewport and headless mode for production environment
    browser = await playwright.chromium.launch(
        headless=True,  # Changed from False to True for stability
    )
    
    # Create a context with realistic viewport and device settings
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        device_scale_factor=1,
        user_agent=CONSISTENT_USER_AGENT,
        is_mobile=False
    )
    
    # Add stealth script to avoid detection
    await context.add_init_script("""
        () => {
            // Override WebDriver property to avoid detection
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            
            // Override plugins to look like a real browser
            Object.defineProperty(navigator, 'plugins', { 
                get: () => [
                    { description: "Portable Document Format", filename: "internal-pdf-viewer", name: "Chrome PDF Plugin" },
                    { description: "", filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai", name: "Chrome PDF Viewer" },
                    { description: "", filename: "internal-nacl-plugin", name: "Native Client" }
                ]
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            
            // Override hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            
            // Fake battery API
            if (typeof navigator.getBattery === 'function') {
                navigator.getBattery = () => Promise.resolve({
                    charging: true,
                    chargingTime: 0,
                    dischargingTime: Infinity,
                    level: 1,
                });
            }
        }
    """)
    
    # Create a page and set various properties
    page = await context.new_page()
    await page.set_extra_http_headers({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br"
    })
    
    return playwright, browser, context, page

async def extract_with_playwright(url: str) -> Tuple[str, str, bool, str]:
    """
    Extract company info using Playwright as a fallback
    Returns (company_name, logo_url, success, message)
    """
    playwright = None
    browser = None
    context = None
    
    try:
        domain = urlparse(url).netloc
        if domain.startswith('www.'):
            domain = domain[4:]
        default_company_name = domain.split('.')[0].capitalize()
        
        # Set up browser with anti-detection
        playwright, browser, context, page = await setup_stealth_browser()
        
        # Add random delay before navigation
        await asyncio.sleep(random.uniform(0.5, 1.5))
        
        # Navigate to page
        logger.info(f"Navigating to {url} with Playwright")
        response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        
        if not response.ok:
            logger.warning(f"Failed to load page with status: {response.status}")
            return default_company_name, "/placeholder.svg?height=48&width=48", False, f"Failed to load page: HTTP {response.status}"
        
        # Check if this is an App/Play Store page
        if is_app_store_url(url):
            app_name, app_logo = await extract_app_store_info(page)
            if app_name and app_logo:
                return app_name.strip(), app_logo, True, "Successfully extracted app info from App Store"
        elif is_play_store_url(url):
            app_name, app_logo = await extract_play_store_info(page)
            if app_name and app_logo:
                return app_name.strip(), app_logo, True, "Successfully extracted app info from Play Store"
        
        # Add human-like interaction - scroll down smoothly
        await page.evaluate("""
            () => {
                const randomScrollStop = () => Math.random() < 0.15;
                const totalScrolls = Math.floor(Math.random() * 5) + 5;
                
                return new Promise((resolve) => {
                    let scrolls = 0;
                    const scroll = () => {
                        if (scrolls >= totalScrolls) {
                            resolve();
                            return;
                        }
                        
                        scrolls++;
                        const scrollAmount = Math.floor(Math.random() * 400) + 100;
                        window.scrollBy(0, scrollAmount);
                        
                        if (randomScrollStop()) {
                            // Sometimes pause scrolling like a human would
                            setTimeout(() => {
                                setTimeout(scroll, Math.random() * 500 + 400);
                            }, Math.random() * 1000 + 500);
                        } else {
                            setTimeout(scroll, Math.random() * 500 + 100);
                        }
                    };
                    
                    scroll();
                });
            }
        """)
        
        # Extract company name from title
        title = await page.title()
        company_name = default_company_name
        
        if title:
            # Clean up title (remove common suffixes)
            common_suffixes = [
                " - Home", " | Home", " - Official Website", " | Official Website",
                " - Official Site", " | Official Site"
            ]
            for suffix in common_suffixes:
                if title.endswith(suffix):
                    title = title[:-len(suffix)]
            
            # If domain name is in title, use it
            domain_name = domain.split('.')[0].lower()
            if domain_name in title.lower():
                company_name = title.strip()
            else:
                # Otherwise, use cleaned title
                company_name = title.strip()[:50]
        
        # Extract logo using sophisticated JavaScript
        logo_url = await page.evaluate("""
            () => {
                // Try multiple methods to find a logo
                const findLogo = () => {
                    // Check meta tags first
                    const metaTags = document.querySelectorAll('meta[property*="logo"], meta[name*="logo"]');
                    for (const tag of metaTags) {
                        if (tag.content) return tag.content;
                    }
                    
                    // Check OpenGraph image
                    const ogImage = document.querySelector('meta[property="og:image"]');
                    if (ogImage && ogImage.content) return ogImage.content;
                    
                    // Check structured data
                    const jsonlds = document.querySelectorAll('script[type="application/ld+json"]');
                    for (const script of jsonlds) {
                        try {
                            const data = JSON.parse(script.textContent);
                            if (data.logo) return typeof data.logo === 'string' ? data.logo : data.logo.url;
                            if (data.organization && data.organization.logo) 
                                return typeof data.organization.logo === 'string' ? data.organization.logo : data.organization.logo.url;
                        } catch (e) {
                            // Skip invalid JSON
                        }
                    }
                    
                    // Find by common selectors with scoring
                    const logoSelectors = [
                        'img.logo', '.logo img', 'img#logo', '#logo img',
                        'img.brand-logo', '.brand-logo img', 
                        'img.brand', '.brand img', 
                        'header img', '.header img', 
                        '.navbar-brand img', '.site-logo img',
                        'a[href="/"] img', 'a[href="./"] img'
                    ];
                    
                    // Score each element
                    const candidates = [];
                    for (const selector of logoSelectors) {
                        const elements = document.querySelectorAll(selector);
                        for (const el of elements) {
                            if (!el.src) continue;
                            
                            // Skip tiny images (likely icons)
                            if (el.width > 0 && el.width < 10) continue;
                            if (el.height > 0 && el.height < 10) continue;
                            
                            // Score by position (top of page = better)
                            const rect = el.getBoundingClientRect();
                            const verticalScore = Math.max(0, 1000 - rect.top);
                            
                            // Score by attributes
                            let attrScore = 0;
                            if (el.alt && /logo|brand|company/i.test(el.alt)) attrScore += 50;
                            if (el.id && /logo|brand|company/i.test(el.id)) attrScore += 30;
                            if (el.src && /logo|brand|company/i.test(el.src)) attrScore += 20;
                            
                            // Score image size (prefer square or landscape)
                            const aspectScore = (el.width / el.height >= 0.5 && el.width / el.height <= 3) ? 30 : 0;
                            
                            // Final score
                            candidates.push({
                                src: el.src,
                                score: verticalScore + attrScore + aspectScore
                            });
                        }
                    }
                    
                    // Return highest scoring image
                    if (candidates.length > 0) {
                        candidates.sort((a, b) => b.score - a.score);
                        return candidates[0].src;
                    }
                    
                    // Last resort: favicon
                    const favicon = document.querySelector('link[rel*="icon"]');
                    if (favicon && favicon.href) return favicon.href;
                    
                    return null;
                };
                
                return findLogo();
            }
        """)
        
        # If no logo found, use favicon from Google service
        if not logo_url:
            logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
        
        # Make sure the logo URL is absolute
        if logo_url and not logo_url.startswith(('http://', 'https://')):
            base_url = get_base_url(url)
            logo_url = urljoin(base_url, logo_url)
        
        # Verify logo URL is valid
        try:
            # Use simple HEAD request to check logo
            verify_response = await page.request.head(logo_url, timeout=5000)
            if not verify_response.ok:
                logger.warning(f"Logo URL validation failed: {verify_response.status}")
                logo_url = "/placeholder.svg?height=48&width=48"
        except Exception as e:
            logger.warning(f"Error validating logo URL: {e}")
            logo_url = "/placeholder.svg?height=48&width=48"
        
        return company_name, logo_url, True, "Successfully extracted company information with Playwright"
    
    except Exception as e:
        logger.error(f"Error extracting with Playwright: {e}")
        # Always extract a company name from the domain
        try:
            domain = urlparse(url).netloc if url else ""
            if domain:
                return extract_company_name_from_domain(domain), "/placeholder.svg?height=48&width=48", False, f"Playwright extraction error: {str(e)}"
            else:
                # If no domain can be extracted, use the URL directly
                return url.capitalize(), "/placeholder.svg?height=48&width=48", False, f"Playwright extraction error: {str(e)}"
        except:
            # Final fallback - extract something usable from the URL
            return url.split('/')[-1].capitalize(), "/placeholder.svg?height=48&width=48", False, f"Playwright extraction error: {str(e)}"
    
    finally:
        # Clean up Playwright resources
        if context:
            await context.close()
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()

async def extract_company_info(url: str) -> tuple:
    """Extract company name and logo URL from a website, with robust fallbacks."""
    
    logger.info(f"Attempting to extract company information from URL: {url}")
    
    # Define default values early
    company_name = "Unknown"
    logo_url = "/placeholder.svg?height=48&width=48"
    success = False
    message = "Extraction failed"
    domain = ""
    
    # Try using standard HTTP request and BS4 first
    try:
        # Validate and normalize URL
        url = sanitize_url(url)
        if not url:
            return "Unknown", "/placeholder.svg?height=48&width=48", False, "Invalid URL"
        
        # Extract domain after validation
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            if domain:
                company_name = extract_company_name_from_domain(domain) # Initial name from domain
            else:
                company_name = url.split('/')[0].capitalize() if '/' in url else url.capitalize()
        except Exception as parse_err:
            logger.error(f"Error parsing domain from URL '{url}': {parse_err}")
            company_name = url.split('/')[0].capitalize() if '/' in url else url.capitalize()

        # Check if App Store or Play Store URL
        if is_app_store_url(url) or is_play_store_url(url):
            logger.info(f"Detected app store URL: {url}")
            company_name, logo_url, success, message = await extract_with_playwright(url)
            
            if success and company_name and logo_url:
                return company_name, logo_url, success, message
        
        # Attempt extraction
        try:
            headers = {"User-Agent": get_random_user_agent()}
            response = requests.get(url, headers=headers, timeout=10)
            if not response.ok:
                logger.warning(f"HTTP request failed with status: {response.status_code}")
                logger.info("Falling back to Playwright extraction")
                return await extract_with_playwright(url)
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            base_url = get_base_url(url)
            
            # 1. Try to extract company name from title tag
            if soup.title and soup.title.string:
                title_text = soup.title.string.strip()
                # Clean up title (remove common suffixes like "- Home", "| Official Website", etc.)
                common_suffixes = [
                    " - Home", " | Home", " - Official Website", " | Official Website",
                    " - Official Site", " | Official Site"
                ]
                for suffix in common_suffixes:
                    if title_text.endswith(suffix):
                        title_text = title_text[:-len(suffix)]
                
                # Remove domain name if it appears in title
                domain_parts = domain.split('.')
                if len(domain_parts) > 1:
                    domain_name = domain_parts[0].lower()
                    if domain_name in title_text.lower():
                        company_name = title_text.strip()
                    else:
                        # If no domain match, use the title but limit length
                        company_name = title_text[:50].strip()
            
            # Expand logo detection: also check header and organization schema
            # Look for schema.org Organization logo first (most accurate)
            logo_found = False
            schema_tags = soup.find_all('script', type='application/ld+json')
            for tag in schema_tags:
                try:
                    import json
                    data = json.loads(tag.string)
                    # Check for Organization schema
                    if isinstance(data, dict):
                        # Direct logo property
                        if 'logo' in data:
                            if isinstance(data['logo'], str):
                                logo_url = urljoin(base_url, data['logo'])
                                logo_found = True
                                break
                            elif isinstance(data['logo'], dict) and 'url' in data['logo']:
                                logo_url = urljoin(base_url, data['logo']['url'])
                                logo_found = True
                                break
                        # Logo inside Organization property
                        elif '@type' in data and data['@type'] == 'Organization' and 'logo' in data:
                            if isinstance(data['logo'], str):
                                logo_url = urljoin(base_url, data['logo'])
                                logo_found = True
                                break
                            elif isinstance(data['logo'], dict) and 'url' in data['logo']:
                                logo_url = urljoin(base_url, data['logo']['url'])
                                logo_found = True
                                break
                        # Logo inside nested Organization
                        elif 'organization' in data and isinstance(data['organization'], dict) and 'logo' in data['organization']:
                            org_logo = data['organization']['logo']
                            if isinstance(org_logo, str):
                                logo_url = urljoin(base_url, org_logo)
                                logo_found = True
                                break
                            elif isinstance(org_logo, dict) and 'url' in org_logo:
                                logo_url = urljoin(base_url, org_logo['url'])
                                logo_found = True
                                break
                except Exception as e:
                    logger.warning(f"Error parsing JSON-LD: {e}")
                    continue
            
            # Look for meta tags with 'logo' in the property/name
            if not logo_found:
                meta_logo_tags = soup.find_all('meta', attrs={'property': lambda x: x and 'logo' in x.lower() if x else False})
                if not meta_logo_tags:
                    meta_logo_tags = soup.find_all('meta', attrs={'name': lambda x: x and 'logo' in x.lower() if x else False})
                
                if meta_logo_tags:
                    for tag in meta_logo_tags:
                        if tag.get('content'):
                            logo_url = urljoin(base_url, tag['content'])
                            logo_found = True
                            break
            
            # Look for OpenGraph image
            if not logo_found:
                og_image = soup.find('meta', property='og:image')
                if og_image and og_image.get('content'):
                    logo_url = urljoin(base_url, og_image['content'])
                    logo_found = True
            
            # Look for typical logos in the header or navigation
            if not logo_found:
                # Find the header or nav element
                header = soup.find(['header', 'nav', 'div.header', 'div.nav', 'div.navbar'])
                if header:
                    # Look for img in the header with logo-like attributes
                    logo_candidates = header.select('img[src]')
                    for img in logo_candidates:
                        # Check if it looks like a logo based on attributes
                        alt_text = img.get('alt', '').lower()
                        class_attr = ' '.join(img.get('class', [])).lower()
                        img_id = img.get('id', '').lower()
                        
                        is_likely_logo = (
                            'logo' in alt_text or 'brand' in alt_text or
                            'logo' in class_attr or 'brand' in class_attr or
                            'logo' in img_id or 'brand' in img_id
                        )
                        
                        if is_likely_logo:
                            logo_url = urljoin(base_url, img['src'])
                            logo_found = True
                            break
            
            # Look for common logo class/id patterns
            if not logo_found:
                logo_selectors = [
                    'img.logo', 'img#logo', '.logo img', '#logo img',
                    'img.brand', 'img#brand', '.brand img', '#brand img',
                    'img.site-logo', 'img#site-logo', '.site-logo img', '#site-logo img',
                    'header img', '.header img', '#header img',
                    '.navbar-brand img', '.brand-logo img',
                    'a[href="/"] img', 'a[href="./"] img'  # Logo often links to homepage
                ]
                
                for selector in logo_selectors:
                    try:
                        logo_img = soup.select_one(selector)
                        if logo_img and logo_img.get('src'):
                            logo_url = urljoin(base_url, logo_img['src'])
                            logo_found = True
                            break
                    except Exception as e:
                        logger.warning(f"Error with selector {selector}: {e}")
                        continue
            
            # Fall back to favicon if nothing else worked
            if not logo_found:
                # Check for link rel="icon" or rel="shortcut icon"
                favicon_link = soup.find('link', rel=lambda r: r and ('icon' in r.lower() if r else False))
                if favicon_link and favicon_link.get('href'):
                    logo_url = urljoin(base_url, favicon_link['href'])
                    logo_found = True
                else:
                    # Use Google's favicon service as last resort
                    logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                    logo_found = True
            
            success = True
            message = "Successfully extracted company information with BeautifulSoup"
            
            # Verify logo URL is valid
            if logo_found:
                try:
                    # Don't download the image, just check if it exists with a HEAD request
                    logo_test = requests.head(logo_url, timeout=5)
                    if logo_test.status_code >= 400:
                        logger.warning(f"Logo URL returned error: {logo_test.status_code}")
                        logo_url = "/placeholder.svg?height=48&width=48"
                except Exception as e:
                    logger.warning(f"Error verifying logo URL: {e}")
                    logo_url = "/placeholder.svg?height=48&width=48"
            
            return company_name, logo_url, success, message
        
        except Exception as e:
            logger.warning(f"BeautifulSoup extraction failed: {e}. Falling back to Playwright.")
            # Fall back to Playwright for JavaScript-rendered sites
            return await extract_with_playwright(url)
    
    except Exception as e:
        logger.error(f"Error extracting company info: {e}")
        
        # Even in error case, extract company name from domain if possible
        if domain:
            try:
                company_name = extract_company_name_from_domain(domain)
                logger.info(f"Fallback: Extracted company name '{company_name}' from domain '{domain}'")
            except Exception as inner_e:
                logger.error(f"Error in fallback company name extraction from domain '{domain}': {str(inner_e)}")
                # If domain extraction fails, use URL based fallback
                company_name = url.split('/')[0].capitalize() if '/' in url else url.capitalize()
        else:
            # If domain was never set, use URL based fallback
            company_name = url.split('/')[0].capitalize() if '/' in url else url.capitalize()
            logger.info(f"Final fallback (no domain): Using URL part as company name: '{company_name}'")

        # Use the default logo URL in case of error
        logo_url = "/placeholder.svg?height=48&width=48" # Reset logo url on error
        success = False
        message = f"Error extracting company info: {str(e)}"
        return company_name, logo_url, success, message

def extract_company_name_from_domain(domain: str) -> str:
    """Extract company name from domain.
    
    Args:
        domain: Domain name (e.g. www.example.com)
        
    Returns:
        Capitalized company name
    """
    try:
        # Check for empty domain
        if not domain or domain.strip() == "":
            logger.warning("Empty domain provided to extract_company_name_from_domain")
            return "Unknown"
        
        # Remove www. if present
        if domain.startswith('www.'):
            domain = domain[4:]
        
        # Remove protocol if present
        if '://' in domain:
            domain = domain.split('://', 1)[1]
            
        # Handle IP addresses or localhost
        if re.match(r'^(\d{1,3}\.){3}\d{1,3}$', domain) or domain.startswith('localhost'):
            return "Local"
            
        # Handle domains with port
        if ':' in domain:
            domain = domain.split(':', 1)[0]
        
        # Simply use the first part of the domain
        if '.' in domain:
            company = domain.split('.')[0]
        else:
            # If no dots, use the whole domain
            company = domain
        
        # Skip very short or empty company names
        if not company or company.strip() == "":
            logger.warning(f"Empty company name extracted from domain: {domain}")
            return domain.capitalize()
            
        # Handle very short names (just use as is, capitalized)
        if len(company) <= 2:
            return company.upper()
            
        # Format company name (capitalize first letter of each word)
        company_name = string.capwords(company.replace('-', ' ').replace('_', ' '))
        
        return company_name
    except Exception as e:
        logger.error(f"Error extracting company name from domain {domain}: {e}")
        
        # Always use the domain as fallback
        try:
            return domain.capitalize()
        except:
            return "Unknown"

def extract_company_name(soup: BeautifulSoup) -> str:
    """
    Extract company name from the BeautifulSoup object.
    
    Args:
        soup: BeautifulSoup object of the webpage
        
    Returns:
        Company name or empty string if not found
    """
    # Try to get og:site_name first (most reliable)
    og_site_name = soup.find('meta', property='og:site_name')
    if og_site_name and og_site_name.get('content'):
        name = og_site_name['content'].strip()
        # Clean up if it has taglines
        if ' - ' in name:
            name = name.split(' - ')[0].strip()
        elif ' | ' in name:
            name = name.split(' | ')[0].strip()
        return name
    
    # Check title tag but clean it
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        
        # Remove everything after a dash or pipe (common for page titles)
        if ' - ' in title:
            title = title.split(' - ')[0].strip()
        elif ' | ' in title:
            title = title.split(' | ')[0].strip()
        
        # Skip titles with "log in" or "sign up" phrases as they're typically not company names
        if "log in" in title.lower() or "sign up" in title.lower() or "login" in title.lower():
            return ""
            
        return title.strip()
    
    # Check common header elements
    header_logo = soup.find('a', class_=['logo', 'brand', 'navbar-brand'])
    if header_logo and header_logo.get_text().strip():
        return header_logo.get_text().strip()
    
    # Check copyright text
    copyright_text = soup.find(string=lambda text: text and '©' in text)
    if copyright_text:
        match = re.search(r'©\s*\d{4}\s*([A-Za-z0-9\s]+)', copyright_text)
        if match:
            return match.group(1).strip()
    
    return ""

def extract_logo_url(soup: BeautifulSoup, domain: str) -> str:
    """
    Extract logo URL from the BeautifulSoup object.
    
    Args:
        soup: BeautifulSoup object of the webpage
        domain: Domain name
        
    Returns:
        Logo URL or default logo URL if not found
    """
    base_url = f"https://{domain}"
    
    # Look for schema.org Organization logo first (most accurate)
    schema_tags = soup.find_all('script', type='application/ld+json')
    for tag in schema_tags:
        try:
            import json
            data = json.loads(tag.string)
            # Check for Organization schema
            if isinstance(data, dict):
                # Direct logo property
                if 'logo' in data:
                    if isinstance(data['logo'], str):
                        return urljoin(base_url, data['logo'])
                    elif isinstance(data['logo'], dict) and 'url' in data['logo']:
                        return urljoin(base_url, data['logo']['url'])
                # Logo inside Organization property
                elif '@type' in data and data['@type'] == 'Organization' and 'logo' in data:
                    if isinstance(data['logo'], str):
                        return urljoin(base_url, data['logo'])
                    elif isinstance(data['logo'], dict) and 'url' in data['logo']:
                        return urljoin(base_url, data['logo']['url'])
        except Exception:
            continue
    
    # Look for meta tags with 'logo' in the property/name
    meta_logo_tags = soup.find_all('meta', attrs={'property': lambda x: x and 'logo' in x.lower() if x else False})
    if not meta_logo_tags:
        meta_logo_tags = soup.find_all('meta', attrs={'name': lambda x: x and 'logo' in x.lower() if x else False})
    
    for tag in meta_logo_tags:
        if tag.get('content'):
            return urljoin(base_url, tag['content'])
    
    # Look for OpenGraph image
    og_image = soup.find('meta', property='og:image')
    if og_image and og_image.get('content'):
        return urljoin(base_url, og_image['content'])
    
    # Look for common logo selectors
    logo_selectors = [
        'img.logo', 'img#logo', '.logo img', '#logo img',
        'img.brand', 'img#brand', '.brand img', '#brand img',
        'img.site-logo', 'img#site-logo', '.site-logo img', '#site-logo img',
        'header img:first-child', '.header img:first-child',
        '.navbar-brand img', '.brand-logo img',
        'a[href="/"] img', 'a[href="./"] img'
    ]
    
    for selector in logo_selectors:
        try:
            logo_img = soup.select_one(selector)
            if logo_img and logo_img.get('src'):
                return urljoin(base_url, logo_img['src'])
        except Exception:
            continue
    
    # Fall back to favicon
    favicon_link = soup.find('link', rel=lambda r: r and ('icon' in r.lower() if r else False))
    if favicon_link and favicon_link.get('href'):
        return urljoin(base_url, favicon_link['href'])
    
    # Use Google's favicon service as last resort
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"

@router.post("/extract-company-info", response_model=CompanyInfoResponse)
async def get_company_info(request: CompanyInfoRequest) -> CompanyInfoResponse:
    """
    Extract company name and logo URL from a website.
    """
    try:
        # Validate and normalize URL
        url = request.url
        sanitized_url = sanitize_url(url)
        if not sanitized_url:
            return CompanyInfoResponse(
                url=url,
                company_name=None,
                logo_url=None,
                success=False,
                message="Invalid URL format"
            )
        
        # If this is an App Store or Play Store URL, use specialized extraction
        if is_app_store_url(sanitized_url) or is_play_store_url(sanitized_url):
            logger.info(f"Detected app store URL: {sanitized_url}")
            company_name, logo_url, success, message = await extract_with_playwright(sanitized_url)
            
            if success and company_name and logo_url:
                return CompanyInfoResponse(
                    url=url,
                    company_name=company_name,
                    logo_url=logo_url,
                    success=True,
                    message=message or "Successfully extracted app info from store page"
                )

        # First try the direct extraction method
        company_name, logo_url, success, message = await extract_company_info(sanitized_url)
        
        return CompanyInfoResponse(
            url=url,
            company_name=company_name,
            logo_url=logo_url,
            success=success,
            message=message
        )
            
    except Exception as e:
        logger.error(f"Error extracting company info: {str(e)}")
        
        # Even in error case, extract company name from domain if possible
        if hasattr(request, 'logo_url') and request.logo_url:
            return CompanyInfoResponse(
                url=url,
                company_name=company_name,
                logo_url=request.logo_url,  # Use the provided logo URL
                success=False,
                message=f"Error extracting company info: {str(e)}"
            )
        else:
            return CompanyInfoResponse(
                url=url,
                company_name=company_name,
                logo_url=logo_url,  # This will be either a favicon or placeholder
                success=False,
                message=f"Error extracting company info: {str(e)}"
            ) 