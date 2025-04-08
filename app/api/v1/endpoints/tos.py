from typing import Optional, Tuple, Dict, Any, List
import re
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl, validator
from playwright.async_api import async_playwright, Page, Browser, BrowserContext
import asyncio
from urllib.parse import urlparse, urljoin

router = APIRouter()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TermsRequest(BaseModel):
    """Request model for Terms of Service detection endpoints"""
    url: str
    
    @validator('url')
    def validate_url(cls, v):
        """Validate and normalize URL"""
        if not v:
            raise ValueError("URL cannot be empty")
        
        # Normalize URL
        v = normalize_url(v)
        return v

class TermsResponse(BaseModel):
    """Response model for Terms of Service detection endpoints"""
    url: str
    terms_url: Optional[str] = None
    success: bool = False
    message: str = ""
    method_used: str = ""

# Strong match terms for ToS link detection
STRONG_TERMS_MATCHES = [
    'terms of service', 'terms of use', 'terms and conditions',
    'conditions of use', 'condition of use', 'user agreement',
    'terms', 'tos', 'eula', 'legal terms'
]

def normalize_url(url: str) -> str:
    """
    Normalize URLs to ensure they're properly formatted.
    
    Args:
        url: The URL to normalize
        
    Returns:
        Normalized URL with proper scheme
    """
    url = url.strip()
    
    # Add scheme if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Parse and reconstruct to handle other normalization
    try:
        parsed = urlparse(url)
        
        # Remove trailing slash from netloc if present in the path
        if parsed.path == '/' and url.endswith('/'):
            url = url[:-1]
        
        # Validate that we have a valid netloc/domain
        if not parsed.netloc:
            raise ValueError("Invalid URL: missing domain")
    
    except Exception as e:
        logger.warning(f"URL normalization warning: {e}")
    
    return url

@router.post("/terms-finder", response_model=TermsResponse)
async def find_terms_of_service(request: TermsRequest) -> TermsResponse:
    """
    Enhanced endpoint to find Terms of Service link for a given URL.
    
    This endpoint uses advanced detection techniques including:
    - JavaScript-based detection
    - Dynamic scrolling and navigation
    - Footer and navigation analysis
    - Cloudflare challenge detection
    - Content verification
    
    Args:
        request (TermsRequest): Request body containing the URL to search
        
    Returns:
        TermsResponse: Response containing the Terms URL or error message
    """
    if not request.url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    try:
        # URL is already normalized in the validator
        return await detect_terms_url(request.url)
    except ValueError as e:
        # Handle validation errors
        return TermsResponse(
            url=request.url,
            success=False,
            message=f"Invalid URL: {str(e)}",
            method_used="validation_error"
        )

async def find_matching_link(page: Page, context: BrowserContext) -> Tuple[Optional[str], Page]:
    """Find a link matching ToS patterns and click it"""
    for keyword in STRONG_TERMS_MATCHES:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        element = await page.query_selector(f"text=/{pattern.pattern}/i")
        if element:
            logger.info(f"Found link matching: '{keyword}'")
            
            # Store starting URL
            starting_url = page.url
            
            # Set up event handlers for navigation
            try:
                page_promise = context.wait_for_event('page', timeout=500)
            except:
                page_promise = None
                
            try:
                nav_promise = page.wait_for_navigation(timeout=1000)
            except:
                nav_promise = None
            
            # Click the element
            await element.click()
            
            # Check for URL change first
            await page.wait_for_timeout(200)
            if page.url != starting_url:
                return page.url, page
            
            # Check for new page/tab
            try:
                if page_promise:
                    try:
                        new_page = await page_promise
                        await new_page.wait_for_load_state('domcontentloaded', timeout=1500)
                        return new_page.url, new_page
                    except Exception as e:
                        page_promise = None
            except Exception:
                page_promise = None
            
            # Check for navigation
            try:
                if nav_promise:
                    try:
                        await nav_promise
                        if page.url != starting_url:
                            return page.url, page
                    except Exception:
                        nav_promise = None
            except Exception:
                nav_promise = None
            
            # Final URL check
            await page.wait_for_timeout(300)
            if page.url != starting_url:
                return page.url, page
                
    return None, page

