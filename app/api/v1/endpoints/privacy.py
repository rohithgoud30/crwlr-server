import random
from urllib.parse import urlparse
import logging
from typing import List, Optional, Union, Any
import asyncio
import re

from fastapi import APIRouter, HTTPException
from playwright.async_api import async_playwright, Page

from app.models.privacy import PrivacyRequest, PrivacyResponse

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

router = APIRouter()

logger = logging.getLogger(__name__)

# Define User_Agents list for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

"""
PRIVACY POLICY SCORING SYSTEM
=============================

The privacy policy finder uses a multi-layered scoring system to identify and rank potential
privacy policy pages. Here's how links are scored across different methods:

1. TITLE-BASED SCORING (0-150 points)
   ✅ "Privacy Notice" in title: 120 points (highest priority)
   ✅ "Privacy Policy" in title: 100 points
   ✅ "Privacy Statement" in title: 90 points 
   ✅ General "Privacy" in title: 70 points
   ✅ User/Customer mention in title: +30 bonus points (increased)
   ✅ Title is an exact match (not partial): +15 bonus points

2. URL-BASED SCORING (0-80 points)
   ✅ Contains "privacy-notice" or "privacy_notice": 60 points
   ✅ Contains "privacy-policy" or "privacy_policy": 50 points
   ✅ Contains just "privacy": 40 points
   ✅ Contains "user" or "customer": +20 bonus points (increased)
   ✅ Clean/short URL path (fewer segments): +10 bonus points

3. CONTEXT BONUSES (0-85 points)
   ✅ Both title has "privacy notice" AND URL has "privacy-notice": +30 points
   ✅ Found in page footer: +25 points (increased)
   ✅ URL contains "/legal/": +15 points
   ✅ On main domain (not subdomain): +15 points
   ✅ From official site navigation: +20 points

4. CONTENT ANALYSIS (0-100 points)
   ✅ Contains user/customer related phrases: +15 points each (increased)
      - "user data", "customer data"
      - "user information", "customer information" 
      - "user rights", "customer rights"
      - "user preferences", "customer preferences"
   ✅ General mention of "user" or "customer": +20 points (increased)
   ✅ Content length appropriate for privacy policy: +15 points
   ✅ Contains sections about data collection/usage: +20 points

5. VERIFICATION CONFIDENCE (0-100 points)
   Pages are evaluated on:
   ✅ Strong privacy indicators in title/URL: up to 65 points
   ✅ Privacy sections identified in content: up to 20 points
   ✅ Privacy phrases found in content: up to 15 points
   ✅ Content length and structure analysis: up to 10 points
   ❌ Penalty for negative indicators: -10 to -30 points

6. SEARCH ENGINE RESULTS
   Results from search engines are scored similarly with title and URL matching.
   Results supported by multiple methods receive a significant boost.

7. SPECIAL CASE HANDLING
   ✅ Links confirmed by multiple methods: +50 points bonus
   ✅ High-scoring footer links (150+ points): prioritized immediately
   ✅ Privacy Notice links are consistently scored higher than other terminology
   ✅ When scores are tied, footer links are preferred

8. EXTERNAL AUTHORITY SIGNALS
   ✅ Link appears in search results: +25 points
   ✅ Link verified by multiple search engines: +40 points

The final decision takes into account both the raw scores and the verification confidence.
Links with scores above thresholds (typically 150-200+) are considered very reliable candidates.
This scoring system is unbounded - scores can exceed 300+ points for very strong matches.
"""

# Priorities for privacy policy matching terms
exactMatchPriorities = {
    "privacy policy": 100,
    "privacy notice": 120,  # Increased over Privacy Policy
    "privacy statement": 90,
    "data policy": 85,
    "data privacy policy": 80,
    "privacy": 75,
}

# Priorities for partial match terms
partialMatchPriorities = {
    "data protection": 70,
    "personal information": 65,
    "data collection": 60,
    "privacy practices": 55,
    "privacy rights": 50,
    "data processing": 45,
    "gdpr": 40,
    "ccpa": 35,
}

# Define strong match terms for privacy
strong_privacy_matches = [
    "privacy policy",
    "privacy notice",
    "privacy statement",
    "data policy",
    "data privacy",
    "privacy",
    "data protection",
    "personal data",
]

# User and customer related terms to boost scoring
user_customer_terms = [
    "user privacy",
    "customer privacy", 
    "user data protection",
    "customer data protection",
    "user information",
    "customer information",
    "user rights",
    "customer rights",
    "user preferences",
    "user choices",
    "customer preferences",
    "customer choices",
    "user consent",
    "customer consent",
    "user data",
    "customer data",
]

# Define dynamic scoring weights for link evaluation
LINK_EVALUATION_WEIGHTS = {
    "text_match": 0.5,
    "url_structure": 0.5,
    "context": 0.5,
    "position": 0.1,
}

# Special content indicators that deserve extra points - ENHANCED for user/customer terms
content_value_indicators = [
    # User/customer focused indicators - HIGHEST PRIORITY
    "user privacy rights",
    "customer privacy rights",
    "user data collection",
    "customer data collection",
    "user data processing",
    "customer data processing",
    "user data retention",
    "customer data retention",
    "user consent management",
    "customer consent management",
    "user preferences management",
    "customer preferences management",
    "user access rights",
    "customer access rights",
    "user data deletion",
    "customer data deletion",
    "user information sharing",
    "customer information sharing",
    
    # Standard privacy policy indicators
    "data processing purposes",
    "legal basis",
    "data retention period",
    "data protection officer",
    "data controller",
    "right to be forgotten",
    "right to erasure",
    "right to access",
    "data subject rights",
    "data processing activities",
    "data protection principles",
    "data transfer safeguards",
    "cookie preferences",
    "tracking technologies",
    "data subject access request",
    "data protection impact assessment",
]

