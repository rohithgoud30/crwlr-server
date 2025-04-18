import random
import time
import re
import os
import asyncio
import aiohttp
from urllib.parse import urlparse, urljoin

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
            if domain.count(
                ".") == 1:  # Only one dot indicates a likely main domain
                return url.replace(domain, f"www.{domain}")

        return url
    except Exception as e:
        print(f"Error normalizing domain: {e}")
        return url  # Return original URL if parsing fails


def normalize_privacy_url(url):
    """Normalize URL for privacy policy detection."""
    if url:
        # Remove leading/trailing whitespace
        url = url.strip()

        # Remove any unnecessary prefixes
        if url.startswith('url='):
            url = url[4:]
        if url.startswith('http%3A//'):
            url = 'http://' + url[10:]
        if url.startswith('https%3A//'):
            url = 'https://' + url[11:]
        if url.startswith('//'):
            url = 'https:' + url
        if url.startswith('/'):
            url = url[1:]

        # Ensure URL has proper scheme
        if url and not url.startswith(('http://', 'https://')):
            url = f"https://{url}"

    return url


@router.post("/privacy", response_model=PrivacyResponse)
async def find_privacy_policy(request: PrivacyRequest) -> PrivacyResponse:
    """Find privacy policy link for a website using intelligent strategies."""
    url = request.url
    playwright = None
    potential_privacy_links = []  # Store all potential links with their confidence scores
    unverified_links = []  # Store unverified links for fallback
    best_confidence = 0
    start_time = time.time()
    bing_result = None
    ddg_result = None

    # Ensure all execution paths return a valid PrivacyResponse
    try:
        # Early URL validation and normalization
        if not url:
            raise HTTPException(
                status_code=400,
                detail="URL parameter is required")

        try:
            url = normalize_privacy_url(url)
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
        except Exception as e:
            return PrivacyResponse(
                url=url,
                pp_url=None,
                success=False,
                message=f"Invalid URL format: {str(e)}",
                method_used="validation_error"
            )

        # Track potential links for "best effort" response
        potential_privacy_links = []
        unverified_links = []

        # Attempt browser-based detection with fallbacks
        playwright = None
        try:
            playwright = await async_playwright().start()
            browser, context, page, user_agent = await setup_browser(playwright)

            try:
                # Try to navigate to the URL
                success, response, privacy_links_from_errors = await navigate_with_retry(page, url, max_retries=2)

            except Exception as e:
                print(f"Navigation setup error: {e}")
                # Save any links found during error handling
                if 'privacy_links_from_errors' in locals() and privacy_links_from_errors:
                    unverified_links.extend(privacy_links_from_errors)

                # If navigation failed, try search engines directly
                return await fallback_to_search_engines(url, domain, page)

            # Check if navigation was successful
            if not success:
                # If navigation failed completely, try search fallbacks
                print(f"Navigation to {url} failed. Trying search fallbacks.")

                # If we have privacy links from anti-bot detection, use them with search result verification
                if privacy_links_from_errors and len(privacy_links_from_errors) > 0:
                    # Add these links to unverified_links for potential comparison with search results
                    unverified_links.extend(privacy_links_from_errors)
                    
                    # Get search engine results to verify the links
                    search_results = await get_search_engine_results(domain, page)
                    
                    # Look for matches between error-found links and search results
                    matches = []
                    for link in privacy_links_from_errors:
                        for result in search_results:
                            # Check for exact match or similar URLs
                            if link == result or similar_urls(link, result):
                                print(f"Match found between link from error page and search results: {link}")
                                matches.append(link)
                                break
                    
                    # If we found matches, return the first one
                    if matches:
                        return PrivacyResponse(
                            url=url,
                            pp_url=matches[0],
                            success=True,
                            message=f"Found privacy link confirmed by both error page and search results: {matches[0]}",
                            method_used="error_search_match"
                        )
                
                # If we got here, no matches were found. Try search engines as fallback
                return await fallback_to_search_engines(url, domain, page)

            # After successful navigation

            # First attempt: JavaScript-based detection (most reliable)
            js_result, page, js_unverified = await find_all_privacy_links_js(page, domain)

            # Second attempt: Scroll-based detection (finds footer links)
            scroll_result, page, scroll_unverified = await smooth_scroll_and_click_privacy(page, context, js_unverified)

            # If we have both JS and scroll results, check if they match
            if js_result and scroll_result:
                # Compare JS and scroll results for consistency
                if js_result == scroll_result or similar_urls(js_result, scroll_result):
                    print(f"✅ JS and scroll results match, high confidence: {js_result}")
                    # High confidence result, verify with search as extra check
                    search_results = await get_search_engine_results(domain, page)
                    # If also confirmed by search, even higher confidence
                    if any(similar_urls(js_result, sr) for sr in search_results):
                        return PrivacyResponse(
                            url=url,
                            pp_url=js_result,
                            success=True,
                            message=f"Found privacy policy confirmed by JS, scroll, and search: {js_result}",
                            method_used="multi_method_confirmation"
                        )
                    else:
                        # Still good confidence
                        return PrivacyResponse(
                            url=url,
                            pp_url=js_result,
                            success=True,
                            message=f"Found privacy policy confirmed by both JS and scroll: {js_result}",
                            method_used="js_scroll_match"
                        )
                else:
                    # We have different results from JS and scroll, check with search engines
                    search_results = await get_search_engine_results(domain, page)
                    
                    # Check if either result is confirmed by search engines
                    js_confirmed = any(similar_urls(js_result, search_result) for search_result in search_results)
                    scroll_confirmed = any(similar_urls(scroll_result, search_result) for search_result in search_results)
                    
                    if js_confirmed and not scroll_confirmed:
                        print(f"JS result confirmed by search engines: {js_result}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=js_result,
                            success=True,
                            message=f"JavaScript result confirmed by search engines: {js_result}",
                            method_used="js_search_confirmed"
                        )
                    elif scroll_confirmed and not js_confirmed:
                        print(f"Scroll result confirmed by search engines: {scroll_result}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=scroll_result,
                            success=True,
                            message=f"Scroll result confirmed by search engines: {scroll_result}",
                            method_used="scroll_search_confirmed"
                        )
                    elif js_confirmed and scroll_confirmed:
                        # Both are confirmed, prioritize JS
                        print(f"Both JS and scroll results confirmed by search engines, using JS: {js_result}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=js_result,
                            success=True,
                            message=f"Both methods confirmed by search engines, using JavaScript result: {js_result}",
                            method_used="js_scroll_search_confirmed"
                        )
            elif js_result and not scroll_result:
                # Only JS result - verify with search engines
                search_results = await get_search_engine_results(domain, page)
                if any(similar_urls(js_result, search_result) for search_result in search_results):
                    print(f"JS result confirmed by search engines: {js_result}")
                    return PrivacyResponse(
                        url=url,
                        pp_url=js_result,
                        success=True,
                        message=f"JavaScript result confirmed by search engines: {js_result}",
                        method_used="js_search_confirmed"
                    )
                else:
                    # JS result only, still high confidence
                    return PrivacyResponse(
                        url=url,
                        pp_url=js_result,
                        success=True,
                        message=f"Found privacy policy via JavaScript detection: {js_result}",
                        method_used="javascript"
                    )
            elif scroll_result and not js_result:
                # Only scroll result - verify with search engines
                search_results = await get_search_engine_results(domain, page)
                if any(similar_urls(scroll_result, search_result) for search_result in search_results):
                    print(f"Scroll result confirmed by search engines: {scroll_result}")
                    return PrivacyResponse(
                        url=url,
                        pp_url=scroll_result,
                        success=True,
                        message=f"Scroll result confirmed by search engines: {scroll_result}",
                        method_used="scroll_search_confirmed"
                    )
                else:
                    # Scroll result only, still good confidence
                    return PrivacyResponse(
                        url=url,
                        pp_url=scroll_result,
                        success=True,
                        message=f"Found privacy policy via scroll detection: {scroll_result}",
                        method_used="scroll"
                    )
            
            # If neither JS nor scroll found anything, try search engines
            return await fallback_to_search_engines(url, domain, page)

        except Exception as e:
            print(f"Error in privacy detection: {e}")
            # Try to return best effort from collected links if available
            if potential_privacy_links:
                best_link = max(potential_privacy_links, key=lambda x: x["confidence"])
                end_time = time.time()
                return PrivacyResponse(
                    url=url, 
                    pp_url=best_link["url"],
                    success=True,
                    message=f"Best effort link with confidence {best_link['confidence']}%",
                    method_used=f"best_effort_{best_link['source']}"
                )
            elif unverified_links:
                end_time = time.time()
                return PrivacyResponse(
                    url=url, 
                    pp_url=unverified_links[0],
                    success=True,
                    message=f"Using unverified link as fallback. No verified links found.",
                    method_used="unverified_fallback"
                )
            else:
                # Always return a valid PrivacyResponse, never None
                return await handle_error_privacy(url, None, str(e))
    
    except Exception as e:
        print(f"Critical error in privacy detection: {e}")
        if potential_privacy_links:
            best_link = max(potential_privacy_links, key=lambda x: x["confidence"])
            end_time = time.time()
            return PrivacyResponse(
                url=url, 
                pp_url=best_link["url"],
                success=True,
                message=f"Best effort link with confidence {best_link['confidence']}%",
                method_used=f"best_effort_{best_link['source']}"
            )
        elif unverified_links:
            end_time = time.time()
            return PrivacyResponse(
                url=url, 
                pp_url=unverified_links[0],
                success=True,
                message=f"Using unverified link as fallback. No verified links found.",
                method_used="unverified_fallback"
            )
        else:
            # Always return a valid PrivacyResponse, never None
            return await handle_error_privacy(url, None, str(e))
    
    finally:
        # Clean up
        if playwright:
            try:
                await playwright.stop()
            except Exception as e:
                print(f"Error stopping playwright: {e}")