async def find_all_links_js(page: Page, context: BrowserContext) -> Tuple[Optional[str], Page]:
    """Use JavaScript to find all potential ToS links without scrolling"""
    # Check for Cloudflare challenge page
    is_cloudflare = await page.evaluate("""() => {
        const html = document.documentElement.innerHTML.toLowerCase();
        return html.includes('cloudflare') && 
               (html.includes('challenge') || 
                html.includes('security check') || 
                html.includes('captcha') || 
                html.includes('verify your browser'));
    }""")
    
    if is_cloudflare:
        logger.info("Detected Cloudflare challenge page, cannot extract reliable links")
        return None, page
    
    # Find all links using JS
    links = await page.evaluate("""() => {
        const allElements = document.querySelectorAll('a');
        const links = [];
        
        // Define priority scoring
        const exactMatchPriorities = {
            'terms': 100,
            'terms of service': 95,
            'terms of use': 90,
            'terms and conditions': 85,
            'conditions of use': 80,
            'condition of use': 75,
            'user agreement': 70,
        };
        
        allElements.forEach(el => {
            const text = (el.textContent || '').toLowerCase().trim();
            const href = el.getAttribute('href') || '';
            
            // Skip Cloudflare links
            if (href.includes('cloudflare.com') && 
                (href.includes('challenge') || href.includes('utm_source=challenge'))) {
                return;
            }
            
            // Skip Cloudflare text
            if (text.includes('cloudflare') && 
                (text.includes('challenge') || text.includes('security check'))) {
                return;
            }
            
            // Check if text or href contains target terms
            if (text.includes('terms') || 
                text.includes('tos') || 
                text.includes('conditions') || 
                text.includes('eula') || 
                text.includes('agreement') ||
                href.includes('terms') || 
                href.includes('tos') || 
                href.includes('conditions') || 
                href.includes('eula') || 
                href.includes('agreement') ||
                href.includes('legal'))
            {
                // Calculate priority
                let priority = 0;
                
                // Check for exact matches
                for (const [exactMatch, score] of Object.entries(exactMatchPriorities)) {
                    if (text === exactMatch) {
                        priority = score;
                        break;
                    }
                }
                
                // Partial matches
                if (priority === 0) {
                    if (text.includes('terms of service')) priority = 40;
                    else if (text.includes('terms of use')) priority = 38;
                    else if (text.includes('terms')) priority = 35;
                    else if (text.includes('conditions')) priority = 30;
                    else if (text.includes('agreement')) priority = 25;
                    else if (text.includes('tos')) priority = 20;
                    else if (text.includes('eula')) priority = 15;
                    else if (href.includes('terms')) priority = 10;
                    else if (href.includes('legal')) priority = 5;
                }
                
                // Get absolute URL for relative links
                let absoluteHref = href;
                if (href && !href.startsWith('http') && !href.startsWith('//')) {
                    const base = document.baseURI;
                    if (href.startsWith('/')) {
                        const origin = new URL(base).origin;
                        absoluteHref = origin + href;
                    } else {
                        absoluteHref = new URL(href, base).href;
                    }
                }
                
                links.push({
                    text: (el.textContent || '').trim(),
                    href: href,
                    absoluteHref: absoluteHref,
                    x: el.getBoundingClientRect().left,
                    y: el.getBoundingClientRect().top,
                    width: el.getBoundingClientRect().width,
                    height: el.getBoundingClientRect().height,
                    priority: priority
                });
            }
        });
        
        // Sort by priority
        links.sort((a, b) => b.priority - a.priority);
        
        return links;
    }""")
    
    logger.info(f"Found {len(links)} potential matching links via JavaScript")
    
    if links:
        for link in links[:5]:  # Limit to top 5 for logging
            logger.info(f"Checking link: {link['text']} → {link['href']}")
            
            try:
                # Skip Cloudflare challenge links
                if (('absoluteHref' in link and link['absoluteHref'] and 'cloudflare.com' in link['absoluteHref']) or
                    (link['href'] and 'cloudflare.com' in link['href'])):
                    continue
                
                # Try direct navigation for absolute URLs
                if 'absoluteHref' in link and link['absoluteHref'] and link['absoluteHref'].startswith('http'):
                    try:
                        await page.goto(link['absoluteHref'], timeout=2000, wait_until='domcontentloaded')
                        
                        # Check for Cloudflare challenge
                        is_challenge = await page.evaluate("""() => {
                            const html = document.documentElement.innerHTML.toLowerCase();
                            return html.includes('cloudflare') && 
                                  (html.includes('challenge') || 
                                   html.includes('security check'));
                        }""")
                        
                        if is_challenge:
                            continue
                        
                        logger.info(f"Direct navigation successful to: {page.url}")
                        return page.url, page
                    except Exception:
                        # Fall back to clicking
                        pass
                
                # Try clicking the element
                element = None
                
                # Try different selectors
                if link['href']:
                    element = await page.query_selector(f"a[href='{link['href']}']")
                    if not element and not link['href'].startswith('http'):
                        element = await page.query_selector(f"a[href*='{link['href'].replace('/', '')}']")
                
                if not element:
                    clean_text = link['text'].replace("'", "\\'").strip()
                    element = await page.query_selector(f"text='{clean_text}'")
                
                if element:
                    # Store starting URL
                    starting_url = page.url
                    
                    # Handle navigation
                    try:
                        page_promise = context.wait_for_event('page', timeout=500)
                    except:
                        page_promise = None
                        
                    try:
                        nav_promise = page.wait_for_navigation(timeout=1000)
                    except:
                        nav_promise = None
                    
                    # Click the element
                    try:
                        if 'x' in link and 'y' in link and 'width' in link and 'height' in link:
                            x = link['x'] + link['width']/2
                            y = link['y'] + link['height']/2
                            await page.mouse.click(x, y)
                        else:
                            await element.click()
                    except Exception:
                        if 'absoluteHref' in link and link['absoluteHref']:
                            await page.goto(link['absoluteHref'], timeout=2000)
                    
                    # Check for URL change
                    await page.wait_for_timeout(200)
                    if page.url != starting_url:
                        return page.url, page
                    
                    # Check for new page/tab
                    if page_promise:
                        try:
                            new_page = await page_promise
                            await new_page.wait_for_load_state('domcontentloaded')
                            return new_page.url, new_page
                        except:
                            pass
                    
                    # Check for navigation
                    if nav_promise:
                        try:
                            await nav_promise
                            if page.url != starting_url:
                                return page.url, page
                        except:
                            pass
                    
                    # Final URL check
                    await page.wait_for_timeout(300)
                    if page.url != starting_url:
                        return page.url, page
                    
                    # Last resort: direct navigation
                    if 'absoluteHref' in link and link['absoluteHref']:
                        try:
                            await page.goto(link['absoluteHref'], timeout=2000)
                            return page.url, page
                        except:
                            pass
            except Exception as e:
                logger.error(f"Error with link: {e}")
                continue
    
    return None, page