privacy_policy_terms = [
    'privacy policy', 'privacy statement', 'data protection', 
    'privacy practices', 'privacy rights'
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
            if domain.count(
                ".") == 1:  # Only one dot indicates a likely main domain
                return url.replace(domain, f"www.{domain}")

        return url
    except Exception as e:
        print(f"Error normalizing domain: {e}")
        return url  # Return original URL if parsing fails


async def find_user_customer_privacy_links(page):
    """Special function to prioritize user/customer privacy links above all else."""
    try:
        print("🔍🔍🔍 SUPREME PRIORITY CHECK: Looking for user/customer privacy links...")
        # Use a targeted CSS selector just for user/customer privacy links
        user_customer_links = await page.query_selector_all(
            'a[href*="user-privacy"], a[href*="user_privacy"], ' + 
            'a[href*="user"][href*="privacy"], a[href*="customer-privacy"], ' + 
            'a[href*="customer_privacy"], a[href*="customer"][href*="privacy"]'
        )
        
        if user_customer_links and len(user_customer_links) > 0:
            print(f"💎💎💎 SUPREME PRIORITY: Found {len(user_customer_links)} direct user/customer privacy links!")
            
            # Score the links to prioritize them
            scored_links = []
            
            for link in user_customer_links:
                try:
                    href = await link.get_attribute("href")
                    text = await link.text_content() or ""
                    text = text.lower().strip()
                    
                    if not href:
                        continue
                        
                    # Calculate a score for each link
                    score = 0
                    
                    # Scores for text
                    if text == "user privacy notice" or text == "user privacy policy":
                        score += 1000
                    elif "user privacy" in text:
                        score += 800
                    elif text == "customer privacy notice" or text == "customer privacy policy":
                        score += 600
                    elif "customer privacy" in text:
                        score += 400
                    
                    # Scores for URL
                    href_lower = href.lower()
                    if "user-privacy" in href_lower or "user_privacy" in href_lower:
                        score += 500
                    elif "user" in href_lower and "privacy" in href_lower:
                        score += 400
                    elif "customer-privacy" in href_lower or "customer_privacy" in href_lower:
                        score += 300
                    elif "customer" in href_lower and "privacy" in href_lower:
                        score += 200
                        
                    # Any link with user/customer terms should get minimum baseline score
                    if score == 0 and ("user" in href_lower or "customer" in href_lower):
                        score += 100
                    
                    # Print details about high-scoring links
                    if score >= 100:
                        print(f"User/Customer Link: '{text}' - {href} (Score: {score})")
                        scored_links.append({"link": link, "text": text, "href": href, "score": score})
                except Exception as e:
                    print(f"Error scoring user/customer link: {e}")
                    continue
            
            # Sort by score
            scored_links.sort(key=lambda x: x["score"], reverse=True)
            
            # Try the highest scoring links
            for scored_link in scored_links[:3]:  # Try the top 3 links
                link = scored_link["link"]
                href = scored_link["href"]
                text = scored_link["text"]
                score = scored_link["score"]
                
                print(f"⭐⭐⭐ Trying SUPREME PRIORITY user/customer link: {text} - {href} (Score: {score})")
                try:
                    success = await click_and_wait_for_navigation(page, link, timeout=5000)
                    if success:
                        print(f"✓✓✓ Successfully navigated to USER/CUSTOMER privacy link: {page.url}")
                        return page.url
                except Exception as e:
                    print(f"Error navigating to user/customer link: {e}")
                    continue
            
            # If navigation failed for all links, return the best URL anyway
            if scored_links:
                best_link = scored_links[0]["href"]
                print(f"⚠️ Navigation failed, but returning best user/customer link: {best_link}")
                return best_link
        
        return None
    except Exception as e:
        print(f"Error in user/customer privacy search: {e}")
        return None

@router.post("/privacy", response_model=PrivacyResponse)
async def find_privacy_policy(request: PrivacyRequest) -> PrivacyResponse:
    """Find Privacy Policy page for a given URL."""
    original_url = request.url
    
    if not original_url:
        raise HTTPException(status_code=400, detail="URL is required")
    
    # First sanitize the URL to handle malformed URLs
    url = sanitize_url(original_url)
    
    if not url:
        print(f"Invalid URL detected: {original_url}")
        return PrivacyResponse(
            url=original_url,
            pp_url=None,
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
                
        # HIGHEST PRIORITY: First try to find user/customer privacy links directly
        # This is done BEFORE any other methods to ensure these links get absolute priority
        print("Running SUPREME PRIORITY user/customer privacy link detection first...")
        user_customer_link = await find_user_customer_privacy_links(page)
        if user_customer_link:
            print(f"\n\n⭐⭐⭐ SUPREME PRIORITY SUCCESS: Found user/customer privacy link: {user_customer_link}")
            # Return immediately with this high priority link
            return PrivacyResponse(
                url=original_url,
                pp_url=user_customer_link,
                success=True,
                message="Found User/Customer Privacy Policy (HIGHEST PRIORITY)",
                method_used="user_customer_supreme_priority"
            )

        all_links = []
        method_sources = []

        # 1. JavaScript method - Highest priority
        print("Trying find_all_links_js approach...")
        try:
            js_result, page, js_unverified = await find_privacy_links_js(page, context, None)
            if js_result:
                all_links.append(js_result)
                method_sources.append((js_result, "javascript"))

                # Track if this is a high-scoring footer link
                try:
                    # Check if this looks like a privacy notice from the title
                    await page.goto(js_result, timeout=5000, wait_until="domcontentloaded")
                    page_title = await page.title()
                    title_lower = page_title.lower()

                    # If the title contains key privacy terms, mark it as a
                    # high-score footer link
                    if ('privacy notice' in title_lower or
                        'privacy policy' in title_lower or
                        'privacy statement' in title_lower):
                        print(f"✅ Found high-score footer link with privacy-related title: {js_result}")
                        high_score_footer_link = js_result
                except Exception as e:
                    print(f"Error checking footer link title: {e}")

        except Exception as e:
            print(f"Error in JavaScript method: {e}")

        # 2. Scroll method - Second highest priority
        print("Trying smooth_scroll_and_click approach...")
        try:
            scroll_result, page, scroll_unverified = await smooth_scroll_and_click_privacy(page, context, js_unverified if 'js_unverified' in locals() else None)
            if scroll_result:
                all_links.append(scroll_result)
                method_sources.append((scroll_result, "scroll"))

                # If no high-score footer link yet, check this one
                if not high_score_footer_link:
                    try:
                        await page.goto(scroll_result, timeout=5000, wait_until="domcontentloaded")
                        page_title = await page.title()
                        title_lower = page_title.lower()

                        if ('privacy notice' in title_lower or
                            'privacy policy' in title_lower or
                            'privacy statement' in title_lower):
                            print(f"✅ Found high-score scroll link with privacy-related title: {scroll_result}")
                            high_score_footer_link = scroll_result
                    except Exception as e:
                        print(f"Error checking scroll link title: {e}")
        except Exception as e:
            print(f"Error in scroll method: {e}")

        # If we have a high-score footer link with privacy-related title, check if it's a user or customer one first
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
                print(f"🚀🚀🚀 Found user/customer terms in link - PRIORITIZING OVER STANDARD PRIVACY LINK: {user_customer_link}")
                try:
                    # Try to navigate to it
                    await page.goto(user_customer_link, timeout=5000, wait_until="domcontentloaded")
                    return PrivacyResponse(
                        url=original_url,
                        pp_url=user_customer_link,
                        success=True,
                        message="Found User/Customer Privacy Policy (highest priority)",
                        method_used=method
                    )
                except Exception as e:
                    print(f"Error navigating to user/customer link: {e}")
                    # Still return it even if navigation fails
                    return PrivacyResponse(
                        url=original_url,
                        pp_url=user_customer_link,
                        success=True,
                        message="Found User/Customer Privacy Policy (navigation failed but high confidence)",
                        method_used=method + "_nav_failed"
                    )
            
            # Otherwise proceed with the high score footer link
            print(f"Prioritizing high-score footer link with privacy-related title: {high_score_footer_link}")
            return PrivacyResponse(
                url=original_url,
                pp_url=high_score_footer_link,
                success=True,
                message="Found Privacy Policy using high-priority footer link with privacy title",
                method_used="footer_title_match"
            )

        # 3. Search engine methods - Lower priority
        # Define search engine functions at the point of use to avoid undefined errors
        search_engines = []
        # These will be populated after all search fallback functions are defined
        
        # Store search results
        search_results = []
        
        # Try Bing search
        try:
            print("Trying Bing search fallback...")
            bing_result = await bing_search_fallback(domain, page)
            if bing_result:
                search_results.append(bing_result)
                all_links.append(bing_result)
                method_sources.append((bing_result, "Bing"))
        except Exception as e:
            print(f"Error with Bing search: {e}")
        
        # Try Yahoo search
        try:
            print("Trying Yahoo search fallback...")
            yahoo_result = await yahoo_search_fallback(domain, page)
            if yahoo_result:
                search_results.append(yahoo_result)
                all_links.append(yahoo_result)
                method_sources.append((yahoo_result, "Yahoo"))
        except Exception as e:
            print(f"Error with Yahoo search: {e}")
        
        # Try DuckDuckGo search
        try:
            print("Trying DuckDuckGo search fallback...")
            ddg_result = await duckduckgo_search_fallback(domain, page)
            if ddg_result:
                search_results.append(ddg_result)
                all_links.append(ddg_result)
                method_sources.append((ddg_result, "DuckDuckGo"))
        except Exception as e:
            print(f"Error with DuckDuckGo search: {e}")

        # Add a special priority check for footer links from the main domain
        # This ensures footer links from the main domain get highest priority
        js_main_domain_footer = None
        scroll_main_domain_footer = None
        
        # Check if JS unverified result is from main domain
        if 'js_unverified' in locals() and js_unverified:
            js_parsed = urlparse(js_unverified)
            js_domain = js_parsed.netloc.replace('www.', '')
            if js_domain == domain.replace('www.', ''):
                js_main_domain_footer = js_unverified
                print(f"✅ Found main domain footer link from JS: {js_main_domain_footer}")
        
        # Check if scroll unverified result is from main domain
        if 'scroll_unverified' in locals() and scroll_unverified:
            scroll_parsed = urlparse(scroll_unverified)
            scroll_domain = scroll_parsed.netloc.replace('www.', '')
            if scroll_domain == domain.replace('www.', ''):
                scroll_main_domain_footer = scroll_unverified
                print(f"✅ Found main domain footer link from scroll: {scroll_main_domain_footer}")
        
        # Prioritize main domain footer links
        main_domain_footer = js_main_domain_footer or scroll_main_domain_footer
        if main_domain_footer:
            print(f"⭐ Prioritizing main domain footer link: {main_domain_footer}")
            try:
                await page.goto(main_domain_footer, timeout=10000, wait_until="domcontentloaded")
                title = await page.title()
                if 'privacy' in title.lower():
                    print(f"✅ Confirmed main domain footer link has privacy-related title")
                    return PrivacyResponse(
                        url=original_url,
                        pp_url=main_domain_footer,
                        success=True,
                        message="Found Privacy Policy using main domain footer link",
                        method_used="main_domain_footer"
                    )
            except Exception as e:
                print(f"Error checking main domain footer link: {e}")

        # Track the best footer link and its score
        best_footer_link = None
        if 'js_unverified' in locals() and js_unverified:
            best_footer_link = js_unverified
        elif 'scroll_unverified' in locals() and scroll_unverified:
            best_footer_link = scroll_unverified

        # If we have a footer link, check its title for privacy terms
        if best_footer_link:
            try:
                print(f"Checking best footer link: {best_footer_link}")
                await page.goto(best_footer_link, timeout=10000, wait_until="domcontentloaded")

                # Check title and URL for privacy terms
                title = await page.title()
                title_lower = title.lower()
                url_lower = page.url.lower()

                privacy_title_score = 0
                if 'privacy notice' in title_lower or 'privacy policy' in title_lower:
                    privacy_title_score = 100
                elif 'privacy statement' in title_lower:
                    privacy_title_score = 90
                elif 'privacy' in title_lower:
                    privacy_title_score = 80
                
                # Extra points for user/customer mentions in title - EXTREME PRIORITY INCREASE
                user_customer_bonus = 0
                if 'user privacy notice' in title_lower:
                    user_customer_bonus = 5000  # EXTREME priority for "user privacy notice"
                    print(f"✓ EXTREME PRIORITY 'user privacy notice' in title: +5000 points")
                elif 'user privacy policy' in title_lower:
                    user_customer_bonus = 4000  # Very extreme priority
                    print(f"✓ VERY EXTREME priority 'user privacy policy' in title: +4000 points")
                elif 'user privacy' in title_lower:
                    user_customer_bonus = 3000  # Ultra-high priority for "user privacy"
                    print(f"✓ ULTRA-HIGH priority 'user privacy' in title: +3000 points")
                elif 'customer privacy notice' in title_lower:
                    user_customer_bonus = 2500  # Super-high for "customer privacy notice"
                    print(f"✓ SUPER-HIGH priority 'customer privacy notice' in title: +2500 points")
                elif 'customer privacy policy' in title_lower:
                    user_customer_bonus = 2000  # Super high for "customer privacy policy"
                    print(f"✓ SUPER-HIGH priority 'customer privacy policy' in title: +2000 points")
                elif 'customer privacy' in title_lower:
                    user_customer_bonus = 1500  # Very high for "customer privacy"
                    print(f"✓ VERY HIGH priority 'customer privacy' in title: +1500 points")
                elif 'user' in title_lower and 'privacy' in title_lower:
                    user_customer_bonus = 1000  # Very high priority for user + privacy
                    print(f"✓ VERY HIGH priority 'user' + 'privacy' in title: +1000 points")
                elif 'customer' in title_lower and 'privacy' in title_lower:
                    user_customer_bonus = 800  # High priority for customer + privacy
                    print(f"✓ HIGH priority 'customer' + 'privacy' in title: +800 points")
                elif 'user' in title_lower:
                    user_customer_bonus = 500  # Good bonus just for "user"
                    print(f"✓ GOOD priority 'user' mention in title: +500 points")
                elif 'customer' in title_lower:
                    user_customer_bonus = 400  # Good bonus just for "customer"
                    print(f"✓ GOOD priority 'customer' mention in title: +400 points")

                privacy_url_score = 0
                if 'privacy-policy' in url_lower or 'privacy_policy' in url_lower:
                    privacy_url_score = 50
                elif 'privacy-notice' in url_lower or 'privacy_notice' in url_lower:
                    privacy_url_score = 48
                elif 'privacy' in url_lower:
                    privacy_url_score = 40
                
                # Extra points for user/customer in URL - EXTREME PRIORITY INCREASE
                url_user_customer_bonus = 0
                if 'user-privacy-notice' in url_lower or 'user_privacy_notice' in url_lower:
                    url_user_customer_bonus = 5000  # EXTREME priority for user-privacy-notice in URL
                    print(f"✓ EXTREME PRIORITY 'user-privacy-notice' in URL: +5000 points")
                elif 'user-privacy-policy' in url_lower or 'user_privacy_policy' in url_lower:
                    url_user_customer_bonus = 4000  # VERY EXTREME priority
                    print(f"✓ VERY EXTREME priority 'user-privacy-policy' in URL: +4000 points")
                elif 'user-privacy' in url_lower or 'user_privacy' in url_lower:
                    url_user_customer_bonus = 3000  # ULTRA-HIGH priority for user-privacy in URL
                    print(f"✓ ULTRA-HIGH priority 'user-privacy' in URL: +3000 points")
                elif 'customer-privacy-notice' in url_lower or 'customer_privacy_notice' in url_lower:
                    url_user_customer_bonus = 2500  # SUPER-HIGH priority for customer-privacy-notice
                    print(f"✓ SUPER-HIGH priority 'customer-privacy-notice' in URL: +2500 points")
                elif 'customer-privacy-policy' in url_lower or 'customer_privacy_policy' in url_lower:
                    url_user_customer_bonus = 2000  # SUPER-HIGH priority
                    print(f"✓ SUPER-HIGH priority 'customer-privacy-policy' in URL: +2000 points")
                elif 'customer-privacy' in url_lower or 'customer_privacy' in url_lower:
                    url_user_customer_bonus = 1500  # VERY HIGH priority for customer-privacy
                    print(f"✓ VERY HIGH priority 'customer-privacy' in URL: +1500 points")
                elif 'user' in url_lower and 'privacy' in url_lower:
                    url_user_customer_bonus = 1000  # HIGH priority for user + privacy
                    print(f"✓ HIGH priority 'user' + 'privacy' in URL: +1000 points")
                elif 'customer' in url_lower and 'privacy' in url_lower:
                    url_user_customer_bonus = 800  # GOOD priority for customer + privacy
                    print(f"✓ GOOD priority 'customer' + 'privacy' in URL: +800 points")
                elif 'user' in url_lower:
                    url_user_customer_bonus = 500  # BASE priority just for user
                    print(f"✓ BASE priority 'user' mention in URL: +500 points")
                elif 'customer' in url_lower:
                    url_user_customer_bonus = 400  # BASE priority just for customer
                    print(f"✓ BASE priority 'customer' mention in URL: +400 points")
                
                # Add a main domain bonus
                main_domain_bonus = 0
                best_footer_parsed = urlparse(best_footer_link)
                footer_domain = best_footer_parsed.netloc.replace('www.', '')
                if footer_domain == domain.replace('www.', ''):
                    main_domain_bonus = 80  # Increased from 50 to 80
                    print(f"✓ Footer link is from main domain: +80 bonus points")

                total_score = privacy_title_score + privacy_url_score + main_domain_bonus + user_customer_bonus + url_user_customer_bonus
                print(f"Footer link score: {total_score} (Title: {privacy_title_score}, URL: {privacy_url_score}, Domain: {main_domain_bonus}, User/Customer Title: {user_customer_bonus}, User/Customer URL: {url_user_customer_bonus})")

                # If medium to high-scoring footer link (70+), use it directly
                if total_score >= 70:
                    print(f"✅ Using high-scoring footer link: {best_footer_link} (Score: {total_score})")
                    return PrivacyResponse(
                        url=original_url,
                        pp_url=best_footer_link,
                        success=True,
                        message="Found Privacy Policy using high-scoring footer link",
                        method_used="high_score_footer"
                    )
            except Exception as e:
                print(f"Error analyzing footer link: {e}")

        # Check if any js_unverified or scroll_unverified has user/customer terms
        js_scroll_with_user_terms = []
        if 'js_unverified' in locals() and js_unverified:
            if any(term in js_unverified.lower() for term in ['user', 'customer']):
                js_scroll_with_user_terms.append((js_unverified, "javascript_with_user_terms"))
        if 'scroll_unverified' in locals() and scroll_unverified:
            if any(term in scroll_unverified.lower() for term in ['user', 'customer']):
                js_scroll_with_user_terms.append((scroll_unverified, "scroll_with_user_terms"))
                
        # If we have any links with user/customer terms, prioritize them immediately
        if js_scroll_with_user_terms:
            best_link, method = js_scroll_with_user_terms[0] # Take the first one
            print(f"⭐⭐⭐ Found unverified link with user/customer terms: {best_link}")
            try:
                await page.goto(best_link, timeout=10000, wait_until="domcontentloaded")
                # No need to verify content - presence of user/customer in URL is enough
                return PrivacyResponse(
                    url=original_url,
                    pp_url=best_link,
                    success=True,
                    message="Found Privacy Policy containing user/customer terms in URL",
                    method_used=method
                )
            except Exception as e:
                print(f"Error navigating to user/customer link: {e}")
                # Return anyway as high confidence
                return PrivacyResponse(
                    url=original_url,
                    pp_url=best_link,
                    success=True,
                    message="Found Privacy Policy with user/customer terms (navigation failed but high confidence)",
                    method_used=method + "_nav_failed"
                )
                
        # Compare JS/scroll results with search results to find overlaps
        high_priority_links = []
        if 'js_unverified' in locals() and js_unverified:
            high_priority_links.append(js_unverified)
        if 'scroll_unverified' in locals() and scroll_unverified:
            high_priority_links.append(scroll_unverified)

        # Check for overlaps between high priority links and search results
        for hp_link in high_priority_links:
            if not hp_link:
                continue

            for search_link in search_results:
                if not search_link:
                    continue

                # Compare URLs (normalize for comparison)
                hp_parsed = urlparse(hp_link)
                search_parsed = urlparse(search_link)

                # Remove trailing slashes and compare
                hp_path = hp_parsed.path.rstrip('/')
                search_path = search_parsed.path.rstrip('/')

                if (hp_parsed.netloc == search_parsed.netloc and
                    (hp_path == search_path or
                     hp_path.lower().endswith(search_path.lower()) or
                     search_path.lower().endswith(hp_path.lower()))):
                    print(f"✅ Found overlap between high priority link and search result: {hp_link}")
                    return PrivacyResponse(
                        url=original_url,
                        pp_url=hp_link,
                        success=True,
                        message="Found Privacy Policy (confirmed by multiple methods)",
                        method_used="multiple_confirmation"
                    )

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
        unique_sources = [src for i, src in enumerate(
            unique_sources) if unique_links[i] in unique_links]

        # === HIGHEST PRIORITY: user/customer privacy links ===
        # Check for user/customer privacy terms in URL or title and return immediately if found
        user_customer_terms = [
            'user-privacy', 'customer-privacy', 'user_privacy', 'customer_privacy',
            'user privacy', 'customer privacy'
        ]
        for link in unique_links:
            link_lower = link.lower()
            if any(term in link_lower for term in user_customer_terms):
                try:
                    await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                    title = await page.title()
                    title_lower = title.lower()
                    if any(term in title_lower for term in user_customer_terms) or any(term in link_lower for term in user_customer_terms):
                        print(f"🚀 Returning user/customer privacy link immediately: {link}")
                        return PrivacyResponse(
                            url=original_url,
                            pp_url=link,
                            success=True,
                            message="Found User/Customer Privacy Policy (highest priority)",
                            method_used="user_customer_privacy"
                        )
                except Exception as e:
                    print(f"Error verifying user/customer privacy link: {e}")
                    continue
        # === END HIGHEST PRIORITY ===

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
                
                # Add a domain bonus - strongly prefer main domain links
                domain_bonus = 0
                link_parsed = urlparse(link)
                link_domain = link_parsed.netloc.replace('www.', '')
                if link_domain == domain.replace('www.', ''):
                    domain_bonus = 80  # Increased from 50 to 80
                    print(f"✓ Main domain bonus for {link}: +80 points")
                
                # Special bonus for user/customer in link URL
                user_customer_link_bonus = 0
                link_url_lower = link.lower()
                if 'user-privacy-notice' in link_url_lower or 'user_privacy_notice' in link_url_lower:
                    user_customer_link_bonus = 100
                    print(f"✓ Special bonus for 'user-privacy-notice' in URL: +100 points")
                elif 'user-privacy' in link_url_lower or 'user_privacy' in link_url_lower:
                    user_customer_link_bonus = 80
                    print(f"✓ Special bonus for 'user-privacy' in URL: +80 points")
                elif 'user' in link_url_lower and 'privacy' in link_url_lower:
                    user_customer_link_bonus = 60
                    print(f"✓ Special bonus for 'user' + 'privacy' in URL: +60 points")
                elif 'customer-privacy' in link_url_lower or 'customer_privacy' in link_url_lower:
                    user_customer_link_bonus = 70
                    print(f"✓ Special bonus for 'customer-privacy' in URL: +70 points")
                elif 'customer' in link_url_lower and 'privacy' in link_url_lower:
                    user_customer_link_bonus = 50
                    print(f"✓ Special bonus for 'customer' + 'privacy' in URL: +50 points")
                
                # Add a source bonus - prefer JS and scroll results which are more likely to be footers
                source_bonus = 0
                if src in ["javascript", "scroll"]:
                    source_bonus = 30  # Increased from 20 to 30
                    print(f"✓ JS/Scroll source bonus for {link}: +30 points")
                
                # Calculate final score with bonuses
                final_score = score + domain_bonus + source_bonus + user_customer_link_bonus
                
                scored_links.append({
                    "url": link,
                    "source": src,
                    "score": final_score,
                    "original_score": score,
                    "domain_bonus": domain_bonus,
                    "source_bonus": source_bonus,
                    "user_customer_bonus": user_customer_link_bonus,
                    "is_privacy": is_privacy,
                })
                
                print(f"Final score for {link}: {final_score} (Base: {score}, Domain: {domain_bonus}, Source: {source_bonus}, User/Customer: {user_customer_link_bonus})")
            except Exception as e:
                print(f"Error verifying link {link}: {e}")
                continue

        # Sort by score (confidence), prefer is_privacy True
        scored_links.sort(
            key=lambda x: (
                x["is_privacy"],
                x["score"]),
            reverse=True)

        # Prioritize JS and scroll methods if they have decent scores
        js_scroll_results = [
            link for link in scored_links if link["source"] in [
                "javascript", "scroll"]]
        
        # Extra check for main domain JS/scroll results - give them highest priority
        main_domain_js_scroll = [link for link in js_scroll_results 
                               if urlparse(link["url"]).netloc.replace('www.', '') == domain.replace('www.', '')]
        
        if main_domain_js_scroll:
            best = main_domain_js_scroll[0]
            print(f"⭐ Using main domain {best['source']} result as highest priority: {best['url']}")
            return PrivacyResponse(
                url=url,
                pp_url=best["url"],
                success=True,
                message=f"Found Privacy Policy using main domain {best['source']} method",
                method_used=f"main_domain_{best['source']}"
            )

        # If we have high-scoring JS or scroll results, prioritize those
        high_priority_results = [
            link for link in js_scroll_results if link["score"] >= 70]

        if high_priority_results:
            best = high_priority_results[0]
            return PrivacyResponse(
                url=url,
                pp_url=best["url"],
                success=True,
                message=f"Found Privacy Policy using high-priority {best['source']} method",
                method_used=best["source"]
            )

        # If we have any verified result at all
        if scored_links:
            best = scored_links[0]
            return PrivacyResponse(
                url=url,
                pp_url=best["url"],
                success=True,
                message=f"Found Privacy Policy using {best['source']} method (highest confidence)",
                method_used=best["source"])

        # Last resort: If we have unverified footer links from JS scan, use them
        # Check which unverified link has the best title match
        unverified_links = []
        if 'js_unverified' in locals() and js_unverified:
            unverified_links.append((js_unverified, "javascript_footer"))
        if 'scroll_unverified' in locals() and scroll_unverified:
            unverified_links.append((scroll_unverified, "scroll_footer"))

        # If we have unverified footer links, select the best one
        if unverified_links:
            best_score = 0
            best_link = None
            best_method = None

            for link, method in unverified_links:
                try:
                    await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                    title = await page.title()
                    title_lower = title.lower()
                    
                    # Get page content to look for user/customer related keywords
                    page_content = await page.evaluate("() => document.body.innerText.toLowerCase()")
                    
                    score = 0
                    # Score by title - prioritize "Privacy Notice"
                    if 'privacy notice' in title_lower:
                        score += 120  # Higher score for Privacy Notice specifically
                    elif 'privacy policy' in title_lower:
                        score += 100
                    elif 'privacy statement' in title_lower:
                        score += 90
                    elif 'privacy' in title_lower:
                        score += 70
                    
                    # Extra points for user/customer mentions in title
                    if ('user' in title_lower or 'customer' in title_lower):
                        score += 30  # Increased bonus for user/customer mentions in title
                    
                    # Exact match bonus
                    if title_lower == 'privacy notice' or title_lower == 'privacy policy':
                        score += 15  # Extra points for exact title match
                    
                    # Score by URL - prioritize "privacy-notice"
                    if 'privacy-notice' in url_lower or 'privacy_notice' in url_lower:
                        score += 60  # Higher score for privacy-notice URLs
                    elif 'privacy-policy' in url_lower or 'privacy_policy' in url_lower:
                        score += 50
                    elif 'privacy' in url_lower:
                        score += 40
                    
                    # Extra points for user/customer in URL
                    if ('user' in url_lower or 'customer' in url_lower):
                        score += 20  # Increased bonus for user/customer mentions in URL
                    
                    # Clean URL path bonus
                    url_path_parts = url_lower.split('/')
                    if len(url_path_parts) <= 5:  # Relatively short/clean URL path
                        score += 10
                    
                    # Additional boost for both title and URL containing privacy notice
                    if 'privacy notice' in title_lower and ('privacy-notice' in url_lower or 'privacy_notice' in url_lower):
                        score += 30  # Extra boost when both match
                    
                    # Main domain bonus
                    parsed_url = urlparse(link)
                    if not parsed_url.netloc.startswith('www.') and parsed_url.netloc.count('.') == 1:
                        score += 15  # On main domain, not subdomain
                    
                    # Footer location bonus (we're already checking footer links)
                    score += 25  # Increased boost for footer links
                    
                    # Legal directory boost
                    if '/legal/' in url_lower:
                        score += 15
                    
                    # Scan content for specific terms related to users/customers
                    user_customer_phrases = [
                        'user data', 
                        'customer data',
                        'user information', 
                        'customer information',
                        'user rights', 
                        'customer rights',
                        'user preferences', 
                        'customer preferences',
                        'user consent',
                        'customer consent'
                    ]
                    
                    # Add points for user/customer content mentions
                    for phrase in user_customer_phrases:
                        if phrase in page_content:
                            score += 15  # Increased points for each specific user/customer phrase
                            print(f"Found user/customer phrase: {phrase}")
                    
                    # General bonus for any mention of users/customers
                    if 'user' in page_content or 'customer' in page_content:
                        score += 20  # Increased general bonus for user/customer mentions
                    
                    # Content length appropriateness
                    content_length = len(page_content)
                    if 3000 <= content_length <= 50000:  # Typical privacy policy length
                        score += 15
                    
                    # Check for key privacy policy content indicators - ENHANCED for meaning analysis
                    user_customer_indicators = 0
                    standard_indicators = 0
                    
                    # First check user/customer indicators (first 18 items in the list)
                    for indicator in content_value_indicators[:18]:
                        if indicator in page_content:
                            user_customer_indicators += 1
                            print(f"Found user/customer content indicator: {indicator}")
                    
                    # Then check standard indicators
                    for indicator in content_value_indicators[18:]:
                        if indicator in page_content:
                            standard_indicators += 1
                    
                    # Give FULL points for user/customer indicators
                    if user_customer_indicators > 0:
                        user_customer_indicator_points = user_customer_indicators * 15  # 15 points per user/customer indicator
                        score += user_customer_indicator_points
                        print(f"✓ User/customer content indicators: +{user_customer_indicator_points} points ({user_customer_indicators} indicators)")
                    
                    # Add points based on standard content quality
                    if standard_indicators >= 5:
                        score += 30  # Increased from 20 - High quality content with many indicators
                        print(f"✓ High-quality standard content indicators: +30 points ({standard_indicators} indicators)")
                    elif standard_indicators >= 3:
                        score += 20  # Increased from 10 - Medium quality content
                        print(f"✓ Medium-quality standard content indicators: +20 points ({standard_indicators} indicators)")
                    elif standard_indicators >= 1:
                        score += 10  # New category - Basic content quality
                        print(f"✓ Basic standard content indicators: +10 points ({standard_indicators} indicators)")
                    
                    print(f"Unverified link score: {link} - {score}")
                    
                    if score > best_score:
                        best_score = score
                        best_link = link
                        best_method = method
                except Exception as e:
                    print(f"Error checking unverified link: {e}")
                    
            if best_link:
                print(f"✅ Using best unverified footer link: {best_link} (Score: {best_score})")
                return PrivacyResponse(
                    url=url,
                    pp_url=best_link,
                    success=True,
                    message=f"Found potential Privacy Policy link using {best_method} (high title match)",
                    method_used=best_method
                )
            
            # If scoring failed, just use the first JS link as default
            if 'js_unverified' in locals() and js_unverified:
                return PrivacyResponse(
                    url=url,
                    pp_url=js_unverified,
                    success=True,
                    message="Found potential Privacy Policy link using JavaScript (unverified)",
                    method_used="javascript_unverified"
                )
            
            if 'scroll_unverified' in locals() and scroll_unverified:
                return PrivacyResponse(
                    url=url,
                    pp_url=scroll_unverified,
                    success=True,
                    message="Found potential Privacy Policy link using scroll method (unverified)",
                    method_used="scroll_unverified"
                )

        # If all methods fail, return failure
        return handle_navigation_failure(url, None)

    except Exception as e:
        print(f"Error during browser automation: {e}")
        return handle_error(url, None, str(e))
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
            slow_mo=10,  # Significantly reduced delay for better performance
        )

        # Create context with optimized settings
        context = await browser.new_context(
            # Consistent with browser window
            viewport={"width": 1920, "height": 1080},
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
        async def random_delay(min_ms=100, max_ms=300):
            delay = random.randint(min_ms, max_ms)
            await page.wait_for_timeout(delay)

        # Set reasonable timeouts
        page.set_default_timeout(10000)  # Significantly reduced timeout

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
                delay = random.randint(200, 500)  # Significantly reduced delay
                print(f"Waiting {delay/1000}s before retry {attempt+1}...")
                await page.wait_for_timeout(delay)

            print(f"Navigation attempt {attempt+1}/{max_retries} to {url}")

            # Optimized navigation strategy with shorter timeout
            response = await page.goto(url, timeout=5000, wait_until="domcontentloaded")

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


async def find_privacy_links_js(page, context, unverified_result=None):
    """Optimized JavaScript-based privacy policy link finder with anti-bot protection handling."""
    print("\n=== Starting find_privacy_links_js ===")
    print("Searching for privacy policy links using JavaScript...")

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
            # On anti-bot pages, specifically target footer links which are
            # often still accessible
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
                               text.includes('personal information') ||
                               href.includes('privacy') ||
                               href.includes('data-policy');
                    })
                    .map(a => {
                        const text = a.textContent.trim().toLowerCase();
                        const href = a.href.toLowerCase();

                        // Score the link - Enhanced scoring for important titles - MASSIVELY BOOSTED for user/customer terms
                        let score = 0;

                        // Check for user/customer patterns FIRST - absolute highest priority
                        if (text === 'user privacy notice' || text === 'user privacy policy') score += 500; // MASSIVE boost
                        else if (text.includes('user privacy notice') || text.includes('user privacy policy')) score += 450; // MASSIVE boost
                        else if (text === 'customer privacy notice' || text === 'customer privacy policy') score += 400; // MASSIVE boost
                        else if (text.includes('customer privacy notice') || text.includes('customer privacy policy')) score += 350; // MASSIVE boost
                        else if (text.includes('user privacy') || text.includes('user data')) score += 300; // MASSIVE boost
                        else if (text.includes('customer privacy') || text.includes('customer data')) score += 250; // MASSIVE boost
                        else if (text.includes('user') && text.includes('privacy')) score += 200; // MASSIVE boost
                        else if (text.includes('customer') && text.includes('privacy')) score += 180; // MASSIVE boost

                        // URL-based user/customer scoring - extreme priority
                        if (href.includes('user-privacy') || href.includes('user_privacy')) score += 300; // MASSIVE boost
                        else if (href.includes('customer-privacy') || href.includes('customer_privacy')) score += 250; // MASSIVE boost
                        else if (href.includes('user') && href.includes('privacy')) score += 200; // MASSIVE boost
                        else if (href.includes('customer') && href.includes('privacy')) score += 180; // MASSIVE boost

                        // Only check standard patterns if no user/customer match was found
                        if (score === 0) {
                            // Exact title matches get highest priority (unchanged from before)
                            if (text === 'privacy notice' || text === 'privacy policy') score += 200;
                            else if (text.includes('privacy notice') || text.includes('privacy policy')) score += 180;
                            else if (text.includes('privacy statement')) score += 160;
                            else if (text.includes('data privacy')) score += 150;
                            else if (text.includes('privacy')) score += 140;
                            else if (text.includes('data policy')) score += 130;
                            else if (text.includes('data protection')) score += 120;

                            // URL scoring (unchanged from before)
                            if (href.includes('privacy-policy') || href.includes('privacy_policy')) score += 50;
                            else if (href.includes('privacy-notice') || href.includes('privacy_notice')) score += 48;
                            else if (href.includes('privacy')) score += 45;
                            else if (href.includes('data-policy')) score += 40;
                            else if (href.includes('data-protection')) score += 30;
                        }

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
                    f"Found {len(footer_links)} potential privacy links in footer despite anti-bot protection"
                )
                for i, link in enumerate(footer_links[:3]):
                    print(
                        f"Footer link #{i + 1}: {link['text']} - {link['href']} (Score: {link['score']})"
                    )

                best_link = footer_links[0]["href"]

                # Always choose the best footer link when available
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

        # Enhanced link detection script - specifically targeting common
        # Privacy Policy links at bottom of page
        links = await page.evaluate(
            """(baseDomain) => {
            // Get all footer sections (most Privacy links are in footers)
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
                'data policy', 'privacy', 'data protection', 'personal data',
                'data processing', 'gdpr', 'ccpa'
            ];
            
            // User/customer patterns (high priority)
            const userCustomerPatterns = [
                'user privacy', 'customer privacy', 'user data', 'customer data',
                'user rights', 'customer rights', 'user information', 'customer information'
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
                    return privacyPatterns.some(term => text.includes(term) || href.includes(term)) ||
                           userCustomerPatterns.some(term => text.includes(term) || href.includes(term));
                })
                .map(link => {
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href.toLowerCase();

                    // Score the link
                    let score = 0;

                    // Text matching - ENHANCED with user/customer priority
                    if (text === 'user privacy notice' || text === 'user privacy policy') score += 250;  // Absolute highest priority
                    else if (text.includes('user privacy notice')) score += 240;  // Nearly as high
                    else if (text.includes('user privacy policy')) score += 230;  // Almost as high
                    else if (text.includes('user privacy')) score += 220;  // User privacy is top priority
                    else if (text === 'customer privacy notice' || text === 'customer privacy policy') score += 210; // Almost as high
                    else if (text.includes('customer privacy notice')) score += 200;  // Very high priority
                    else if (text.includes('customer privacy')) score += 190;  // Customer privacy is high priority
                    else if (text.includes('user') && text.includes('privacy policy')) score += 180;  // Very high priority
                    else if (text.includes('customer') && text.includes('privacy policy')) score += 170;  // Very high priority
                    else if (text === 'privacy policy' || text === 'privacy notice') score += 120;  // Increased from 100
                    else if (text.includes('privacy policy') || text.includes('privacy notice')) score += 110;  // Increased from 90
                    else if (text.includes('privacy') && text.includes('statement')) score += 105;  // Increased from 85
                    else if (text.includes('data policy') || text.includes('privacy policy')) score += 100;  // Increased from 80
                    else if (text.includes('privacy')) score += 90;  // Increased from 70
                    else if (text === 'data protection' || text === 'data privacy') score += 80;  // Increased from 60
                    else if (text.includes('data protection') || text.includes('data privacy')) score += 70;  // Increased from 50
                    
                    // Add user/customer bonuses separately if not already caught above
                    if (!score >= 170 && text.includes('user')) score += 80;  // Increased from 60
                    if (!score >= 170 && text.includes('customer')) score += 70;  // Increased from 50

                    // URL matching - ENHANCED with user/customer priority
                    if (href.includes('user-privacy-notice') || href.includes('user_privacy_notice')) score += 120;  // New highest URL priority
                    else if (href.includes('user-privacy-policy') || href.includes('user_privacy_policy')) score += 110;  // New very high priority
                    else if (href.includes('user-privacy') || href.includes('user_privacy')) score += 100;  // Increased from 80
                    else if (href.includes('customer-privacy-notice') || href.includes('customer_privacy_notice')) score += 95;  // New high priority
                    else if (href.includes('customer-privacy') || href.includes('customer_privacy')) score += 90;  // Increased from 75
                    else if (href.includes('user') && href.includes('privacy')) score += 85;  // Increased from 70
                    else if (href.includes('customer') && href.includes('privacy')) score += 80;  // Increased from 65
                    else if (href.includes('privacy-policy') ||
                        href.includes('privacy-notice') ||
                        href.includes('privacy_policy') ||
                        href.includes('privacy_notice')) score += 70;  // Increased from 50
                    else if (href.includes('privacy-statement') ||
                             href.includes('privacy_statement')) score += 65;  // Increased from 45
                    else if (href.includes('data-privacy') ||
                             href.includes('data_privacy')) score += 65;  // Increased from 45
                    else if (href.includes('/privacy/') ||
                             href.includes('/datapolicy/')) score += 60;  // Increased from 40
                    else if (href.includes('/privacy') ||
                             href.includes('/datapolicy')) score += 55;  // Increased from 35
                    else if (href.includes('gdpr') ||
                             href.includes('ccpa')) score += 50;  // Increased from 30
                    
                    // Add user/customer URL bonuses separately if not already caught
                    if (!score >= 140 && href.includes('user')) score += 50;  // Increased from 30
                    if (!score >= 140 && href.includes('customer')) score += 45;  // Increased from 25

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
            print("No relevant privacy links found using JavaScript method")
            return None, page, unverified_result

        # Display top results with scores for JS method
        print(f"Found {len(links)} relevant privacy links (JS):")
        for i, link in enumerate(links[:5]):
            print(
                f"JS Link #{i + 1}: {link['text']} - {link['href']} (Score: {link['score']})")

        # If links were found, use the highest scored one
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

                # Verify this is actually a privacy page
                is_privacy_page = await page.evaluate(
                    """() => {
                    const text = document.body.innerText.toLowerCase();
                    
                    // HIGHEST PRIORITY - First check for user/customer terms 
                    // AUTOMATICALLY classify as privacy page if user/customer terms exist
                    if (text.includes('user privacy') || 
                        text.includes('customer privacy') ||
                        text.includes('user data privacy') || 
                        text.includes('customer data privacy') ||
                        text.includes('user privacy policy') || 
                        text.includes('customer privacy policy') ||
                        text.includes('user privacy notice') || 
                        text.includes('customer privacy notice')) {
                        console.log('SUPREME PRIORITY USER/CUSTOMER PRIVACY TERMS FOUND!');
                        return true;
                    }
                    
                    // Check if both 'user'/'customer' AND 'privacy' exist anywhere
                    if ((text.includes('user') || text.includes('customer')) && 
                         text.includes('privacy') && 
                         (text.includes('notice') || text.includes('policy'))) {
                        console.log('VERY HIGH PRIORITY - USER/CUSTOMER + PRIVACY + NOTICE/POLICY found!');
                        return true;
                    }
                    
                    // Only then check standard privacy terms
                    const strongPrivacyMatchers = [
                        'privacy policy',
                        'privacy notice',
                        'data policy',
                        'privacy statement',
                        'personal information',
                        'data protection',
                        'how we collect',
                        'information we collect',
                        'data we collect',
                        'data subject rights',
                        'right to access',
                        'gdpr compliance'
                    ];
                    
                    const hasPrivacyTerms = strongPrivacyMatchers.some(term => text.includes(term));
                    return hasPrivacyTerms;
                }"""
                )

                if is_privacy_page:
                    print(f"✅ Verified as privacy policy page: {page.url}")
                    return page.url, page, unverified_result
                else:
                    print(
                        "⚠️ Link does not appear to be a privacy page after inspection")
                    return None, page, best_link
            except Exception as e:
                print(f"Error navigating to best link: {e}")
                return None, page, best_link

        return None, page, unverified_result

    except Exception as e:
        print(f"Error in JavaScript privacy link finder: {e}")
        return None, page, unverified_result


async def smooth_scroll_and_click_privacy(
    page, context, unverified_result=None, step=200, delay=50
):
    """Optimized version of smooth scroll with faster execution time for privacy policy links."""
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
            await current_page.wait_for_timeout(150) # Reduced timeout

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
                               text.includes('data policy') ||
                               href.includes('privacy') ||
                               href.includes('datapolicy');
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
                            element = await current_page.query_selector(f"a[href='{link['href']}']")
                            if element:
                                try:
                                    success = await click_and_wait_for_navigation(
                                        current_page, element, timeout=5000)
                                    if success:
                                        return (
                                            current_page.url,
                                            current_page,
                                            unverified_result,
                                        )
                                except Exception as e:
                                    print(f"Error during navigation: {e}")
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")

        # Skip scrolling back up to save time
        print("✅ Reached the bottom of the page.")

    except Exception as e:
        print(f"Error in footer/scroll check: {e}")

    return None, current_page, unverified_result


async def find_matching_privacy_link(page, context, unverified_result=None):
    """Find and click on privacy-related links with optimized performance."""
    try:
        # Use a more targeted selector with significantly enhanced user/customer terms priority
        links = await page.query_selector_all(
            'footer a, .footer a, #footer a, a[href*="user-privacy"], a[href*="user_privacy"], ' +
            'a[href*="user"][href*="privacy"], a[href*="customer-privacy"], a[href*="customer_privacy"], ' +
            'a[href*="customer"][href*="privacy"], a[href*="privacy"], a[href*="user"], a[href*="customer"], ' +
            'a[href*="datapolicy"], a[href*="data-policy"]'
        )

        # First pass: Look for "User Privacy" or "Privacy Notice" links specifically
        # With SIGNIFICANTLY ENHANCED priority for user/customer terms
        for link in links:
            try:
                text = await link.text_content()
                if not text:
                    continue
                text = text.lower().strip()
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Score the link text for user/customer priority - ENHANCED SCORING
                user_customer_score = 0
                
                # === DIRECT TEXT MATCHES - HIGHEST PRIORITY ===
                # Direct match for "User Privacy Notice" gets ABSOLUTE HIGHEST priority
                if text == "user privacy notice":
                    user_customer_score = 1000  # Absolute highest priority
                    print(f"⭐⭐⭐ Found EXACT 'user privacy notice' link text: {text} - ABSOLUTE HIGHEST PRIORITY")
                elif text == "user privacy policy":
                    user_customer_score = 950  # Almost absolute highest
                    print(f"⭐⭐⭐ Found EXACT 'user privacy policy' link text: {text} - NEAR HIGHEST PRIORITY")
                elif text == "customer privacy notice":
                    user_customer_score = 900  # Extremely high priority
                    print(f"⭐⭐ Found EXACT 'customer privacy notice' link text: {text} - EXTREMELY HIGH PRIORITY")
                elif text == "customer privacy policy":
                    user_customer_score = 850  # Very high priority
                    print(f"⭐⭐ Found EXACT 'customer privacy policy' link text: {text} - VERY HIGH PRIORITY")
                # === PARTIAL TEXT MATCHES - HIGH PRIORITY ===
                elif "user privacy notice" in text:
                    user_customer_score = 800  # Top tier priority
                    print(f"⭐⭐ Found 'user privacy notice' in link text: {text} - TOP TIER PRIORITY")
                elif "user privacy policy" in text:
                    user_customer_score = 750  # High priority
                    print(f"⭐⭐ Found 'user privacy policy' in link text: {text} - HIGH PRIORITY")
                elif "customer privacy notice" in text:
                    user_customer_score = 700  # High priority
                    print(f"⭐⭐ Found 'customer privacy notice' in link text: {text} - HIGH PRIORITY")
                elif "customer privacy policy" in text:
                    user_customer_score = 650  # High priority
                    print(f"⭐ Found 'customer privacy policy' in link text: {text} - HIGH PRIORITY")
                elif "user privacy" in text:
                    user_customer_score = 600  # Good priority
                    print(f"⭐ Found 'user privacy' in link text: {text} - GOOD PRIORITY")
                elif "customer privacy" in text:
                    user_customer_score = 550  # Good priority
                    print(f"⭐ Found 'customer privacy' in link text: {text} - GOOD PRIORITY")
                    
                # === URL MATCHING - ADDITIONAL PRIORITY ===
                # Also check URL for user/customer terms for additional score
                href_lower = href.lower()
                if "user-privacy" in href_lower or "user_privacy" in href_lower:
                    user_customer_score += 200  # Big boost for user privacy in URL
                    print(f"  + URL contains user-privacy: +200 points")
                elif "customer-privacy" in href_lower or "customer_privacy" in href_lower:
                    user_customer_score += 180  # Good boost for customer privacy in URL
                    print(f"  + URL contains customer-privacy: +180 points")
                elif "user" in href_lower and "privacy" in href_lower:
                    user_customer_score += 150  # Decent boost for user + privacy in URL
                    print(f"  + URL contains user and privacy: +150 points")
                elif "customer" in href_lower and "privacy" in href_lower:
                    user_customer_score += 130  # Smaller boost for customer + privacy in URL
                    print(f"  + URL contains customer and privacy: +130 points")
                    
                # If this is a high-scoring user/customer link, try to navigate immediately
                if user_customer_score >= 500:  # High enough to prioritize
                    print(f"⭐ Prioritizing high-scoring user/customer link: {text} (Score: {user_customer_score})")
                    try:
                        success = await click_and_wait_for_navigation(page, link, timeout=3000)  # Reduced timeout for faster processing
                        if success:
                            return page.url, page, unverified_result or href  # Use href as unverified_result if none exists
                    except Exception as e:
                        print(f"Navigation error clicking high-priority user/customer link: {e}")
                        # Still store this as unverified_result even if navigation fails
                        if not unverified_result:
                            unverified_result = href
                
                # STANDARD PRIVACY LINK MATCHING
                # Direct match for "Privacy Notice" gets top standard priority
                elif text == "privacy notice" or text == "privacy policy":
                    print(f"Found exact privacy notice/policy link: {text}")
                    try:
                        success = await click_and_wait_for_navigation(page, link, timeout=2500) # Reduced timeout
                        if success:
                            return page.url, page, unverified_result
                    except Exception as e:
                        print(f"Navigation error clicking link: {e}")
                        continue
            except Exception as e:
                print(f"Error processing link: {e}")
                continue

        # Second pass: Special handling for user privacy notice (high priority)
        for link in links:
            try:
                text = await link.text_content()
                if not text:
                    continue
                text = text.lower().strip()
                href = await link.get_attribute("href")
                if not href:
                    continue

                # Special case for eBay and similar sites
                if ("user privacy" in text or "user" in text and "privacy" in text) or \
                   (href and ("user-privacy" in href.lower() or "user_privacy" in href.lower() or "user" in href.lower() and "privacy" in href.lower())):
                    print(f"⭐ Found high-priority user privacy link: {text}")
                    success = await click_and_wait_for_navigation(page, link, timeout=3000)
                    if success:
                        return page.url, page, unverified_result
            except Exception as e:
                continue

        # Third pass: Check for other privacy links
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
                elif "data policy" in text:
                    score = 70
                elif "data protection" in text:
                    score = 50

                # Additional URL scoring
                if "/privacy-policy" in href or "/privacy_policy" in href:
                    score += 50
                elif "/privacy" in href or "/datapolicy" in href:
                    score += 40
                elif "/data-protection" in href or "/data_protection" in href:
                    score += 30

                if score > 50:  # High confidence match
                    print(f"Found high confidence privacy link: {text} ({score})")
                    success = await click_and_wait_for_navigation(
                        page, link, timeout=2500
                    )
                    if success:
                        return page.url, page, unverified_result
            except Exception as e:
                continue
        return None, page, unverified_result
    except Exception as e:
        print(f"Error in find_matching_privacy_link: {e}")
        return None, page, unverified_result


async def verify_is_privacy_page(page):
    """Verify if the current page is a privacy policy page."""
    try:
        page_title = await page.title()
        page_url = page.url

        # Initial checks on title and URL
        title_lower = page_title.lower()
        url_lower = page_url.lower()

        print("🔍 Performing thorough privacy page verification...")
        
        # Check for user/customer privacy terms first - HIGHEST PRIORITY
        user_customer_terms = await page.evaluate(
            """() => {
                const text = document.body.innerText.toLowerCase();
                const url = window.location.href.toLowerCase();
                const title = document.title.toLowerCase();
                
                // Create arrays to hold found terms
                const foundUserTerms = [];
                const foundCustomerTerms = [];
                
                // User terms in title/URL (supreme priority)
                if (title.includes('user privacy notice') || title.includes('user privacy policy'))
                    foundUserTerms.push('user privacy notice/policy in title');
                else if (title.includes('user privacy'))
                    foundUserTerms.push('user privacy in title');
                else if (title.includes('user') && title.includes('privacy'))
                    foundUserTerms.push('user + privacy in title');
                
                if (url.includes('user-privacy') || url.includes('user_privacy'))
                    foundUserTerms.push('user-privacy in URL');
                else if (url.includes('user') && url.includes('privacy'))
                    foundUserTerms.push('user + privacy in URL');
                
                // Customer terms in title/URL (high priority)
                if (title.includes('customer privacy notice') || title.includes('customer privacy policy'))
                    foundCustomerTerms.push('customer privacy notice/policy in title');
                else if (title.includes('customer privacy'))
                    foundCustomerTerms.push('customer privacy in title');
                else if (title.includes('customer') && title.includes('privacy'))
                    foundCustomerTerms.push('customer + privacy in title');
                
                if (url.includes('customer-privacy') || url.includes('customer_privacy'))
                    foundCustomerTerms.push('customer-privacy in URL');
                else if (url.includes('customer') && url.includes('privacy'))
                    foundCustomerTerms.push('customer + privacy in URL');
                
                // Check content for key phrases (good priority)
                const userPhrases = [
                    'user privacy rights',
                    'user data collection',
                    'user data processing',
                    'user consent',
                    'user preferences',
                    'user information'
                ];
                
                const customerPhrases = [
                    'customer privacy rights',
                    'customer data collection',
                    'customer data processing',
                    'customer consent',
                    'customer preferences',
                    'customer information'
                ];
                
                // Check for user phrases
                for (const phrase of userPhrases) {
                    if (text.includes(phrase)) {
                        foundUserTerms.push(phrase);
                    }
                }
                
                // Check for customer phrases
                for (const phrase of customerPhrases) {
                    if (text.includes(phrase)) {
                        foundCustomerTerms.push(phrase);
                    }
                }
                
                return {
                    userTerms: foundUserTerms,
                    customerTerms: foundCustomerTerms,
                    hasUserTerms: foundUserTerms.length > 0,
                    hasCustomerTerms: foundCustomerTerms.length > 0
                };
            }"""
        )
        
        # If we found user or customer terms, this is strong evidence
        if user_customer_terms["hasUserTerms"] or user_customer_terms["hasCustomerTerms"]:
            user_terms = user_customer_terms["userTerms"]
            customer_terms = user_customer_terms["customerTerms"]
            
            # Log findings
            if user_customer_terms["hasUserTerms"]:
                print(f"✅ USER PRIVACY TERMS FOUND: {', '.join(user_terms)}")
            if user_customer_terms["hasCustomerTerms"]:
                print(f"✅ CUSTOMER PRIVACY TERMS FOUND: {', '.join(customer_terms)}")
            
            # Calculate a confidence score - user terms weighted higher
            user_confidence = len(user_terms) * 25  # Each user term adds 25 confidence points
            customer_confidence = len(customer_terms) * 20  # Each customer term adds 20 confidence points
            
            total_confidence = min(100, user_confidence + customer_confidence)  # Cap at 100
            
            return {
                "isPrivacyPage": True,
                "confidence": total_confidence,
                "reason": "Contains user/customer privacy terms (highest priority)"
            }

        # Check if title contains strong privacy indicators
        title_confidence = 0
        is_privacy_title = False
        
        # Score the title - prioritize "Privacy Notice"
        if 'privacy notice' in title_lower:
            title_confidence = 90
            is_privacy_title = True
        elif 'privacy policy' in title_lower:
            title_confidence = 85
            is_privacy_title = True
        elif 'privacy statement' in title_lower:
            title_confidence = 80
            is_privacy_title = True
        elif 'privacy' in title_lower and any(word in title_lower for word in ['information', 'data', 'personal']):
            title_confidence = 75
            is_privacy_title = True
        elif 'privacy' in title_lower:
            title_confidence = 70
            is_privacy_title = True
        elif 'data protection' in title_lower:
            title_confidence = 65
            is_privacy_title = True
        elif 'data policy' in title_lower:
            title_confidence = 60
            is_privacy_title = True
        
        # If title is very strong indicator, this is enough for a quick confirmation
        if title_confidence >= 80:
            return {
                "isPrivacyPage": True,
                "confidence": title_confidence,
                "reason": f"Strong privacy indicator in title: '{page_title}'"
            }
        
        # URL checks
        url_confidence = 0
        if 'privacy-notice' in url_lower or 'privacy_notice' in url_lower:
            url_confidence = 60
        elif 'privacy-policy' in url_lower or 'privacy_policy' in url_lower:
            url_confidence = 55
        elif 'privacy-statement' in url_lower or 'privacy_statement' in url_lower:
            url_confidence = 50
        elif 'privacy' in url_lower:
            url_confidence = 45
        elif 'data-protection' in url_lower or 'data_protection' in url_lower:
            url_confidence = 40
        elif 'data-policy' in url_lower or 'data_policy' in url_lower:
            url_confidence = 35
        
        # Combined checks for early exit
        if is_privacy_title and url_confidence >= 45:
            combined_confidence = min(95, title_confidence + 10)  # Cap at 95
            return {
                "isPrivacyPage": True,
                "confidence": combined_confidence,
                "reason": f"Privacy in both title and URL: '{page_title}'"
            }
        
        # Content checks
        content_result = await page.evaluate(
            """() => {
                const text = document.body.innerText.toLowerCase();
                
                // Count privacy-related phrases
                const phrases = [
                    'information we collect', 'data we collect', 'personal information',
                    'your rights', 'privacy rights', 'gdpr', 'ccpa', 'opt-out',
                    'cookies', 'tracking technologies', 'third parties',
                    'how we use', 'share your information', 'contact us',
                    'data retention', 'policy updates', 'changes to this policy',
                    'delete your data', 'access your data'
                ];
                
                let phraseCount = 0;
                for (const phrase of phrases) {
                    if (text.includes(phrase)) {
                        phraseCount++;
                    }
                }
                
                // Check for sections that would appear in a privacy policy
                const hasSections = 
                    /collection.*information/i.test(text) ||
                    /use.*information/i.test(text) ||
                    /sharing.*information/i.test(text) ||
                    /cookies.*technologies/i.test(text) ||
                    /your.*rights/i.test(text) ||
                    /contact.*us/i.test(text);
                
                // Check length - privacy policies tend to be long
                // but not too long (to avoid false positives from long pages)
                const contentLength = text.length;
                const isPrivacyLength = contentLength >= 2000 && contentLength <= 100000;
                
                return {
                    phraseCount,
                    hasSections,
                    contentLength,
                    isPrivacyLength
                };
            }"""
        )
        
        # Build confidence from content
        content_confidence = 0
        
        # Phrase-based scoring
        if content_result["phraseCount"] >= 10:
            content_confidence += 40
        elif content_result["phraseCount"] >= 7:
            content_confidence += 30
        elif content_result["phraseCount"] >= 4:
            content_confidence += 20
        elif content_result["phraseCount"] >= 2:
            content_confidence += 10
        
        # Structure scoring
        if content_result["hasSections"]:
            content_confidence += 15
        
        # Length scoring
        if content_result["isPrivacyLength"]:
            content_confidence += 10
        
        # Final decision - combine all factors
        final_confidence = title_confidence + url_confidence * 0.5 + content_confidence * 0.7
        final_confidence = min(100, final_confidence)  # Cap at 100
        
        is_privacy_page = False
        reason = ""
        
        if final_confidence >= 70:
            is_privacy_page = True
            reason = "High confidence from multiple factors"
        elif title_confidence >= 70:
            is_privacy_page = True
            reason = "High confidence from title"
        elif title_confidence >= 50 and url_confidence >= 50:
            is_privacy_page = True
            reason = "Good confidence from title and URL"
        elif content_confidence >= 50 and (title_confidence > 0 or url_confidence > 0):
            is_privacy_page = True
            reason = "Good confidence from content with title/URL indicators"
        
        return {
            "isPrivacyPage": is_privacy_page,
            "confidence": final_confidence,
            "reason": reason
        }
        
    except Exception as e:
        print(f"Error verifying privacy page: {e}")
        return {
            "isPrivacyPage": False,
            "confidence": 0,
            "reason": f"Error during verification: {str(e)}"
        }


async def duckduckgo_search_fallback(domain, page):
    """Search for privacy policy using DuckDuckGo Search."""
    try:
        print("Attempting search engine fallback with DuckDuckGo...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data policy" OR "privacy"'
        
        # Navigate to DuckDuckGo
        await page.goto('https://duckduckgo.com/', timeout=5000)
        
        # Enter search query
        await page.fill('input[name="q"]', search_query)
        await page.press('input[name="q"]', 'Enter')
        
        # Wait for results to load
        await page.wait_for_selector('.result__body', timeout=5000)
        
        # Extract search results
        search_results = await page.evaluate(
            """() => {
                const results = Array.from(document.querySelectorAll('.result__body'))
                    .map(result => {
                        const titleEl = result.querySelector('.result__title a');
                        const urlEl = result.querySelector('.result__url');
                        const snippetEl = result.querySelector('.result__snippet');
                        
                        if (!titleEl || !urlEl) return null;
                        
                        const title = titleEl.textContent.trim();
                        const url = titleEl.href;
                        const snippet = snippetEl ? snippetEl.textContent.trim() : '';
                        
                        // Score the result
                        let score = 0;
                        
                        // Title scoring
                        const titleLower = title.toLowerCase();
                        if (titleLower === 'privacy policy' || titleLower === 'privacy notice')
                            score += 100;
                        else if (titleLower.includes('privacy policy') || titleLower.includes('privacy notice'))
                            score += 90;
                        else if (titleLower.includes('privacy') && titleLower.includes('statement'))
                            score += 85;
                        else if (titleLower.includes('privacy'))
                            score += 70;
                        else if (titleLower.includes('data protection') || titleLower.includes('data policy'))
                            score += 60;
                            
                        // User/customer bonus
                        if (titleLower.includes('user privacy') || titleLower.includes('customer privacy'))
                            score += 50;
                        else if ((titleLower.includes('user') || titleLower.includes('customer')) && 
                                 titleLower.includes('privacy'))
                            score += 40;
                            
                        // URL scoring
                        const urlLower = url.toLowerCase();
                        if (urlLower.includes('privacy-policy') || urlLower.includes('privacy_policy'))
                            score += 50;
                        else if (urlLower.includes('privacy-notice') || urlLower.includes('privacy_notice'))
                            score += 48;
                        else if (urlLower.includes('privacy'))
                            score += 40;
                            
                        // User/customer URL bonus
                        if (urlLower.includes('user-privacy') || urlLower.includes('user_privacy'))
                            score += 50;
                        else if (urlLower.includes('customer-privacy') || urlLower.includes('customer_privacy'))
                            score += 45;
                        else if ((urlLower.includes('user') || urlLower.includes('customer')) && 
                                urlLower.includes('privacy'))
                            score += 35;
                        
                        // Snippet scoring
                        const snippetLower = snippet.toLowerCase();
                        if (snippetLower.includes('privacy policy') || snippetLower.includes('privacy notice'))
                            score += 30;
                        
                        return { title, url, snippet, score };
                    })
                    .filter(result => result !== null)
                    .filter(result => {
                        // Additional filtering
                        const urlLower = result.url.toLowerCase();
                        return !urlLower.includes('facebook.com') && 
                               !urlLower.includes('twitter.com') && 
                               !urlLower.includes('youtube.com') &&
                               !urlLower.includes('linkedin.com');
                    })
                    .sort((a, b) => b.score - a.score);
                
                return results.slice(0, 10); // Return top 10 results
            }"""
        )
        
        if search_results and len(search_results) > 0:
            print(f"Found {len(search_results)} DuckDuckGo search results")
            
            # Try to verify the top results
            best_result_url = None
            best_result_score = 0
            
            for result_index in range(min(3, len(search_results))):
                # Only check the top 3 results
                if search_results[result_index]['score'] < 50:
                    # Skip low-scoring results
                    continue
                    
                best_result = search_results[result_index]["url"]
                
                try:
                    # Visit the page to verify it's a privacy page
                    print(f"Checking DuckDuckGo result: {best_result}")
                    await page.goto(best_result, timeout=5000, wait_until="domcontentloaded")
                    
                    # Get the page title
                    title = await page.title()
                    title_lower = title.lower()
                    
                    # Verify it's a privacy page
                    if ('privacy' in title_lower or 
                        'data policy' in title_lower or 
                        'data protection' in title_lower):
                        print(f"✅ Confirmed privacy page from DuckDuckGo search: {title}")
                        return best_result
                    
                    # If title doesn't confirm, check content
                    privacy_indicators = await page.evaluate(
                        """() => {
                            const text = document.body.innerText.toLowerCase();
                            return {
                                hasPrivacyHeader: /privacy|data\\s+policy/i.test(document.body.innerText),
                                hasPrivacyWords: text.includes('information we collect') || 
                                                text.includes('data we collect') || 
                                                text.includes('personal information')
                            };
                        }"""
                    )
                    
                    if privacy_indicators["hasPrivacyHeader"] and privacy_indicators["hasPrivacyWords"]:
                        print(f"✅ Confirmed privacy page from content analysis")
                        return best_result
                        
                    # Store this as a fallback
                    if search_results[result_index]['score'] > best_result_score:
                        best_result_url = best_result
                        best_result_score = search_results[result_index]['score']
                except Exception as e:
                    print(f"Error verifying DuckDuckGo result: {e}")
                    # Store as fallback if high score
                    if search_results[result_index]['score'] > best_result_score:
                        best_result_url = best_result
                        best_result_score = search_results[result_index]['score']
            
            # If we get here, no verified result was found
            if search_results[0]['score'] >= 60:
                print(f"Returning unverified high-confidence result: {search_results[0]['url']}")
                return search_results[0]['url']
            else:
                print("No high-confidence results found from DuckDuckGo")
                return None
        else:
            print("No relevant search results found from DuckDuckGo")
            return None
    except Exception as e:
        print(f"Error in DuckDuckGo search fallback: {e}")
        return None


async def yahoo_search_fallback(domain, page):
    """Search for privacy policy using Yahoo Search."""
    try:
        print("Attempting search engine fallback with Yahoo...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data policy" OR "privacy"'
        
        # Navigate to Yahoo
        await page.goto('https://search.yahoo.com/', timeout=5000)
        
        # Enter search query
        await page.fill('input[name="p"]', search_query)
        await page.press('input[name="p"]', 'Enter')
        
        # Wait for results to load
        await page.wait_for_selector('.algo', timeout=5000)
        
        # Extract search results
        search_results = await page.evaluate(
            """() => {
                const results = Array.from(document.querySelectorAll('.algo'))
                    .map(result => {
                        const titleEl = result.querySelector('h3 a');
                        const urlEl = result.querySelector('.compTitle span');
                        const snippetEl = result.querySelector('.compText p');
                        
                        if (!titleEl) return null;
                        
                        const title = titleEl.textContent.trim();
                        const url = titleEl.href;
                        const snippet = snippetEl ? snippetEl.textContent.trim() : '';
                        
                        // Score the result
                        let score = 0;
                        
                        // Title scoring
                        const titleLower = title.toLowerCase();
                        if (titleLower === 'privacy policy' || titleLower === 'privacy notice')
                            score += 100;
                        else if (titleLower.includes('privacy policy') || titleLower.includes('privacy notice'))
                            score += 90;
                        else if (titleLower.includes('privacy') && titleLower.includes('statement'))
                            score += 85;
                        else if (titleLower.includes('privacy'))
                            score += 70;
                        else if (titleLower.includes('data protection') || titleLower.includes('data policy'))
                            score += 60;
                            
                        // User/customer bonus
                        if (titleLower.includes('user privacy') || titleLower.includes('customer privacy'))
                            score += 50;
                        else if ((titleLower.includes('user') || titleLower.includes('customer')) && 
                                 titleLower.includes('privacy'))
                            score += 40;
                            
                        // URL scoring
                        const urlLower = url.toLowerCase();
                        if (urlLower.includes('privacy-policy') || urlLower.includes('privacy_policy'))
                            score += 50;
                        else if (urlLower.includes('privacy-notice') || urlLower.includes('privacy_notice'))
                            score += 48;
                        else if (urlLower.includes('privacy'))
                            score += 40;
                            
                        // User/customer URL bonus
                        if (urlLower.includes('user-privacy') || urlLower.includes('user_privacy'))
                            score += 50;
                        else if (urlLower.includes('customer-privacy') || urlLower.includes('customer_privacy'))
                            score += 45;
                        else if ((urlLower.includes('user') || urlLower.includes('customer')) && 
                                urlLower.includes('privacy'))
                            score += 35;
                        
                        // Snippet scoring
                        const snippetLower = snippet.toLowerCase();
                        if (snippetLower.includes('privacy policy') || snippetLower.includes('privacy notice'))
                            score += 30;
                        
                        return { title, url, snippet, score };
                    })
                    .filter(result => result !== null)
                    .filter(result => {
                        // Additional filtering
                        const urlLower = result.url.toLowerCase();
                        return !urlLower.includes('facebook.com') && 
                               !urlLower.includes('twitter.com') && 
                               !urlLower.includes('youtube.com') &&
                               !urlLower.includes('linkedin.com');
                    })
                    .sort((a, b) => b.score - a.score);
                
                return results.slice(0, 10); // Return top 10 results
            }"""
        )
        
        if search_results and len(search_results) > 0:
            print(f"Found {len(search_results)} Yahoo search results")
            
            # Try to verify the top results
            best_result_url = None
            best_result_score = 0
            
            for result_index in range(min(3, len(search_results))):
                # Only check the top 3 results
                if search_results[result_index]['score'] < 50:
                    # Skip low-scoring results
                    continue
                    
                best_result = search_results[result_index]["url"]
                
                try:
                    # Visit the page to verify it's a privacy page
                    print(f"Checking Yahoo result: {best_result}")
                    await page.goto(best_result, timeout=10000, wait_until="domcontentloaded")
                    
                    # Get the page title
                    title = await page.title()
                    title_lower = title.lower()
                    
                    # Verify it's a privacy page
                    if ('privacy' in title_lower or 
                        'data policy' in title_lower or 
                        'data protection' in title_lower):
                        print(f"✅ Confirmed privacy page from Yahoo search: {title}")
                        return best_result
                    
                    # If title doesn't confirm, check content
                    privacy_indicators = await page.evaluate(
                        """() => {
                            const text = document.body.innerText.toLowerCase();
                            return {
                                hasPrivacyHeader: /privacy|data\\s+policy/i.test(document.body.innerText),
                                hasPrivacyWords: text.includes('information we collect') || 
                                                text.includes('data we collect') || 
                                                text.includes('personal information')
                            };
                        }"""
                    )
                    
                    if privacy_indicators["hasPrivacyHeader"] and privacy_indicators["hasPrivacyWords"]:
                        print(f"✅ Confirmed privacy page from content analysis")
                        return best_result
                        
                    # Store this as a fallback
                    if search_results[result_index]['score'] > best_result_score:
                        best_result_url = best_result
                        best_result_score = search_results[result_index]['score']
                except Exception as e:
                    print(f"Error verifying Yahoo result: {e}")
                    # Store as fallback if high score
                    if search_results[result_index]['score'] > best_result_score:
                        best_result_url = best_result
                        best_result_score = search_results[result_index]['score']
            
            # If we get here, no verified result was found
            if search_results[0]['score'] >= 60:
                print(f"Returning unverified high-confidence result: {search_results[0]['url']}")
                return search_results[0]['url']
            else:
                print("No high-confidence results found from Yahoo")
                return None
        else:
            print("No relevant search results found from Yahoo")
            return None
    except Exception as e:
        print(f"Error in Yahoo search fallback: {e}")
        return None


async def bing_search_fallback(domain, page):
    """Search for privacy policy using Bing Search."""
    try:
        print("Attempting search engine fallback with Bing...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data policy" OR "privacy"'
        
        # Navigate to Bing
        await page.goto('https://www.bing.com/', timeout=10000)
        
        # Enter search query
        await page.fill('input[name="q"]', search_query)
        await page.press('input[name="q"]', 'Enter')
        
        # Wait for results to load
        await page.wait_for_selector('.b_algo', timeout=10000)
        
        # Extract search results
        search_results = await page.evaluate(
            """() => {
                const results = Array.from(document.querySelectorAll('.b_algo'))
                    .map(result => {
                        const titleEl = result.querySelector('h2 a');
                        const urlEl = result.querySelector('cite');
                        const snippetEl = result.querySelector('.b_caption p');
                        
                        if (!titleEl) return null;
                        
                        const title = titleEl.textContent.trim();
                        const url = titleEl.href;
                        const snippet = snippetEl ? snippetEl.textContent.trim() : '';
                        
                        // Score the result
                        let score = 0;
                        
                        // Title scoring
                        const titleLower = title.toLowerCase();
                        if (titleLower === 'privacy policy' || titleLower === 'privacy notice')
                            score += 100;
                        else if (titleLower.includes('privacy policy') || titleLower.includes('privacy notice'))
                            score += 90;
                        else if (titleLower.includes('privacy') && titleLower.includes('statement'))
                            score += 85;
                        else if (titleLower.includes('privacy'))
                            score += 70;
                        else if (titleLower.includes('data protection') || titleLower.includes('data policy'))
                            score += 60;
                            
                        // User/customer bonus
                        if (titleLower.includes('user privacy') || titleLower.includes('customer privacy'))
                            score += 50;
                        else if ((titleLower.includes('user') || titleLower.includes('customer')) && 
                                 titleLower.includes('privacy'))
                            score += 40;
                            
                        // URL scoring
                        const urlLower = url.toLowerCase();
                        if (urlLower.includes('privacy-policy') || urlLower.includes('privacy_policy'))
                            score += 50;
                        else if (urlLower.includes('privacy-notice') || urlLower.includes('privacy_notice'))
                            score += 48;
                        else if (urlLower.includes('privacy'))
                            score += 40;
                            
                        // User/customer URL bonus
                        if (urlLower.includes('user-privacy') || urlLower.includes('user_privacy'))
                            score += 50;
                        else if (urlLower.includes('customer-privacy') || urlLower.includes('customer_privacy'))
                            score += 45;
                        else if ((urlLower.includes('user') || urlLower.includes('customer')) && 
                                urlLower.includes('privacy'))
                            score += 35;
                        
                        // Snippet scoring
                        const snippetLower = snippet.toLowerCase();
                        if (snippetLower.includes('privacy policy') || snippetLower.includes('privacy notice'))
                            score += 30;
                        
                        return { title, url, snippet, score };
                    })
                    .filter(result => result !== null)
                    .filter(result => {
                        // Additional filtering
                        const urlLower = result.url.toLowerCase();
                        return !urlLower.includes('facebook.com') && 
                               !urlLower.includes('twitter.com') && 
                               !urlLower.includes('youtube.com') &&
                               !urlLower.includes('linkedin.com');
                    })
                    .sort((a, b) => b.score - a.score);
                
                return results.slice(0, 10); // Return top 10 results
            }"""
        )
        
        if search_results and len(search_results) > 0:
            print(f"Found {len(search_results)} Bing search results")
            
            # Try to verify the top results
            best_result_url = None
            best_result_score = 0
            
            for result_index in range(min(3, len(search_results))):
                # Only check the top 3 results
                if search_results[result_index]['score'] < 50:
                    # Skip low-scoring results
                    continue
                    
                best_result = search_results[result_index]["url"]
                
                try:
                    # Visit the page to verify it's a privacy page
                    print(f"Checking result: {best_result}")
                    await page.goto(best_result, timeout=10000, wait_until="domcontentloaded")
                    
                    # Get the page title
                    title = await page.title()
                    title_lower = title.lower()
                    
                    # Verify it's a privacy page
                    if ('privacy' in title_lower or 
                        'data policy' in title_lower or 
                        'data protection' in title_lower):
                        print(f"✅ Confirmed privacy page from Bing search: {title}")
                        return best_result
                    
                    # If title doesn't confirm, check content
                    privacy_indicators = await page.evaluate(
                        """() => {
                            const text = document.body.innerText.toLowerCase();
                            return {
                                hasPrivacyHeader: /privacy|data\\s+policy/i.test(document.body.innerText),
                                hasPrivacyWords: text.includes('information we collect') || 
                                                text.includes('data we collect') || 
                                                text.includes('personal information')
                            };
                        }"""
                    )
                    
                    if privacy_indicators["hasPrivacyHeader"] and privacy_indicators["hasPrivacyWords"]:
                        print(f"✅ Confirmed privacy page from content analysis")
                        return best_result
                        
                    # Store this as a fallback
                    if search_results[result_index]['score'] > best_result_score:
                        best_result_url = best_result
                        best_result_score = search_results[result_index]['score']
                except Exception as e:
                    print(f"Error verifying result: {e}")
                    # Store as fallback if high score
                    if search_results[result_index]['score'] > best_result_score:
                        best_result_url = best_result
                        best_result_score = search_results[result_index]['score']
        
        # Return the best result anyway if it has a decent score (60+)
        if best_result_score >= 60:
            print(
                f"⚠️ All verification methods failed, returning best unverified result: {best_result_url} (Score: {best_result_score})"
            )
            return best_result_url
        else:
            print("No relevant Bing search results found")
            return None
    except Exception as e:
        print(f"Error in Bing search fallback: {e}")
        return None


def handle_navigation_failure(url: str, unverified_result: str = None) -> PrivacyResponse:
    """Handle cases where all navigation attempts fail."""
    if unverified_result:
        # Return unverified result with warning
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,  # Still consider it success but with low confidence
            message="Found potential Privacy Policy URL (unverified)",
            method_used="unverified_result"
        )
    else:
        return PrivacyResponse(
            url=url,
            pp_url=None,
            success=False,
            message="Could not find Privacy Policy",
            method_used="navigation_failure"
        )


def handle_error(url: str, unverified_result: str, error: str) -> PrivacyResponse:
    """Handle errors during privacy policy search process."""
    if unverified_result:
        # Return unverified result with error message
        return PrivacyResponse(
            url=url,
            pp_url=unverified_result,
            success=True,  # Still consider it success but with error noted
            message=f"Found potential Privacy Policy URL but encountered an error: {error}",
            method_used="error_with_unverified"
        )
    else:
        return PrivacyResponse(
            url=url,
            pp_url=None,
            success=False,
            message=f"Error finding Privacy Policy: {error}",
            method_used="error"
        )


def prefer_main_domain(links, main_domain):
    """Reorder links to prefer those from the main domain."""
    def is_main_domain(url):
        try:
            parsed = urlparse(url)
            # Replace www. for consistency
            domain = parsed.netloc.replace("www.", "")
            # Check if domain equals or ends with main_domain
            return domain == main_domain or domain.endswith(f".{main_domain}")
        except Exception:
            return False

    main_domain_links = [link for link in links if is_main_domain(link)]
    other_links = [link for link in links if not is_main_domain(link)]
    
    # Return main domain links first, then others
    return main_domain_links + other_links