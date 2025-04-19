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
    "text_match": 0.4,
    "url_structure": 0.3,
    "context": 0.2,
    "position": 0.1,
}

# Special content indicators that deserve extra points
content_value_indicators = [
    "data processing purposes",
    "legal basis",
    "data retention period",
    "data protection officer",
    "data controller",
    "right to be forgotten",
    "right to erasure",
    "right to access",
    "data subject rights",
]

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
    high_score_footer_link = None  # Track high-scoring footer links

    try:
        playwright = await async_playwright().start()
        browser, context, page, _ = await setup_browser(playwright)
        success, _, _ = await navigate_with_retry(page, url, max_retries=2)
        if not success:
            print(
                "\nMain site navigation had issues, but trying to analyze current page...")

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
                    await page.goto(js_result, timeout=10000, wait_until="domcontentloaded")
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
                        await page.goto(scroll_result, timeout=10000, wait_until="domcontentloaded")
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

        # If we have a high-score footer link with privacy-related title,
        # prioritize it
        if high_score_footer_link:
            print(f"Prioritizing high-score footer link with privacy-related title: {high_score_footer_link}")
            return PrivacyResponse(
                url=url,
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
                            url=url,
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
                
                # Extra points for user/customer mentions in title - SIGNIFICANTLY INCREASED
                user_customer_bonus = 0
                if 'user privacy notice' in title_lower:
                    user_customer_bonus = 150  # Supreme priority for "user privacy notice"
                    print(f"✓ Supreme priority 'user privacy notice' in title: +150 points")
                elif 'user privacy policy' in title_lower:
                    user_customer_bonus = 140  # Almost as high
                    print(f"✓ Very top priority 'user privacy policy' in title: +140 points")
                elif 'user privacy' in title_lower:
                    user_customer_bonus = 130  # Top priority for "user privacy"
                    print(f"✓ Top priority 'user privacy' in title: +130 points")
                elif 'customer privacy notice' in title_lower:
                    user_customer_bonus = 120  # Very high for "customer privacy notice"
                    print(f"✓ Very high priority 'customer privacy notice' in title: +120 points")
                elif 'customer privacy' in title_lower:
                    user_customer_bonus = 110  # Very high for "customer privacy"
                    print(f"✓ High priority 'customer privacy' in title: +110 points")
                elif 'user' in title_lower and 'privacy' in title_lower:
                    user_customer_bonus = 100  # Very high priority for user + privacy
                    print(f"✓ High priority 'user' + 'privacy' in title: +100 points")
                elif 'customer' in title_lower and 'privacy' in title_lower:
                    user_customer_bonus = 90  # High priority for customer + privacy
                    print(f"✓ Priority 'customer' + 'privacy' in title: +90 points")
                elif 'user' in title_lower:
                    user_customer_bonus = 70  # Good bonus just for "user"
                    print(f"✓ 'user' mention in title: +70 points")
                elif 'customer' in title_lower:
                    user_customer_bonus = 60  # Good bonus just for "customer"
                    print(f"✓ 'customer' mention in title: +60 points")

                privacy_url_score = 0
                if 'privacy-policy' in url_lower or 'privacy_policy' in url_lower:
                    privacy_url_score = 50
                elif 'privacy-notice' in url_lower or 'privacy_notice' in url_lower:
                    privacy_url_score = 48
                elif 'privacy' in url_lower:
                    privacy_url_score = 40
                
                # Extra points for user/customer in URL - INCREASED
                url_user_customer_bonus = 0
                if 'user-privacy-notice' in url_lower or 'user_privacy_notice' in url_lower:
                    url_user_customer_bonus = 120  # Highest priority for user-privacy-notice in URL
                    print(f"✓ 'user-privacy-notice' in URL: +120 points")
                elif 'user-privacy-policy' in url_lower or 'user_privacy_policy' in url_lower:
                    url_user_customer_bonus = 110  # Very high for user-privacy-policy
                    print(f"✓ 'user-privacy-policy' in URL: +110 points")
                elif 'user-privacy' in url_lower or 'user_privacy' in url_lower:
                    url_user_customer_bonus = 100  # High priority for user-privacy in URL
                    print(f"✓ 'user-privacy' in URL: +100 points")
                elif 'customer-privacy-notice' in url_lower or 'customer_privacy_notice' in url_lower:
                    url_user_customer_bonus = 90  # High priority for customer-privacy-notice
                    print(f"✓ 'customer-privacy-notice' in URL: +90 points")
                elif 'customer-privacy' in url_lower or 'customer_privacy' in url_lower:
                    url_user_customer_bonus = 80  # High priority for customer-privacy
                    print(f"✓ 'customer-privacy' in URL: +80 points")
                elif 'user' in url_lower and 'privacy' in url_lower:
                    url_user_customer_bonus = 70  # Good bonus for user + privacy
                    print(f"✓ 'user' + 'privacy' in URL: +70 points")
                elif 'customer' in url_lower and 'privacy' in url_lower:
                    url_user_customer_bonus = 60  # Good bonus for customer + privacy
                    print(f"✓ 'customer' + 'privacy' in URL: +60 points")
                elif 'user' in url_lower:
                    url_user_customer_bonus = 50  # Bonus just for user
                    print(f"✓ 'user' mention in URL: +50 points")
                elif 'customer' in url_lower:
                    url_user_customer_bonus = 40  # Bonus just for customer
                    print(f"✓ 'customer' mention in URL: +40 points")
                
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
                        url=url,
                        pp_url=best_footer_link,
                        success=True,
                        message="Found Privacy Policy using high-scoring footer link",
                        method_used="high_score_footer"
                    )
            except Exception as e:
                print(f"Error analyzing footer link: {e}")

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
                        url=url,
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
        # Exact match terms with highest priority
        exact_high_priority_terms = [
            'user privacy notice', 'customer privacy notice', 
            'user privacy policy', 'customer privacy policy',
            'user-privacy-notice', 'customer-privacy-notice',
            'user-privacy-policy', 'customer-privacy-policy'
        ]
        
        # Scan through unique links with the most stringent matching
        for link, source in zip(unique_links, unique_sources):
            try:
                # First, check URL for exact match
                if any(term in link.lower() for term in exact_high_priority_terms):
                    await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                    title = await page.title().lower()
                    
                    # Double verification: URL and title match
                    if any(term in title for term in exact_high_priority_terms):
                        print(f"🚀🚀 ABSOLUTE HIGHEST PRIORITY: Found exact user/customer privacy link: {link}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=link,
                            success=True,
                            message="Found Exact User/Customer Privacy Policy (SUPREME PRIORITY)",
                            method_used="exact_user_customer_privacy"
                        )
                
                # Fallback: Check page title if URL doesn't match
                await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                title = await page.title().lower()
                
                if any(term in title for term in exact_high_priority_terms):
                    print(f"🚀 HIGHEST PRIORITY: Found user/customer privacy title: {link}")
                    return PrivacyResponse(
                        url=url,
                        pp_url=link,
                        success=True,
                        message="Found User/Customer Privacy Policy (Highest Priority)",
                        method_used="user_customer_privacy_title"
                    )
            
            except Exception as e:
                print(f"Error processing high-priority privacy link {link}: {e}")
                continue
        
        # Broader matching if exact terms not found
        broader_priority_terms = [
            'user privacy', 'customer privacy', 
            'user-privacy', 'customer-privacy'
        ]
        
        for link, source in zip(unique_links, unique_sources):
            try:
                if any(term in link.lower() for term in broader_priority_terms):
                    await page.goto(link, timeout=10000, wait_until="domcontentloaded")
                    title = await page.title().lower()
                    
                    if any(term in title for term in broader_priority_terms):
                        print(f"🔍 High Priority: Found broader user/customer privacy link: {link}")
                        return PrivacyResponse(
                            url=url,
                            pp_url=link,
                            success=True,
                            message="Found Broader User/Customer Privacy Policy",
                            method_used="broader_user_customer_privacy"
                        )
            
            except Exception as e:
                print(f"Error processing broader priority privacy link {link}: {e}")
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
        if js_unverified:
            unverified_links.append((js_unverified, "javascript_footer"))
        if scroll_unverified:
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
                    
                    # Check for key privacy policy content indicators
                    content_indicators = 0
                    for indicator in content_value_indicators:
                        if indicator in page_content:
                            content_indicators += 1
                    
                    # Add points based on content quality
                    if content_indicators >= 5:
                        score += 20  # High quality content with many indicators
                    elif content_indicators >= 3:
                        score += 10  # Medium quality content
                    
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
            if js_unverified:
                return PrivacyResponse(
                url=url,
                pp_url=js_unverified,
                success=True,
                message="Found potential Privacy Policy link using JavaScript (unverified)",
                method_used="javascript_unverified"
                )
            
            if scroll_unverified:
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
            slow_mo=50,  # Reduced delay for better performance
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

                        // Score the link - Enhanced scoring for important titles
                        let score = 0;

                        // Exact title matches get highest priority
                        if (text === 'privacy notice' || text === 'privacy policy') score += 200;
                        else if (text.includes('privacy notice') || text.includes('privacy policy')) score += 180;
                        else if (text.includes('privacy statement')) score += 160;
                        else if (text.includes('data privacy')) score += 150;
                        else if (text.includes('privacy')) score += 140;
                        else if (text.includes('data policy')) score += 130;
                        else if (text.includes('data protection')) score += 120;

                        // URL scoring
                        if (href.includes('privacy-policy') || href.includes('privacy_policy')) score += 50;
                        else if (href.includes('privacy-notice') || href.includes('privacy_notice')) score += 48;
                        else if (href.includes('privacy')) score += 45;
                        else if (href.includes('data-policy')) score += 40;
                        else if (href.includes('data-protection')) score += 30;

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
                    f"Found {
    len(footer_links)} potential privacy links in footer despite anti-bot protection"
                )
                for i, link in enumerate(footer_links[:3]):
                    print(
                        f"Footer link #{
    i +
    1}: {
        link['text']} - {
            link['href']} (Score: {
                link['score']})"
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
                    const strongPrivacyMatchers = [
                        'privacy policy',
                        'privacy notice',
                        'data policy',
                        'personal information',
                        'data protection',
                        'how we collect'
                    ];
                    return strongPrivacyMatchers.some(term => text.includes(term));
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