async def check_for_better_terms_link(page: Page, context: BrowserContext) -> Tuple[Optional[str], Page]:
    """Check if the terms page has more specific terms links"""
    logger.info("Checking for more specific terms links on the page...")
    
    # Use the same JS function to find additional terms links
    return await find_all_links_js(page, context)

async def is_cloudflare_challenge(page: Page) -> bool:
    """Check if page is a Cloudflare challenge"""
    return await page.evaluate("""() => {
        const html = document.documentElement.innerHTML.toLowerCase();
        return html.includes('cloudflare') && 
              (html.includes('challenge') || 
               html.includes('security check') || 
               html.includes('captcha') || 
               html.includes('verify your browser'));
    }""")

async def verify_final_link(page: Page, context: BrowserContext) -> Optional[str]:
    """Check if the current page links to a more specific terms page"""
    logger.info("Verifying if this is the final Terms of Service destination...")
    
    # Look for links to more specific terms pages
    final_links = await page.evaluate("""() => {
        const links = Array.from(document.querySelectorAll('a[href]'));
        
        // Define indicators with scores
        const strongIndicators = [
            { text: 'terms of service', score: 100 },
            { text: 'terms of use', score: 95 },
            { text: 'user agreement', score: 90 },
            { text: 'terms and conditions', score: 85 }
        ];
        
        // Score links
        const scoredLinks = links.map(link => {
            const text = link.textContent.toLowerCase().trim();
            const href = link.getAttribute('href');
            
            // Skip empty or self links
            if (!href || href === '#' || href === window.location.href) {
                return { score: -1 };
            }
            
            // Skip Cloudflare links
            if (href.includes('cloudflare.com')) {
                return { score: -1 };
            }
            
            let score = 0;
            let matchReason = [];
            
            // Check for exact and partial matches
            for (const indicator of strongIndicators) {
                if (text === indicator.text) {
                    score += indicator.score;
                    matchReason.push(`exact_match: ${indicator.text}`);
                } else if (text.includes(indicator.text)) {
                    score += indicator.score * 0.7;
                    matchReason.push(`contains: ${indicator.text}`);
                }
            }
            
            // URL indicators
            if (href.includes('terms-of-service') || href.includes('terms_of_service')) {
                score += 40;
                matchReason.push('terms_of_service_in_url');
            } else if (href.includes('terms-of-use') || href.includes('terms_of_use')) {
                score += 35;
                matchReason.push('terms_of_use_in_url');
            }
            
            // PDF format bonus
            if (href.endsWith('.pdf')) {
                score += 15;
                matchReason.push('pdf_format');
            }
            
            return {
                text: text,
                href: href,
                score: score,
                matchReason: matchReason
            };
        }).filter(item => item.score > 30);
        
        // Sort by score
        scoredLinks.sort((a, b) => b.score - a.score);
        
        return scoredLinks.slice(0, 3);
    }""")
    
    if not final_links:
        logger.info("Current page appears to be the final destination")
        return None
    
    logger.info(f"Found {len(final_links)} potential deeper terms links")
    
    # Check the top scoring link
    if final_links and final_links[0]['score'] > 50:
        top_link = final_links[0]
        logger.info(f"Found promising deeper link: '{top_link['text']}' → {top_link['href']}")
        
        try:
            # Resolve URL if relative
            href = top_link['href']
            if not href.startswith('http'):
                if href.startsWith('/'):
                    base_url = '/'.join(page.url.split('/')[:3])
                    href = base_url + href
                else:
                    base_url = page.url.split('?')[0].split('#')[0]
                    if base_url.endsWith('/'):
                        href = base_url + href
                    else:
                        href = base_url + '/' + href
            
            # Skip if it's the same URL
            if href == page.url:
                return None
            
            # Skip if it's a Cloudflare URL
            if 'cloudflare.com' in href:
                return None
            
            # Check the link content
            logger.info(f"Checking potential final terms link: {href}")
            await page.goto(href, timeout=3000)
            
            # Verify content quality
            is_better = await page.evaluate("""() => {
                const title = document.title.toLowerCase();
                const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map(h => h.textContent.toLowerCase());
                const content = document.body.textContent.toLowerCase();
                
                // Check for terms indicators
                const hasTermsTitle = title.includes('terms') && 
                                     (title.includes('service') || title.includes('use') || title.includes('legal'));
                const hasTermsHeading = headings.some(h => 
                    h.includes('terms') && (h.includes('service') || h.includes('use'))
                );
                const hasLegalContent = content.includes('agree') && 
                                       content.includes('terms') && 
                                       (content.includes('liable') || content.includes('warranty'));
                
                return {
                    isFinalTerms: hasTermsTitle || hasTermsHeading,
                    contentQuality: hasLegalContent ? 'good' : 'questionable',
                    isCloudflare: content.includes('cloudflare') && 
                                 (content.includes('challenge') || content.includes('security check'))
                };
            }""")
            
            # Check for Cloudflare challenge
            if is_better.get('isCloudflare', False):
                return None
            
            # Use the page if it's better
            if is_better.get('isFinalTerms', False) and is_better.get('contentQuality') == 'good':
                logger.info(f"Found better final terms page: {page.url}")
                return page.url
        except Exception as e:
            logger.error(f"Error checking final link: {e}")
            return None
    
    return None