async def handle_navigation_failure_privacy(url: str, unverified_result: str = None) -> PrivacyResponse:
    """Handle navigation failures by using search engine fallbacks."""
    # This function is being replaced by fallback_to_search_engines
    try:
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        return await fallback_to_search_engines(url, domain, None)
    except Exception as e:
        print(f"Error in navigation failure handler: {e}")
        return PrivacyResponse(
            url=url,
            pp_url=None,
            success=False,
            message=f"Error during navigation: {str(e)}",
            method_used="navigation_error"
        )

# Keeping only one handle_error_privacy function
async def handle_error_privacy(url: str, unverified_result: str, error: str) -> PrivacyResponse:
    """Simplified error handler for Privacy Policy."""
    try:
        if unverified_result:
            # Check if this unverified result appears in search engines
            try:
                parsed_url = urlparse(url)
                domain = parsed_url.netloc
                
                # Create a dummy page object for search functions
                class DummyPage:
                    async def goto(self, *args, **kwargs):
                        pass
                    async def evaluate(self, *args, **kwargs):
                        return []
                    async def wait_for_timeout(self, *args, **kwargs):
                        pass
                
                search_results = await get_search_engine_results(domain, DummyPage())
                
                # Check if unverified result is confirmed by search results
                if any(similar_urls(unverified_result, result) for result in search_results):
                    return PrivacyResponse(
                        url=url,
                        pp_url=unverified_result,
                        success=True,
                        message=f"Error in browser detection but found matching URL in search results: {unverified_result}",
                        method_used="error_search_match"
                    )
            except Exception as e:
                print(f"Error checking search results for verification: {e}")
        
        # Try to extract a domain for search
        try:
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            
            # Try search engines directly
            return await fallback_to_search_engines(url, domain, None)
        except Exception as e:
            print(f"Error in search fallback: {e}")
        
        # If all else fails
        return PrivacyResponse(
            url=url,
            pp_url=None,
            success=False,
            message=f"Error during privacy detection: {error}. Could not find privacy policy link via any method.",
            method_used="error_all_failed"
        )
    except Exception as e:
        # Ultimate fallback if anything fails
        return PrivacyResponse(
            url=url or "unknown_url",
            pp_url=None,
            success=False,
            message=f"Critical error in error handler: {str(e)}",
            method_used="critical_error_fallback"
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

                # Check if link opens in new tab/window
                target = await link.get_attribute("target")
                rel = await link.get_attribute("rel") or ""
                opens_in_new_tab = target == "_blank" or "noopener" in rel or "noreferrer" in rel
                
                # Skip links with text indicating they open in a new window/tab
                if "new window" in text.lower() or "new tab" in text.lower():
                    print(f"Skipping link with 'new window/tab' in text: {text}")
                    if href:
                        # Try direct navigation instead
                        try:
                            await page.goto(href, timeout=4000, wait_until="domcontentloaded")
                            return page.url, page, unverified_result
                        except Exception as e:
                            print(f"Direct navigation error for 'new window/tab' link: {e}")
                    continue

                # Improved scoring to prioritize exact text matches
                score = 0
                
                # Text-based scoring - exact matches are highly prioritized
                if text == "privacy policy":
                    score = 100  # Exact match for privacy policy
                elif text == "privacy notice":
                    score = 95   # Exact match for privacy notice
                elif text == "privacy":
                    score = 90   # Just "privacy" is still very good
                elif "privacy policy" in text:
                    score = 85   # Contains exact phrase "privacy policy"
                elif "privacy notice" in text:
                    score = 80   # Contains exact phrase "privacy notice"
                elif "privacy" in text and ("statement" in text or "information" in text):
                    score = 75   # Privacy statement/information
                elif "privacy" in text:
                    score = 70   # Contains "privacy"
                elif "data protection" in text:
                    score = 65   # Data protection reference 
                elif "data" in text and ("protection" in text or "privacy" in text):
                    score = 60   # Other data privacy references
                elif "legal" in text and "privacy" in text:
                    score = 55   # Legal privacy mentions
                elif "legal" in text:
                    score = 40   # Legal might contain privacy

                # URL-based scoring - adds to text score
                if "/privacy-policy" in href or "/privacy_policy" in href:
                    score += 30
                elif "/privacy" in href or "/data-privacy" in href:
                    score += 25
                elif "/legal/privacy" in href:
                    score += 25
                elif "/data-protection" in href:
                    score += 20
                elif "/legal" in href:
                    score += 15
                
                # Location bonuses
                try:
                    # Check if link is in footer (more likely to be privacy)
                    is_footer = await page.evaluate("""(link) => {
                        const element = link;
                        const rect = element.getBoundingClientRect();
                        const windowHeight = window.innerHeight;
                        return rect.bottom > windowHeight * 0.7; // Bottom 30% of page
                    }""", link)
                    
                    if is_footer:
                        score += 10
                except Exception:
                    pass
                
                # Store the link with its score
                scored_links.append({
                    "element": link,
                    "text": text,
                    "href": href,
                    "score": score,
                    "opens_in_new_tab": opens_in_new_tab
                })
                
            except Exception as e:
                continue
        
        # Sort links by score
        scored_links.sort(key=lambda x: x["score"], reverse=True)
        
        # Try high-scoring links
        for link_data in scored_links:
            if link_data["score"] > 50:  # Only consider high confidence matches
                print(f"Found high confidence privacy link: {link_data['text']} (Score: {link_data['score']})")
                
                # Handle differently based on whether it opens in new tab
                if link_data["opens_in_new_tab"]:
                    print(f"Link opens in new tab/window. Using direct navigation.")
                    try:
                        await page.goto(link_data["href"], timeout=4000, wait_until="domcontentloaded")
                        await page.wait_for_timeout(300)
                        return page.url, page, unverified_result
                    except Exception as e:
                        print(f"Direct navigation error: {e}")
                        # Return href without navigation if direct navigation fails
                        return link_data["href"], page, unverified_result
                else:
                    # For normal links, use click and navigation
                    success = await click_and_wait_for_navigation(
                        page, link_data["element"], timeout=5000
                    )
                    if success:
                        return page.url, page, unverified_result
        
        # If we got here, no suitable links were found
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_link_privacy: {e}")
        return None, page, unverified_result


async def click_and_wait_for_navigation(page, element, timeout=5000):
    """Click a link and wait for navigation with improved handling for new tab links."""
    try:
        # Check if the link will open in a new tab/window
        target = await element.get_attribute("target")
        rel = await element.get_attribute("rel") or ""
        href = await element.get_attribute("href")
        
        # Skip navigation wait if link opens in new tab or has noopener/noreferrer
        opens_in_new_tab = target == "_blank" or "noopener" in rel or "noreferrer" in rel
        
        if opens_in_new_tab and href:
            print(f"Link opens in new tab. Using direct navigation instead of clicking.")
            try:
                await page.goto(href, timeout=timeout, wait_until="domcontentloaded")
                return True
            except Exception as e:
                print(f"Direct navigation error: {e}")
                return False
        else:
            # For normal links, use navigation wait approach
            async with page.expect_navigation(
                timeout=timeout, wait_until="domcontentloaded"):
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
        # Expanded footer selectors to catch more variations
        footer_selectors = [
            "footer", 
            ".footer", 
            "#footer", 
            "[class*='footer']", 
            "[id*='footer']", 
            ".bottom", 
            ".legal", 
            ".copyright", 
            ".site-info", 
            "[role='contentinfo']"
        ]

        # Get page height more efficiently
        page_height = await current_page.evaluate(
            """() => document.documentElement.scrollHeight"""
        )

        # More comprehensive scroll positions
        positions_to_check = [
            page_height * 0.9,  # Bottom of page (likely footer location)
            page_height * 0.8,  # Near bottom
            page_height * 0.7,  # Lower part
            page_height * 0.5,  # Middle of page
            page_height * 0.3,  # Upper part for sites with privacy links in header/menu
        ]

        for scroll_pos in positions_to_check:
            await current_page.evaluate(f"window.scrollTo(0, {scroll_pos})")
            await current_page.wait_for_timeout(300)

            visited_url, current_page, unverified_result = await find_matching_link_privacy(
                current_page, context, unverified_result
            )
            if visited_url:
                return visited_url, current_page, unverified_result

        # Enhanced corporate privacy link detection with more patterns
        corporate_privacy_link = await current_page.evaluate("""() => {
            // Check for corporate-style privacy links with specific patterns
            const links = Array.from(document.querySelectorAll('a[href]'));
            
            // Prioritize and score links
            const scoredLinks = links.map(link => {
                const href = link.href.toLowerCase();
                const text = (link.innerText || link.textContent).toLowerCase().trim();
                
                // Skip if empty text or hash-only link
                if (!text || href === '#' || href.startsWith('javascript:')) {
                    return { score: -1, link: link, href: href };
                }
                
                let score = 0;
                
                // Check for corporate privacy center patterns
                if (href.includes('privacy-center')) score += 90;
                else if (href.includes('privacy-notice')) score += 85;
                else if (href.includes('privacy-policy')) score += 85;
                else if (href.includes('privacy/policy')) score += 80;
                else if (href.includes('corporate/privacy')) score += 80;
                else if (href.includes('legal/privacy')) score += 75;
                else if (href.includes('data-protection')) score += 75;
                else if (href.includes('data-privacy')) score += 75;
                else if (href.includes('personal-data')) score += 70;
                else if (href.includes('/privacy/')) score += 70;
                else if (href.includes('/privacy')) score += 65;
                else if (href.includes('/legal/')) score += 60;
                
                // Text scoring
                if (text === 'privacy policy') score += 100;
                else if (text === 'privacy notice') score += 95;
                else if (text === 'privacy statement') score += 90;
                else if (text === 'privacy') score += 85;
                else if (text.includes('privacy policy')) score += 80;
                else if (text.includes('privacy notice')) score += 75;
                else if (text.includes('data protection')) score += 70;
                else if (text.includes('privacy') && text.length < 20) score += 65;
                else if (text.includes('data privacy')) score += 65;
                else if (text.includes('privacy')) score += 50;
                
                // If it specifies it opens in a new tab/window, add boost
                if (link.getAttribute('target') === '_blank' || 
                    text.includes('new window') || 
                    text.includes('new tab')) {
                    score += 10;
                }
                
                return { score, link, href };
            })
            .filter(item => item.score > 0)  // Only keep scored links
            .sort((a, b) => b.score - a.score);  // Sort by score descending
            
            // Return the href of the highest scored link or null if none found
            return scoredLinks.length > 0 ? scoredLinks[0].href : null;
        }""")
        
        if corporate_privacy_link:
            print(f"Found corporate privacy link: {corporate_privacy_link}")
            try:
                await current_page.goto(corporate_privacy_link, timeout=4000, wait_until="domcontentloaded")
                await current_page.wait_for_timeout(300)
                return current_page.url, current_page, unverified_result
            except Exception as e:
                print(f"Navigation to corporate privacy link failed: {e}")
                # Return the link even if navigation failed
                return corporate_privacy_link, current_page, unverified_result

        # Check footer area with improved approach
        for selector in footer_selectors:
            footer = await current_page.query_selector(selector)
            if footer:
                print(f"Found footer with selector: {selector}")
                await footer.scroll_into_view_if_needed()

                # Check for privacy links with enhanced query
                privacy_links = await current_page.evaluate(
                    """(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    
                    // Get all links in the footer
                    const links = Array.from(footer.querySelectorAll('a[href]'));
                    
                    // Score and filter links
                    const scoredLinks = links.map(link => {
                        const text = (link.innerText || link.textContent).toLowerCase().trim();
                        const href = link.href.toLowerCase();
                        
                        // Skip empty or javascript links
                        if (!text || href === '#' || href.startsWith('javascript:')) {
                            return { score: -1, href, text };
                        }
                        
                        // Calculate score
                        let score = 0;
                        
                        // Text scoring
                        if (text === 'privacy policy') score += 100;
                        else if (text === 'privacy notice') score += 95;
                        else if (text === 'privacy statement') score += 90;
                        else if (text === 'privacy') score += 85;
                        else if (text.includes('privacy policy')) score += 80;
                        else if (text.includes('privacy notice')) score += 75;
                        else if (text.includes('data protection')) score += 70;
                        else if (text.includes('privacy') && text.length < 20) score += 65;
                        else if (text.includes('data privacy')) score += 65;
                        else if (text.includes('privacy')) score += 50;
                        else if (text.includes('data policy')) score += 65;
                        else if (text.includes('personal data')) score += 65;
                        
                        // URL scoring
                        if (href.includes('privacy-policy')) score += 90;
                        else if (href.includes('privacy_policy')) score += 90;
                        else if (href.includes('privacy-notice')) score += 85;
                        else if (href.includes('privacy/policy')) score += 85;
                        else if (href.includes('data-protection')) score += 80;
                        else if (href.includes('data-privacy')) score += 80;
                        else if (href.includes('/privacy/')) score += 75;
                        else if (href.includes('/privacy')) score += 70;
                        else if (href.includes('/legal/privacy')) score += 70;
                        else if (href.includes('/legal/')) score += 50;
                        
                        // Only include links with score above threshold
                        return { 
                            score: score > 0 ? score : -1, 
                            href,
                            text,
                            target: link.getAttribute('target') || '',
                            rel: link.getAttribute('rel') || ''
                        };
                    })
                    .filter(item => item.score > 0)
                    .sort((a, b) => b.score - a.score);
                    
                    return scoredLinks.slice(0, 5); // Return top 5 links
                }""",
                    selector,
                )

                if privacy_links and len(privacy_links) > 0:
                    print(f"Found {len(privacy_links)} potential privacy links in footer")
                    # Process the top 5 links for better coverage
                    for link in privacy_links[:5]:
                        try:
                            # For links that open in new tabs, use direct navigation
                            if link['target'] == '_blank' or 'noopener' in link['rel'] or 'noreferrer' in link['rel']:
                                print(f"Footer link opens in new tab. Using direct navigation for: {link['text']}")
                                await current_page.goto(link['href'], timeout=4000, wait_until="domcontentloaded")
                                await current_page.wait_for_timeout(300)
                                return current_page.url, current_page, unverified_result
                            else:
                                # Try to match the link by different attributes for better reliability
                                element = await current_page.query_selector(
                                    f"a[href='{link['href']}'], a[href*='{link['href'].split('/')[-1]}']")
                            if element:
                                success = await click_and_wait_for_navigation(
                                        current_page, element, timeout=4000
                                )
                                if success:
                                        return current_page.url, current_page, unverified_result
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")
                            continue

        # Last resort: check for common privacy-related link patterns across the entire page
        try:
            privacy_anchors = await current_page.query_selector_all(
                "a[href*='privacy'], a[href*='data-protection'], a[href*='data-privacy'], a[href*='legal/privacy']"
            )
            
            if privacy_anchors and len(privacy_anchors) > 0:
                print(f"Found {len(privacy_anchors)} potential privacy links across the page")
                
                # Score the anchors
                scored_anchors = []
                for anchor in privacy_anchors:
                    try:
                        text = await anchor.text_content()
                        href = await anchor.get_attribute("href")
                        
                        if not text or not href:
                            continue
                            
                        text = text.lower().strip()
                        href = href.lower()
                        
                        # Skip navigation/pagination links
                        if text in ["next", "prev", "previous", "back", "forward"]:
                            continue
                            
                        # Score the anchor
                        score = 0
                        
                        # Text-based scoring
                        if "privacy policy" in text:
                            score += 100
                        elif "privacy notice" in text:
                            score += 95
                        elif "privacy statement" in text:
                            score += 90
                        elif text == "privacy":
                            score += 85
                        elif "privacy" in text:
                            score += 60
                        
                        # URL-based scoring
                        if "privacy-policy" in href:
                            score += 90
                        elif "privacy/policy" in href:
                            score += 85
                        elif "privacy/notice" in href:
                            score += 85
                        elif "/privacy/" in href:
                            score += 80
                        elif "/privacy" in href:
                            score += 75
                            
                        if score >= 50:
                            scored_anchors.append({"anchor": anchor, "score": score, "href": href})
                    except Exception:
                        continue
                
                # Sort by score (highest first)
                scored_anchors.sort(key=lambda x: x["score"], reverse=True)
                
                # Try the top 3 scored anchors
                for item in scored_anchors[:3]:
                    try:
                        success = await click_and_wait_for_navigation(
                            current_page, item["anchor"], timeout=4000
                        )
                        if success:
                            return current_page.url, current_page, unverified_result
                    except Exception as e:
                        print(f"Error clicking anchor: {e}")
                        try:
                            # Try direct navigation as fallback
                            await current_page.goto(item["href"], timeout=4000, wait_until="domcontentloaded")
                            await current_page.wait_for_timeout(300)
                            return current_page.url, current_page, unverified_result
                        except Exception as nav_e:
                            print(f"Direct navigation failed: {nav_e}")
                            continue
        except Exception as e:
            print(f"Error in last resort privacy link detection: {e}")

        # If all else failed, return null
        print("No privacy links found through smooth scroll")
        return None, current_page, unverified_result
    except Exception as e:
        print(f"Error in smooth_scroll_and_click_privacy: {e}")
    return None, current_page, unverified_result

async def bing_search_fallback_privacy(domain, page):
    """Search for privacy policy using Bing Search."""
    try:
        print("Attempting search engine fallback with Bing...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data protection" OR "privacy statement" -hiring -careers -job -recruitment -talent'

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
                    
                    // Penalties for specialized pages
                    if (urlLower.includes('/hiring') || 
                        urlLower.includes('/careers') || 
                        urlLower.includes('/jobs') || 
                        urlLower.includes('/talent') || 
                        urlLower.includes('/recruitment') ||
                        urlLower.includes('/applicants')) {
                        score -= 70; // Heavy penalty for hiring/careers pages
                    }
                    
                    // Penalties for other specialized privacy policies
                    if (urlLower.includes('/seller') || 
                        urlLower.includes('/developer') || 
                        urlLower.includes('/vendor') ||
                        urlLower.includes('/partners') ||
                        urlLower.includes('/suppliers')) {
                        score -= 40; // Penalty for specialized business relationship policies
                    }
                    
                    // Boost for main privacy policy paths
                    if (urlLower === domain.toLowerCase() + '/privacy' || 
                        urlLower === domain.toLowerCase() + '/privacy/' ||
                        urlLower === 'www.' + domain.toLowerCase() + '/privacy' ||
                        urlLower === 'www.' + domain.toLowerCase() + '/privacy/' ||
                        urlLower.includes('/privacy-policy') || 
                        urlLower.includes('/privacy/policy') ||
                        urlLower.includes('/privacy-center')) {
                        score += 40; // Boost for main paths
                    }
                    
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
                        
                        # Instead of hardcoding specific domains, use generic trustworthiness indicators
                        # to determine if we should accept the URL despite captcha
                        def evaluate_url_trustworthiness(url_str):
                            """Evaluate URL trustworthiness without hardcoding specific domains"""
                            try:
                                # Check for common patterns in high-quality domains
                                parsed_url = urlparse(url_str)
                                hostname = parsed_url.netloc.lower()
                                
                                # Score the URL based on generic factors rather than specific domains
                                trust_score = 0
                                
                                # Domain age/popularity indicators (via TLD)
                                if hostname.endswith(('.com', '.org', '.net', '.edu', '.gov')):
                                    trust_score += 20
                                elif hostname.endswith(('.io', '.co', '.me')):
                                    trust_score += 10
                                
                                # Length - very short domains are often well-established
                                domain_parts = hostname.split('.')
                                main_domain = domain_parts[0] if len(domain_parts) > 0 else ""
                                if len(main_domain) <= 8:  # Short domain names often indicate established sites
                                    trust_score += 15
                                
                                # Path quality for privacy pages
                                path = parsed_url.path.lower()
                                if "/privacy" in path:
                                    trust_score += 25
                                elif "/legal/" in path:
                                    trust_score += 20
                                elif "/policies/" in path:
                                    trust_score += 20
                                
                                # Additional trust signals
                                if "privacy-policy" in path:
                                    trust_score += 15
                                elif "privacy-notice" in path:
                                    trust_score += 15
                                elif "terms" in path and "privacy" in path:
                                    trust_score += 10
                                
                                return trust_score >= 45  # Threshold for trustworthiness
                            except Exception:
                                return False
                        
                        # Check if URL has privacy-related path elements
                        has_privacy_path = (
                            "privacy-policy" in original_url_lower or
                            "privacy-notice" in original_url_lower or
                            "data-protection" in original_url_lower or
                            "privacy" in original_url_lower or
                            "policies" in original_url_lower or
                            "legal/privacy" in original_url_lower
                        )
                        
                        # Use trust evaluation instead of hardcoded domains
                        is_trustworthy_url = evaluate_url_trustworthiness(best_result)
                        
                        if is_trustworthy_url and has_privacy_path and search_results[result_index]["score"] >= 60:
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

async def verify_link_validity(link, original_domain):
    """Verify if a link is a valid privacy policy by checking accessibility and content."""
    try:
        # Basic structure analysis
        score = 0
        link_lower = link.lower()
        
        # URL pattern scoring
        if "/privacy-policy" in link_lower or "/privacy_policy" in link_lower:
            score += 30
        elif "/privacy" in link_lower or "/data-privacy" in link_lower:
            score += 25
        elif "/legal/privacy" in link_lower:
            score += 25
        elif "/data-protection" in link_lower:
            score += 20
        elif "/legal" in link_lower:
            score += 15

        # Check if this is from the same domain
        try:
            link_domain = urlparse(link).netloc.replace('www.', '')
            original_domain = original_domain.replace('www.', '')
            if link_domain == original_domain:
                score += 25  # Boost for same domain
            elif link_domain.endswith(original_domain) or original_domain.endswith(link_domain):
                score += 15  # Partial match (subdomain)
        except Exception:
            pass  # Skip domain matching if there's an error
        
        # For links with non-zero score, try to verify with HTTP request
        if score > 0:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(link, timeout=5, allow_redirects=True) as response:
                        if response.status == 200:
                            # Read some content to verify it's a privacy page
                            content = await response.text(errors='ignore')
                            content_lower = content.lower()
                            
                            # Simple content verification
                            privacy_terms = ["privacy policy", "privacy notice", "data protection", 
                                           "data privacy", "personal information", "information we collect"]
                            
                            # Count how many privacy terms appear in the content
                            term_matches = sum(1 for term in privacy_terms if term in content_lower)
                            
                            if term_matches >= 2:
                                score += 40  # Strong boost for verified content
                            elif term_matches == 1:
                                score += 20  # Modest boost for partial verification
                            
                            # Additional score for privacy in title
                            if "<title" in content_lower and "privacy" in content_lower.split("<title")[1].split("</title>")[0]:
                                score += 15
                        else:
                            # Penalize inaccessible links
                            score -= 50
                    
            except Exception as e:
                # Penalize links that can't be accessed
                print(f"Error verifying link {link}: {e}")
                score -= 30
        
        return {"url": link, "score": score, "verified": score > 30}
    
    except Exception as e:
        print(f"Error in verify_link_validity: {e}")
        return {"url": link, "score": 0, "verified": False}

async def verify_privacy_page_result(page, url, result_url, result_type="scroll"):
    """Helper function to verify a potential privacy page with proper error handling."""
    try:
        # Navigate to the result URL if needed
        current_url = page.url
        if current_url != result_url:
            await page.goto(result_url, timeout=6000, wait_until="domcontentloaded")
        
        # Verify if it's a privacy page
        verification = await verify_is_privacy_page(page)
        
        if verification.get('isPrivacyPage', False):
            print(f"✅ Verified {result_type} result as privacy page with confidence: {verification.get('confidence', 0)}%")
            return {
                "verified": True,
                "confidence": verification.get('confidence', 0),
                "url": result_url
            }
        else:
            print(f"⚠️ {result_type} result not verified as privacy page. Adding to potential links.")
            return {
                "verified": False,
                "confidence": verification.get('confidence', 0),
                "url": result_url
            }
    except Exception as e:
        print(f"Error verifying {result_type} result: {e}")
        # Default fallback confidence based on result type
        default_confidence = 65 if result_type == "scroll" else 60
        return {
            "verified": False,
            "confidence": default_confidence,
            "url": result_url,
            "error": str(e)
        }

async def fallback_to_search_engines(url, domain, page):
    """Try to find privacy policy URL using search engines alone."""
    print("Using search engines as fallback for finding privacy policy")
    
    # Try all search engines
    results = []
    
    # Bing search
    try:
        bing_result = await bing_search_fallback_privacy(domain, page)
        if bing_result:
            results.append({"url": bing_result, "source": "bing"})
    except Exception as e:
        print(f"Error with Bing search: {e}")
    
    # DuckDuckGo search
    try:
        ddg_result = await duckduckgo_search_fallback_privacy(domain, page)
        if ddg_result:
            results.append({"url": ddg_result, "source": "duckduckgo"})
    except Exception as e:
        print(f"Error with DuckDuckGo search: {e}")
    
    # Yahoo search
    try:
        yahoo_result = await yahoo_search_fallback_privacy(domain, page)
        if yahoo_result:
            results.append({"url": yahoo_result, "source": "yahoo"})
    except Exception as e:
        print(f"Error with Yahoo search: {e}")
    
    # Look for matches between different search engines
    if len(results) > 1:
        for i in range(len(results)):
            for j in range(i+1, len(results)):
                if similar_urls(results[i]["url"], results[j]["url"]):
                    print(f"Match found between {results[i]['source']} and {results[j]['source']}: {results[i]['url']}")
                    return PrivacyResponse(
                        url=url,
                        pp_url=results[i]["url"],
                        success=True,
                        message=f"Found privacy policy confirmed by multiple search engines: {results[i]['url']}",
                        method_used="multi_search_match"
                    )
    
    # If no matches but we have results, return the first one
    if results:
        return PrivacyResponse(
            url=url,
            pp_url=results[0]["url"],
            success=True,
            message=f"Found privacy policy from {results[0]['source']}: {results[0]['url']}",
            method_used=f"{results[0]['source']}_search"
        )
    
    # If no results at all
    return PrivacyResponse(
        url=url,
        pp_url=None,
        success=False,
        message=f"Could not find privacy policy for {url} using any method.",
        method_used="all_methods_failed"
    )

async def get_search_engine_results(domain, page):
    """Get privacy policy search results from multiple search engines."""
    results = []
    
    # Bing search
    try:
        bing_result = await bing_search_fallback_privacy(domain, page)
        if bing_result:
            results.append(bing_result)
    except Exception as e:
        print(f"Error with Bing search: {e}")
    
    # DuckDuckGo search
    try:
        ddg_result = await duckduckgo_search_fallback_privacy(domain, page)
        if ddg_result:
            results.append(ddg_result)
    except Exception as e:
        print(f"Error with DuckDuckGo search: {e}")
    
    # Yahoo search
    try:
        yahoo_result = await yahoo_search_fallback_privacy(domain, page)
        if yahoo_result:
            results.append(yahoo_result)
    except Exception as e:
        print(f"Error with Yahoo search: {e}")
    
    return results

def similar_urls(url1, url2):
    """Check if two URLs are similar (accounting for www, trailing slashes, etc.)"""
    if not url1 or not url2:
        return False
    
    # Normalize URLs for comparison
    try:
        # Remove protocol, trailing slashes, and www
        u1 = urlparse(url1.lower())
        u2 = urlparse(url2.lower())
        
        netloc1 = u1.netloc.replace('www.', '')
        netloc2 = u2.netloc.replace('www.', '')
        
        path1 = u1.path.rstrip('/')
        path2 = u2.path.rstrip('/')
        
        # Check if domains and paths match
        if netloc1 == netloc2 and (path1 == path2 or 
                                  path1 in path2 or 
                                  path2 in path1):
            return True
            
        # Check for URL paths with same "privacy" component
        if netloc1 == netloc2 and "privacy" in path1 and "privacy" in path2:
            return True
    except Exception:
        return False
    
    return False