async def find_matching_privacy_link(page, context, unverified_result=None):
    """Find and click on privacy-related links with optimized performance."""
    try:
        # Use a more targeted selector for performance
        links = await page.query_selector_all(
            'footer a, .footer a, #footer a, a[href*="privacy"], a[href*="user"], a[href*="datapolicy"], a[href*="data-policy"]'
        )

        # First pass: Look for "User Privacy" or "Privacy Notice" links specifically
        for link in links:
            try:
                text = await link.text_content()
                if not text:
                    continue
                text = text.lower().strip()
                
                # Direct match for "User Privacy Notice" gets HIGHEST priority
                if text == "user privacy notice" or text == "user privacy policy":
                    href = await link.get_attribute("href")
                    if href:
                        print(f"⭐ Found exact user privacy notice/policy link: {text}")
                        try:
                            success = await click_and_wait_for_navigation(page, link, timeout=5000)
                            if success:
                                return page.url, page, unverified_result
                        except Exception as e:
                            print(f"Navigation error clicking link: {e}")
                    continue

                # Direct match for "Privacy Notice" gets top priority
                if text == "privacy notice" or text == "privacy policy":
                    href = await link.get_attribute("href")
                    if href:
                        print(f"Found exact privacy notice/policy link: {text}")
                        try:
                            success = await click_and_wait_for_navigation(page, link, timeout=5000)
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
                    success = await click_and_wait_for_navigation(page, link, timeout=5000)
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
        async with page.expect_navigation(timeout=timeout, wait_until="domcontentloaded"):
                await element.click()
        return True
    except Exception as e:
        print(f"Navigation error: {e}")
        return False