async def detect_terms_url(url: str) -> TermsResponse:
    """Main function to detect Terms of Service URL using enhanced techniques"""
    browser = None
    
    # Normalize URL if not already done
    try:
        url = normalize_url(url)
    except ValueError as e:
        return TermsResponse(
            url=url,
            success=False,
            message=f"Invalid URL: {str(e)}",
            method_used="validation_error"
        )
    
    try:
        async with async_playwright() as p:
            # Launch with faster timeouts
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 720},
                java_script_enabled=True
            )
            
            # Set default timeout for all operations
            context.set_default_timeout(5000)  # 5 seconds default timeout
            
            page = await context.new_page()

            logger.info(f"Navigating to URL: {url}")
            
            try:
                # Faster navigation timeout and wait only for domcontentloaded
                await page.goto(url, wait_until='domcontentloaded', timeout=5000)
            except Exception as e:
                logger.error(f"Failed to navigate to URL: {str(e)}")
                return TermsResponse(
                    url=url,
                    success=False,
                    message=f"Failed to navigate to URL: {str(e)}",
                    method_used="navigation_error"
                )
                
            # Shorter wait for dynamic content
            await page.wait_for_timeout(1000)
            
            # Check for Cloudflare challenge
            if await is_cloudflare_challenge(page):
                logger.warning("Cloudflare challenge detected on the initial page")
                return TermsResponse(
                    url=url,
                    success=False,
                    message="Cloudflare challenge detected, cannot proceed with detection",
                    method_used="cloudflare_blocked"
                )
            
            # First try finding links using JavaScript
            logger.info("Trying JavaScript-based link detection...")
            visited_url, final_page = await find_all_links_js(page, context)
            method_used = "javascript_detection"
            
            # If no link found, try with matching link patterns
            if not visited_url:
                logger.info("No links found with JavaScript. Trying matching link patterns...")
                visited_url, final_page = await find_matching_link(page, context)
                method_used = "pattern_matching"
            
            if visited_url:
                logger.info(f"Found terms link: {visited_url}")
                initial_url = visited_url
                
                # Check for better/more specific terms links
                better_url, better_page = await check_for_better_terms_link(final_page, context)

                # Skip Cloudflare challenge URLs
                if better_url and "cloudflare.com" in better_url:
                    better_url = None
                    better_page = final_page

                # Update with better URL if found
                if better_url:
                    visited_url = better_url
                    final_page = better_page
                    logger.info(f"Found better terms link: {visited_url}")
                    method_used = "better_link_found"
                
                # Verify if we're on the final link
                final_check = await verify_final_link(final_page, context)
                if final_check:
                    visited_url = final_check
                    logger.info(f"Verified final destination URL: {visited_url}")
                    method_used = "verified_final_link"
                
                return TermsResponse(
                    url=url,
                    terms_url=visited_url,
                    success=True,
                    message="Successfully found Terms of Service URL",
                    method_used=method_used
                )
            else:
                logger.warning("No Terms of Service link found")
                return TermsResponse(
                    url=url,
                    success=False,
                    message="No Terms of Service link found with available methods",
                    method_used="not_found"
                )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error finding Terms link: {error_msg}")
        return TermsResponse(
            url=url,
            success=False,
            message=f"Error finding Terms link: {error_msg}",
            method_used="error"
        )
    finally:
        if browser:
            await browser.close()