async def smooth_scroll_and_click_privacy(
    page, context, unverified_result=None, step=200, delay=100
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

async def verify_is_privacy_page(page):
    """Verify if the current page is a privacy policy page."""
    try:
        page_title = await page.title()
        page_url = page.url

        # Initial checks on title and URL
        title_lower = page_title.lower()
        url_lower = page_url.lower()

        print("🔍 Performing thorough privacy page verification...")

        # Define indicators
        title_indicators = [
            "privacy",
            "privacy policy",
            "privacy notice",
            "privacy statement",
            "data policy",
            "data protection",
            "personal data",
            "data privacy",
            "gdpr",
            "ccpa",
        ]

        strong_indicators = [
            "privacy policy",
            "privacy notice",
            "privacy statement",
            "data privacy policy",
            "data protection policy",
        ]

        url_indicators = [
            "/privacy",
            "/privacy-policy",
            "/privacy_policy",
            "/privacy-notice",
            "/privacy_notice",
            "/data-policy",
            "/data_policy",
            "/data-protection",
            "/data_protection",
            "/gdpr",
            "/legal/privacy",
            "privacy.html",
            "privacypolicy.html",
            "privacy-policy.html",
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

        # Check for privacy sections and phrases
        privacy_sections = 0
        privacy_phrases = 0

        section_patterns = [
            "information we collect",
            "personal information",
            "personal data",
            "data we collect",
            "data collection",
            "data processing",
            "data protection",
            "data usage",
            "use of data",
            "use of information",
            "how we use",
            "how we collect",
            "information usage",
            "cookies",
            "tracking technologies",
            "third parties",
            "third-party",
            "data sharing",
            "data transfers",
            "international transfers",
            "data retention",
            "your rights",
            "your choices",
            "opt out",
            "opt-out",
            "gdpr",
            "ccpa",
            "california privacy rights",
            "european users",
            "data controllers",
            "data processors",
            "contact us",
            "privacy team",
            "data protection officer",
            "changes to this policy",
            "updates to this policy",
        ]

        privacy_phrase_patterns = [
            "we collect",
            "we may collect",
            "we process",
            "we use",
            "we may use",
            "we share",
            "we may share",
            "personal information",
            "personal data",
            "your information",
            "your data",
            "your personal information",
            "your personal data",
            "cookie policy",
            "third-party",
            "third party",
            "opt out",
            "opt-out",
            "under the gdpr",
            "under the ccpa",
            "california residents",
            "eu residents",
            "european users",
            "data subject rights",
            "data retention",
            "data protection",
            "privacy rights",
            "tracking technologies",
        ]

        # Check for privacy headings
        privacy_headings = await page.evaluate(
            """() => {
            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6, strong, b'));
            const privacyHeadingPatterns = [
                "privacy", "data", "information", "cookies", "personal", "tracking", 
                "rights", "choices", "opt-out", "gdpr", "ccpa", "retention", "collection",
                "processing", "third-party", "third party", "security", "protection"
            ];
            
            return headings.some(h => {
                const text = h.innerText.toLowerCase();
                return privacyHeadingPatterns.some(pattern => text.includes(pattern));
            });
        }"""
        )

        # Count privacy sections and phrases
        for pattern in section_patterns:
            if pattern in content_lower:
                privacy_sections += 1

        for pattern in privacy_phrase_patterns:
            if pattern in content_lower:
                privacy_phrases += 1

        # Check for negative indicators that suggest it's not a privacy page
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
        ]

        has_negative_indicators = any(
            indicator in content_lower for indicator in negative_indicators
        )

        # Determine if this is a privacy page
        minimum_text_length = 1000  # Minimum content length for a privacy page
        minimum_privacy_sections = 3  # Minimum number of privacy sections required

        minimum_text_present = content_length >= minimum_text_length
        minimum_sections_present = privacy_sections >= minimum_privacy_sections

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
            20, privacy_sections * 3
        )  # Up to 20 points for privacy sections
        confidence_score += min(
            15, privacy_phrases * 2
        )  # Up to 15 points for privacy phrases

        if privacy_headings:
            confidence_score += 10

        # Length bonus
        if content_length > 5000:
            confidence_score += 5

        # Additional title-based scoring for common privacy page titles
        common_privacy_titles = [
            "privacy policy",
            "privacy notice",
            "privacy statement",
            "data policy",
            "data privacy policy",
        ]
        if any(title in title_lower for title in common_privacy_titles):
            confidence_score += 10
            
        # Special bonus for user/customer in title - HIGHEST PRIORITY
        if "user privacy notice" in title_lower:
            confidence_score += 40  # Increased from 25
            print(f"✓ User privacy notice in title: +40 points")
        elif "user privacy policy" in title_lower:
            confidence_score += 35
            print(f"✓ User privacy policy in title: +35 points")
        elif "user privacy" in title_lower:
            confidence_score += 30
            print(f"✓ User privacy in title: +30 points")
        elif "customer privacy notice" in title_lower:
            confidence_score += 28
            print(f"✓ Customer privacy notice in title: +28 points")
        elif "customer privacy" in title_lower:
            confidence_score += 25
            print(f"✓ Customer privacy in title: +25 points")
        elif "user" in title_lower and "privacy" in title_lower:
            confidence_score += 22
            print(f"✓ User + privacy in title: +22 points")
        elif "customer" in title_lower and "privacy" in title_lower:
            confidence_score += 20
            print(f"✓ Customer + privacy in title: +20 points")
        elif "user" in title_lower or "customer" in title_lower:
            confidence_score += 18
            print(f"✓ User or customer mention in title: +18 points")
            
        # Special bonus for user/customer in URL
        if "user-privacy-notice" in url_lower or "user_privacy_notice" in url_lower:
            confidence_score += 35  # Increased from 20
            print(f"✓ User privacy notice in URL: +35 points")
        elif "user-privacy-policy" in url_lower or "user_privacy_policy" in url_lower:
            confidence_score += 30
            print(f"✓ User privacy policy in URL: +30 points")
        elif "user-privacy" in url_lower or "user_privacy" in url_lower:
            confidence_score += 25
            print(f"✓ User privacy in URL: +25 points")
        elif "customer-privacy-notice" in url_lower or "customer_privacy_notice" in url_lower:
            confidence_score += 22
            print(f"✓ Customer privacy notice in URL: +22 points")
        elif "customer-privacy" in url_lower or "customer_privacy" in url_lower:
            confidence_score += 20
            print(f"✓ Customer privacy in URL: +20 points")
        elif "user" in url_lower and "privacy" in url_lower:
            confidence_score += 18
            print(f"✓ User + privacy in URL: +18 points")
        elif "customer" in url_lower and "privacy" in url_lower:
            confidence_score += 16
            print(f"✓ Customer + privacy in URL: +16 points")
        elif "user" in url_lower or "customer" in url_lower:
            confidence_score += 12
            print(f"✓ User or customer mention in URL: +12 points")

        # User/customer in content
        user_customer_content_bonus = 0
        if "user privacy" in content_lower or "customer privacy" in content_lower:
            user_customer_content_bonus = 15
        elif "user data" in content_lower or "customer data" in content_lower:
            user_customer_content_bonus = 12
        elif ("user" in content_lower or "customer" in content_lower) and "privacy" in content_lower:
            user_customer_content_bonus = 10
        
        if user_customer_content_bonus > 0:
            confidence_score += min(15, user_customer_content_bonus)
            print(f"✓ User/customer content bonus: +{min(15, user_customer_content_bonus)} points")

        # Penalties
        if has_negative_indicators:
            # Reduce penalty impact if we have strong title indicators and privacy content
            if (
                any(title in title_lower for title in common_privacy_titles)
                and privacy_sections >= 5
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

        # Determine if this is a privacy page
        # A page is considered a privacy page if it has a high confidence score
        is_privacy_page = confidence_score >= 75

        # Domain preference - give a bonus if the URL is from the original domain
        domain_preference_bonus = 0
        try:
            # Extract the domain being visited from context
            page_domain = urlparse(page_url).netloc.lower().replace('www.', '')
            
            # Get the original domain from the referrer if available
            referrer = await page.evaluate("() => document.referrer")
            original_domain = ""
            if referrer:
                original_domain = urlparse(referrer).netloc.lower().replace('www.', '')
            
            # Check if page domain matches the original domain or is a subdomain
            if page_domain == original_domain:
                domain_preference_bonus = 15
                print(f"✓ Domain match bonus: +15 points (same domain)")
            elif original_domain and page_domain.endswith(f".{original_domain}"):
                domain_preference_bonus = 0  # No bonus for subdomains
            elif original_domain and original_domain.endswith(f".{page_domain}"):
                domain_preference_bonus = 8  # Small bonus for parent domain
                print(f"✓ Domain match bonus: +8 points (parent domain)")
            
            # Add the bonus to the confidence score
            confidence_score += domain_preference_bonus
            confidence_score = max(0, min(100, confidence_score))
        except Exception as e:
            print(f"Error calculating domain preference: {e}")

        # For pages with strong indicators in title but slightly lower scores,
        # be more lenient to avoid missing valid Privacy Policy pages
        if not is_privacy_page and confidence_score >= 65:
            # Check if the title contains very strong Privacy indicators
            very_strong_title_indicators = [
                "privacy policy",
                "privacy notice",
                "privacy statement",
                "data privacy policy",
            ]
            if any(
                indicator in title_lower for indicator in very_strong_title_indicators
            ):
                if privacy_sections >= 5 or privacy_phrases >= 3:
                    is_privacy_page = True
                    print(
                        f"✅ Special consideration: High confidence title with privacy content (Score: {confidence_score}/100)"
                    )

        # Print verification results
        print(f"📊 Page verification results:")
        print(f"  Title: {title_lower}...")
        print(f"  URL: {url_lower}")
        print(f"  Confidence Score: {confidence_score}/100")
        print(f"  Strong Indicators: {strong_indicator}")
        print(f"  Title Indicators: {title_indicator}")
        print(f"  URL Indicators: {url_indicator}")
        print(f"  Privacy Sections: {privacy_sections}")
        print(f"  Privacy Phrases: {privacy_phrases}")
        print(f"  Has Privacy Headings: {privacy_headings}")
        print(f"  Content Length: {content_length} chars")
        print(f"  Has Negative Indicators: {has_negative_indicators}")

        if is_privacy_page:
            print(
                f"✅ VERIFIED: This appears to be a Privacy Policy page (Score: {confidence_score}/100)"
            )
        else:
            print(
                f"❌ NOT VERIFIED: Does not appear to be a Privacy Policy page (Score: {confidence_score}/100)"
            )

        # Return detailed verification results
        return {
            "isPrivacyPage": is_privacy_page,
            "confidence": confidence_score,
            "title": page_title,
            "url": page_url,
            "strongIndicator": strong_indicator,
            "titleIndicator": title_indicator,
            "urlIndicator": url_indicator,
            "privacySectionCount": privacy_sections,
            "privacyPhraseCount": privacy_phrases,
            "hasPrivacyHeadings": privacy_headings,
            "contentLength": content_length,
            "minimumTextPresent": minimum_text_present,
            "minimumSectionsPresent": minimum_sections_present,
            "hasNegativeIndicators": has_negative_indicators,
        }

    except Exception as e:
        print(f"Error during page verification: {e}")
        return {"isPrivacyPage": False, "confidence": 0, "error": str(e)}

async def yahoo_search_fallback(domain, page):
    """Search for privacy policy using Yahoo Search."""
    try:
        print("Attempting search engine fallback with Yahoo...")

        # Create a search query for the domain with specific site constraint
        search_query = f'site:{domain} ("privacy policy" OR "privacy notice" OR "privacy statement" OR "data policy" OR "user privacy" OR "customer privacy")'
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
                    
                    // ===== TITLE SCORING BREAKDOWN =====
                    // Scoring for title matches (prioritize privacy policy)
                    if (titleLower.includes('privacy notice')) score += 120;            // Highest priority - "Privacy Notice"
                    else if (titleLower.includes('privacy policy')) score += 100;       // Standard privacy policy
                    else if (titleLower.includes('privacy statement')) score += 90;     // Privacy statement
                    else if (titleLower.includes('data policy')) score += 85;           // Data policy
                    else if (titleLower.includes('privacy')) score += 70;               // Generic privacy
                    else if (titleLower.includes('data protection')) score += 60;       // Data protection
                    else if (titleLower.includes('personal data')) score += 55;         // Personal data
                    
                    // Bonus for user/customer specific terms in title
                    if (titleLower.includes('user') && titleLower.includes('privacy')) score += 25;
                    if (titleLower.includes('customer') && titleLower.includes('privacy')) score += 25;
                    
                    // ===== DESCRIPTION SCORING BREAKDOWN =====
                    // Scoring for description matches
                    if (descLower.includes('privacy notice')) score += 40;
                    else if (descLower.includes('privacy policy')) score += 35;
                    else if (descLower.includes('personal information')) score += 30;
                    else if (descLower.includes('data we collect')) score += 25;
                    else if (descLower.includes('privacy')) score += 20;
                    
                    // Bonus for user/customer specific terms in description
                    if (descLower.includes('user data') || descLower.includes('customer data')) score += 15;
                    if (descLower.includes('user information') || descLower.includes('customer information')) score += 15;
                    if (descLower.includes('user rights') || descLower.includes('customer rights')) score += 15;
                    if ((descLower.includes('user') || descLower.includes('customer')) && 
                        descLower.includes('privacy')) score += 20;
                    
                    // ===== URL SCORING BREAKDOWN =====
                    // URL-based scoring
                    if (urlLower.includes('privacy-notice') || urlLower.includes('privacy_notice')) score += 60;
                    else if (urlLower.includes('privacy-policy') || urlLower.includes('privacy_policy')) score += 50;
                    else if (urlLower.includes('data-policy') || urlLower.includes('data_policy')) score += 45;
                    else if (urlLower.includes('privacy')) score += 40;
                    else if (urlLower.includes('data-protection')) score += 35;
                    else if (urlLower.includes('gdpr')) score += 30;
                    
                    // Bonus for user/customer in URL
                    if ((urlLower.includes('user') || urlLower.includes('customer')) && 
                        urlLower.includes('privacy')) score += 15;
                    
                    // Bonus for legal section
                    if (urlLower.includes('/legal/')) score += 15;
                    
                    // Print detailed scoring (for debugging)
                    console.log(`YAHOO RESULT SCORING: ${a.href} (${score} points)
                    - Title: "${title}" 
                    - Description: "${description.substring(0, 100)}..."
                    - URL: ${a.href}`);
                    
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
                    result.url.toLowerCase().includes('data-policy') || 
                    result.url.toLowerCase().includes('gdpr')
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
                # Print detailed breakdown if it's the top result
                if i == 0:
                    title_lower = result['title'].lower()
                    url_lower = result['url'].lower()
                    print(f"=== TOP RESULT SCORING BREAKDOWN ===")
                    
                    # Title points breakdown
                    print("TITLE POINTS:")
                    if 'privacy notice' in title_lower:
                        print(" ✓ 'privacy notice' in title: +120 points")
                    elif 'privacy policy' in title_lower:
                        print(" ✓ 'privacy policy' in title: +100 points")
                    elif 'privacy statement' in title_lower:
                        print(" ✓ 'privacy statement' in title: +90 points")
                    elif 'privacy' in title_lower:
                        print(" ✓ 'privacy' in title: +70 points")
                    
                    # User/Customer in title
                    if ('user' in title_lower and 'privacy' in title_lower):
                        print(" ✓ 'user' + 'privacy' in title: +25 points")
                    if ('customer' in title_lower and 'privacy' in title_lower):
                        print(" ✓ 'customer' + 'privacy' in title: +25 points")
                        
                    # URL points breakdown
                    print("URL POINTS:")
                    if 'privacy-notice' in url_lower or 'privacy_notice' in url_lower:
                        print(" ✓ 'privacy-notice' in URL: +60 points")
                    elif 'privacy-policy' in url_lower or 'privacy_policy' in url_lower:
                        print(" ✓ 'privacy-policy' in URL: +50 points")
                    elif 'privacy' in url_lower:
                        print(" ✓ 'privacy' in URL: +40 points")
                        
                    # User/Customer in URL
                    if (('user' in url_lower or 'customer' in url_lower) and 'privacy' in url_lower):
                        print(" ✓ 'user/customer' + 'privacy' in URL: +15 points")
                        
                    # Legal section bonus
                    if '/legal/' in url_lower:
                        print(" ✓ '/legal/' in URL: +15 points")
                    
                    print(f"TOTAL SCORE: {result['score']} points")
                    print("==================================")

            # Check the top 5 results instead of just 3
            for result_index in range(min(5, len(search_results))):
                best_result = search_results[result_index]["url"]

                try:
                    # Visit the page to verify it's a privacy page
                    print(f"Checking result: {best_result}")
                    await page.goto(
                        best_result, timeout=10000, wait_until="domcontentloaded"
                    )
                    await page.wait_for_timeout(
                        2000
                    )  # Increased wait time for full page load

                    # Perform verification
                    verification = await verify_is_privacy_page(page)

                    if (
                        verification["isPrivacyPage"] and verification["confidence"] >= 60
                    ):  # Increased threshold
                        print(
                            f"✅ Verification passed: {page.url} (score: {verification['confidence']})"
                        )
                        return page.url
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


async def bing_search_fallback(domain, page):
    """Search for privacy policy using Bing Search."""
    try:
        print("Attempting search engine fallback with Bing...")

        # Create a search query for the domain with specific site constraint and exact term matches
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data policy" OR "privacy"'
        bing_search_url = f"https://www.bing.com/search?q={search_query}"

        # Navigate to Bing search with shorter timeout
        await page.goto(bing_search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)  # Shorter wait for results to load

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
                        if (/\/\d{4}\/\d{1,2}\/\d{1,2}\//.test(urlPath) ||
                            /\/\d{5,}/.test(urlPath) ||
                            urlPath.includes('/news/') ||
                            urlPath.includes('/blog/') ||
                            urlPath.includes('/article/')) {
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
                    else if (titleLower.includes('data policy')) score += 55;
                    else if (titleLower.includes('privacy')) score += 30;
                    else if (titleLower.includes('data protection')) score += 25;
                    
                    // Boost URLs in common privacy directories
                    if (urlPath === '/privacy' || 
                        urlPath === '/privacy/' ||
                        urlPath === '/privacy-policy' || 
                        urlPath === '/privacy-policy/') {
                        score += 80;  // High boost for exact matches
                    }
                    else if (urlPath === '/legal/privacy' || 
                             urlPath === '/legal/privacy/' ||
                             urlPath === '/policies/privacy' || 
                             urlPath === '/policies/privacy/') {
                        score += 75;  // Also high boost
                    }
                    
                    // Scoring for description matches
                    if (descLower.includes('privacy policy')) score += 30;
                    else if (descLower.includes('privacy notice')) score += 28;
                    else if (descLower.includes('privacy statement')) score += 26;
                    else if (descLower.includes('data policy')) score += 25;
                    else if (descLower.includes('privacy')) score += 15;
                    
                    // URL-based scoring
                    if (urlLower.includes('privacy-policy')) score += 40;
                    else if (urlLower.includes('privacy-notice')) score += 38;
                    else if (urlLower.includes('privacy-statement')) score += 36;
                    else if (urlLower.includes('data-policy')) score += 34;
                    else if (urlLower.includes('privacy')) score += 30;
                    else if (urlLower.includes('data-protection')) score += 25;
                    else if (urlLower.includes('gdpr')) score += 20;
                    
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
                    result.url.toLowerCase().includes('data') || 
                    result.url.toLowerCase().includes('gdpr')
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
                    await page.wait_for_timeout(2000)

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


async def duckduckgo_search_fallback(domain, page):
    """Search for privacy policy using DuckDuckGo Search."""
    try:
        print("Attempting search engine fallback with DuckDuckGo...")
        search_query = f'site:{domain} "privacy policy" OR "privacy notice" OR "data policy" OR "privacy"'
        ddg_search_url = f"https://duckduckgo.com/?q={search_query}&t=h_&ia=web"

        await page.goto(ddg_search_url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Search for privacy-related links
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
                    else if (titleLower.includes('data policy')) score += 40;
                    else if (titleLower.includes('privacy')) score += 30;
                    else if (titleLower.includes('data protection')) score += 20;
                    if (descLower.includes('privacy policy')) score += 20;
                    else if (descLower.includes('privacy notice')) score += 18;
                    else if (descLower.includes('data policy')) score += 16;
                    else if (descLower.includes('privacy') && descLower.includes('personal')) score += 15;
                    if (urlLower.includes('/privacy-policy') || urlLower.includes('/privacy_policy')) score += 60;
                    else if (urlLower.includes('/privacy-notice') || urlLower.includes('/privacy_notice')) score += 55;
                    else if (urlLower.includes('/data-policy') || urlLower.includes('/data_policy')) score += 50;
                    else if (urlLower.includes('/privacy/') || urlLower.includes('/privacy')) score += 45;
                    else if (urlLower.includes('/legal/privacy')) score += 40;
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
    
def handle_navigation_failure(url: str, unverified_result: str = None) -> PrivacyResponse:
    """Handle case where navigation to the URL failed for privacy policy."""
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

def handle_error(url: str, unverified_result: str, error: str) -> PrivacyResponse:
    """Simplified error handler for privacy policy."""
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
        try:
            parsed = urlparse(url)
            # Accept www.amazon.com or amazon.com, but not foo.amazon.com
            return parsed.netloc.replace('www.', '') == main_domain.replace('www.', '')
        except Exception:
            return False
            
    # Add a domain bonus score to each link
    scored_links = []
    for link in links:
        domain_score = 50 if is_main_domain(link) else 0
        scored_links.append({"url": link, "domain_score": domain_score})
    
    # Prefer main domain links, then others
    main_links = [l["url"] for l in scored_links if l["domain_score"] > 0]
    
    # If we have main domain links, return those, otherwise return all links
    return main_links if main_links else links
