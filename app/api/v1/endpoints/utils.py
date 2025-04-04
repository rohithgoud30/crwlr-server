from urllib.parse import urlparse, urljoin
import logging
import re
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List, Dict, Any
import tldextract

try:
    from .social_platforms import detect_policy_page, get_alternative_policy_urls

    has_policy_detection = True
except ImportError:
    has_policy_detection = False

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_root_domain(domain: str) -> str:
    """
    Extract the root domain from a domain string.
    Example: 'www.example.co.uk' -> 'example.co.uk'
            'sub.example.com' -> 'example.com'
    """
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
        
    # Handle special cases like co.uk, com.au, etc.
    if parts[-2] in ["co", "com", "org", "gov", "edu"] and len(parts[-1]) == 2:
        if len(parts) > 3:
            return ".".join(parts[-3:])
        return domain
        
    return ".".join(parts[-2:])


def normalize_url(url: str) -> str:
    """
    Normalize URLs to ensure they're valid for request processing.
    Ensures URLs begin with http:// or https:// protocol.
    
    Returns:
        str: Normalized URL with proper protocol
    """
    url = url.strip()
    
    # Remove any trailing slashes for consistency
    url = url.rstrip("/")
    
    # Handle the "t3.chat" format with dots but no protocol
    # This is an important special case where domains may be entered without protocol
    if re.match(r"^[a-zA-Z0-9][\w\.-]+\.[a-zA-Z]{2,}$", url) and not url.startswith(
        ("http://", "https://")
    ):
        url = f"https://{url}"
        logger.info(f"Added protocol to domain name: {url}")
        return url
    
    # Check if URL has protocol, if not add https://
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
        logger.info(f"Added https:// protocol: {url}")
        
    return url


def prepare_url_variations(original_url: str) -> list:
    """
    Prepare different URL variations to try for link discovery.
    """
    variations = []
    
    # Ensure URL has protocol
    original_url = normalize_url(original_url)
    
    # Add the exact URL
    variations.append(original_url)
    
    # Parse for base domain variations
    parsed = urlparse(original_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # Only add base domain if it's different from the original URL
    if base_domain != original_url:
        variations.append(base_domain)
        
        # Add www/non-www variations of the base domain
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain_without_www = domain[4:]
            variations.append(f"{parsed.scheme}://{domain_without_www}")
        else:
            domain_with_www = f"www.{domain}"
            variations.append(f"{parsed.scheme}://{domain_with_www}")
        
        # Try HTTP instead of HTTPS as last resort
        if parsed.scheme == "https":
            variations.append(f"http://{parsed.netloc}")
    
    # Add common paths where links might be found
    for base_url in [base_domain]:
        variations.append(f"{base_url}/terms")
        variations.append(f"{base_url}/terms-of-service")
        variations.append(f"{base_url}/terms-of-use")
        variations.append(f"{base_url}/tos")
        variations.append(f"{base_url}/legal/terms")
        variations.append(f"{base_url}/legal")
        variations.append(f"{base_url}/privacy")
        variations.append(f"{base_url}/privacy-policy")
    
    # NEW: Try additional common privacy patterns
    for base_url in [base_domain]:
        # Add more variations specific to privacy policies
        variations.append(f"{base_url}/about/privacy")
        variations.append(f"{base_url}/about/legal/privacy")
        variations.append(f"{base_url}/about/privacy-policy")
        variations.append(f"{base_url}/legal/privacy")
        variations.append(f"{base_url}/legal/privacy-policy")
        variations.append(f"{base_url}/help/privacy")
        variations.append(f"{base_url}/settings/privacy")
        variations.append(f"{base_url}/data-protection")

        # Try with trailing slash for sites that require it
        variations.append(f"{base_url}/privacy/")
        variations.append(f"{base_url}/privacy-policy/")
        variations.append(f"{base_url}/legal/privacy/")

        # International variations
        variations.append(f"{base_url}/en/privacy")
        variations.append(f"{base_url}/en-us/privacy")
        variations.append(f"{base_url}/en-gb/privacy")

    # Remove any duplicates and ensure uniqueness
    variations = list(dict.fromkeys(variations))

    logger.info(f"Prepared URL variations: {variations}")
    return variations


def get_footer_score(link) -> float:
    """Calculate a score based on whether the link is in a footer, header, or similar section."""
    score = 0.0
    
    # Check if the link itself is in a footer-like or header-like element
    parent = link.parent
    depth = 0
    max_depth = 10  # Increased from 5 to 10 to look deeper in the DOM tree
    
    # First check for header elements - we'll prioritize checking header first
    while parent and parent.name and depth < max_depth:
        # Check element name
        if parent.name in ["header", "nav"]:
            score += 2.0
            break
            
        # Check classes and IDs for header indicators
        classes = " ".join(parent.get("class", [])).lower()
        element_id = parent.get("id", "").lower()
        
        # Header indicators
        if any(
            term in classes or term in element_id
            for term in ["header", "nav", "top", "menu"]
        ):
            score += 2.0
            break
            
        parent = parent.parent
        depth += 1
    
    # Reset for footer check
    parent = link.parent
    depth = 0
    
    # Then check for footer elements
    while parent and parent.name and depth < max_depth:
        # Check element name
        if parent.name in ["footer", "tfoot"]:
            score += 3.0
            break
            
        # Check classes and IDs
        classes = " ".join(parent.get("class", [])).lower()
        element_id = parent.get("id", "").lower()
        
        # Strong footer indicators
        if any(
            term in classes or term in element_id
            for term in ["footer", "bottom", "btm"]
        ):
            score += 3.0
            break
            
        # Secondary footer indicators
        if any(
            term in classes or term in element_id
            for term in ["legal", "copyright", "links"]
        ):
            score += 1.5
        
        parent = parent.parent
        depth += 1
    
    return score


def get_domain_score(href: str, base_domain: str) -> float:
    """Calculate domain relevance score with less dependency on known domains."""
    try:
        href_domain = urlparse(href).netloc.lower()
        if not href_domain:
            return 0.0
            
        # Same domain gets highest score
        if href_domain == base_domain:
            return 2.0
            
        # Subdomain relationship
        if href_domain.endswith("." + base_domain) or base_domain.endswith(
            "." + href_domain
        ):
            return 1.5
            
        # Check if the domains share a common root
        href_parts = href_domain.split(".")
        base_parts = base_domain.split(".")
        
        if len(href_parts) >= 2 and len(base_parts) >= 2:
            href_root = ".".join(href_parts[-2:])
            base_root = ".".join(base_parts[-2:])
            if href_root == base_root:
                return 1.0
                
        # For external domains, check if they look like legitimate policy hosts
        if any(term in href_domain for term in ["legal", "terms", "privacy", "policy"]):
            return 0.5
            
        # Don't heavily penalize external domains
        return 0.0
    except Exception:
        return -1.0


def get_common_penalties() -> list:
    """Get common penalty patterns for policy links."""
    return [
        ("/blog/", -5.0),
        ("/news/", -5.0),
        ("/article/", -5.0),
        ("/press/", -5.0),
        ("/2023/", -5.0),
        ("/2024/", -5.0),
        ("/posts/", -5.0),
        ("/category/", -5.0),
        ("/tag/", -5.0),
        ("/search/", -5.0),
        ("/product/", -5.0),
        ("/services/", -5.0),
        ("/solutions/", -5.0),
        ("/ai/", -5.0),
        ("/cloud/", -5.0),
        ("/digital/", -5.0),
        ("/enterprise/", -5.0),
        ("/platform/", -5.0),
        ("/technology/", -5.0),
        ("/consulting/", -5.0),
    ]


def is_likely_article_link(href_lower: str, full_url: str) -> bool:
    """
    Determine if a URL is likely to be a news article rather than a policy page.
    
    Args:
        href_lower: The lowercase href attribute
        full_url: The full URL for additional context
    
    Returns:
        bool: True if the URL appears to be an article, False otherwise
    """
    # News article patterns in URLs
    article_indicators = [
        "/article/", 
        "/news/",
        "/story/",
        "/blog/",
        "/post/",
        "/2023/",  # Year patterns
        "/2024/",
        "/politics/",
        "/business/",
        "/technology/",
        "/science/",
        "/health/",
        ".html",
        "/watch/",
        "/video/",
    ]
    
    # Check if URL contains article indicators
    for indicator in article_indicators:
        if indicator in href_lower:
            return True
    
    # Check for date patterns in URL paths
    date_pattern = re.compile(r"/\d{4}/\d{1,2}/\d{1,2}/")
    if date_pattern.search(href_lower):
        return True
    
    # Check if URL is from a known news domain
    parsed_url = urlparse(full_url)
    domain = parsed_url.netloc.lower()
    
    # Get the root domain for comparison
    root_domain = get_root_domain(domain)
    
    # Only consider it a policy link if it clearly has policy terms in the path
    if root_domain in [
        "reuters.com",
        "nytimes.com",
        "washingtonpost.com",
        "cnn.com",
        "bbc.com",
        "forbes.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "economist.com",
    ]:
        # For news sites, be extra careful
        # Only consider it a policy link if it clearly has policy terms in the path
        if not any(
            term in parsed_url.path.lower()
            for term in ["/privacy", "/terms", "/tos", "/legal"]
        ):
            return True
    
    return False


def is_on_policy_page(url: str, policy_type: str) -> bool:
    """
    Check if the URL appears to be already on a policy page.
    policy_type can be 'tos' or 'privacy'.
    """
    url_lower = url.lower()

    if policy_type == "tos":
        return any(
            term in url_lower
            for term in [
                "/terms",
                "/tos",
                "/terms-of-service",
                "/terms-and-conditions",
                "/legal/terms",
                "/conditions",
                "/user-agreement",
                "/eula",
                "/policies",
                "/policy",
                "/legal",
            ]
        )
    elif policy_type == "privacy":
        return any(
            term in url_lower
            for term in [
                "/privacy",
                "/privacy-policy",
                "/data-policy",
                "/data-protection",
                "/legal/privacy",
            ]
        )

    return False


def get_policy_patterns(policy_type: str) -> Tuple[List[str], List[str]]:
    """
    Get patterns for identifying policy links based on type.
    Returns (exact_patterns, url_patterns)
    """
    if policy_type == "tos":
        # Exact text match patterns (case insensitive)
        exact_patterns = [
            "terms of service",
            "terms of use",
            "terms and conditions",
            "terms & conditions",
            "user agreement",
            "conditions of use",
            "terms",
            "tos",
            "legal terms",
            "legal notices",
            "user terms",
        ]

        # URL patterns (these should be lowercase)
        url_patterns = [
            "/terms",
            "/tos",
            "/terms-of-service",
            "/terms-of-use",
            "/terms-and-conditions",
            "/legal/terms",
            "/user-agreement",
            "/conditions",
            "/eula",
        ]
    elif policy_type == "privacy":
        # Exact text match patterns (case insensitive)
        exact_patterns = [
            "privacy policy",
            "privacy notice",
            "privacy statement",
            "data policy",
            "data protection",
            "privacy",
            "your privacy",
            "privacy rights",
        ]

        # URL patterns (these should be lowercase)
        url_patterns = [
            "/privacy",
            "/privacy-policy",
            "/privacypolicy",
            "/privacy-notice",
            "/privacy-statement",
            "/data-policy",
            "/data-protection",
            "/legal/privacy",
        ]
    else:
        # Default to empty if policy type is not recognized
        exact_patterns = []
        url_patterns = []

    return exact_patterns, url_patterns


def get_policy_score(text: str, url: str, policy_type: str) -> float:
    """Calculate a score for a link based on its text and URL."""
    logger.debug(f"Scoring {policy_type} candidate: {url} with text: {text}")
    
    text_lower = text.lower()
    url_lower = url.lower()
    
    score = 0.0
    
    # Apply negative score for wrong policy type
    if policy_type == "privacy":
        # Check if this is likely a ToS URL rather than privacy
        if any(
            term in url_lower for term in ["/terms", "tos.", "termsofservice", "/tos/"]
        ):
            logger.debug(f"Penalizing for likely ToS URL patterns: {url}")
            score -= 12.0
        if any(
            term in text_lower
            for term in ["terms of service", "terms and conditions", "terms of use"]
        ):
            logger.debug(f"Penalizing for ToS text patterns: {text_lower}")
            score -= 10.0
    else:  # ToS
        # Check if this is likely a privacy URL rather than ToS
        if any(term in url_lower for term in ["/privacy", "privacypolicy", "/gdpr"]):
            logger.debug(f"Penalizing for likely privacy URL patterns: {url}")
            score -= 12.0
        if any(
            term in text_lower
            for term in ["privacy policy", "privacy notice", "data protection"]
        ):
            logger.debug(f"Penalizing for privacy text patterns: {text_lower}")
            score -= 10.0
    
    # Handle combined policies - less specific but still valid
    combined_patterns = ["legal", "policies", "legal notices", "legal information"]
    
    # Handle support policies - we want to avoid these
    support_patterns = [
        "help center",
        "support center",
        "contact",
        "faq",
        "customer support",
    ]
    
    # Check for combined policy hits
    combined_matches = sum(1 for pattern in combined_patterns if pattern in text_lower)
    if combined_matches > 0:
        score += combined_matches * 1.0
        
    # Check for support pattern hits (negative)
    support_matches = sum(1 for pattern in support_patterns if pattern in text_lower)
    if support_matches > 0:
        score -= support_matches * 3.0
    
    # Strong matches for privacy policy
    if policy_type == "privacy":
        strong_privacy_matches = [
            "privacy policy",
            "privacy notice",
            "Privacy Notice",
            "privacy statement",
            "data protection",
            "privacy",
            "privacidad",
            "datenschutz",
            "gdpr",
            "ccpa",
            "data privacy",
        ]
        
        # Count strong privacy matches in the text
        matches = sum(1 for pattern in strong_privacy_matches if pattern in text_lower)
        score += matches * 5.0
        
        # Additional bonus for explicit matches
        if (
            "privacy policy" in text_lower
            or "privacy notice" in text_lower
            or "Privacy Notice" in text
        ):
            score += 8.0
            
        if "privacy" in text_lower and (
            "policy" in text_lower or "notice" in text_lower
        ):
            score += 4.0
    else:  # ToS
        strong_terms_matches = [
            "terms of service",
            "terms of use",
            "terms and conditions",
            "conditions of use",
            "condition of use",
            "user agreement",
            "terms",
            "tos",
            "eula",
            "legal terms",
            "términos",
            "agb",
            "nutzungsbedingungen",  # Spanish and German terms
        ]
        
        # Count strong terms matches in the text
        matches = sum(1 for pattern in strong_terms_matches if pattern in text_lower)
        score += matches * 5.0
        
        # Additional bonus for explicit matches
        if any(
            exact in text_lower
            for exact in [
                "terms of service",
                "terms of use",
                "terms and conditions",
                "conditions of use",
            ]
        ):
            score += 8.0
    
    # Technical documentation penalties
    tech_doc_patterns = [
        "api documentation",
        "developer",
        "technical documentation",
        "sdk",
        "integration",
        "api terms",
    ]
    
    # Penalize technical documentation links for ToS search
    if policy_type == "tos":
        tech_matches = sum(1 for pattern in tech_doc_patterns if pattern in text_lower)
        if tech_matches > 0:
            score -= tech_matches * 4.0
    
    # URL-based scoring
    if policy_type == "privacy":
        url_patterns = [
            "/privacy",
            "privacy-policy",
            "privacy_policy",
            "privacypolicy",
            "datenschutz",
        ]
        url_matches = sum(1 for pattern in url_patterns if pattern in url_lower)
        score += url_matches * 3.0
    else:  # ToS
        url_patterns = [
            "/terms",
            "/tos",
            "terms-of-service",
            "terms-of-use",
            "termsofservice",
            "conditions-of-use",
            "condition-of-use",
        ]
        url_matches = sum(1 for pattern in url_patterns if pattern in url_lower)
        score += url_matches * 3.0
    
    # Additional URL pattern scoring
    if re.search(r"/(?:legal|policies)/(?:privacy|data)", url_lower):
        if policy_type == "privacy":
            score += 4.0
        else:
            score -= 2.0  # Penalty if looking for ToS
            
    if re.search(r"/(?:legal|policies)/(?:terms|tos)", url_lower):
        if policy_type == "tos":
            score += 4.0
        else:
            score -= 2.0  # Penalty if looking for privacy
    
    # Exact filename matches
    privacy_filenames = [
        "privacy.html",
        "privacy.php",
        "privacy.htm",
        "privacy.aspx",
        "privacy",
    ]
    tos_filenames = [
        "terms.html",
        "tos.html",
        "terms.php",
        "terms.htm",
        "terms.aspx",
        "tos",
        "terms",
    ]

    if policy_type == "privacy" and any(
        url_lower.endswith(fname) for fname in privacy_filenames
    ):
        score += 5.0
    elif policy_type == "tos" and any(
        url_lower.endswith(fname) for fname in tos_filenames
    ):
        score += 5.0
        
    logger.debug(f"Final score for {policy_type} candidate: {url} = {score}")
    return score


def is_likely_false_positive(url: str, policy_type: str) -> bool:
    """
    Check if a URL is likely to be a false positive (not actually a policy page)
    using contextual analysis instead of hardcoded domains.
    
    Optimized version with early returns for better performance.
    """
    # Fast initial check for common policy paths - these should never be false positives
    url_lower = url.lower()
    
    # Fastest path: Direct word match in URL path for obvious policy pages
    if (
        "/privacy" in url_lower
        or "/terms" in url_lower
        or "/tos" in url_lower
        or "/policy" in url_lower
    ):
        return False
        
    # Parse the URL only if we need more analysis (avoid parsing in common cases)
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    
    # Check for policy indicators in the query (only if we need to parse deeper)
    query = parsed_url.query.lower()
    if "privacy" in query or "policy" in query:
        if policy_type == "privacy":
            return False
    
    if "terms" in query or "tos" in query:
        if policy_type == "tos":
            return False
            
    # Additional policy indicators in path (less common cases)
    policy_indicators = ["/policies", "/legal", "/gdpr", "/data-protection"]
    if any(indicator in path for indicator in policy_indicators):
        return False
    
    # Fast path for media files (almost always false positives)
    file_extensions = [".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4", ".mp3", ".exe"]
    if any(url_lower.endswith(ext) for ext in file_extensions):
        return True
    
    # Social media paths (common false positives)
    social_media_paths = [
        "/share",
        "/status/",
        "/post/",
        "/photo/",
        "/video/",
        "/profile/",
        "/user/",
        "/account/",
        "/page/",
        "/channel/",
    ]
    
    if any(pattern in path for pattern in social_media_paths):
        # Unless it explicitly has policy in the path
        if (
            "privacy" not in path
            and "policy" not in path
            and "terms" not in path
            and "legal" not in path
        ):
            return True
    
    # App store links that aren't privacy-related
    if (
        "play.google.com" in url_lower
        or "apps.apple.com" in url_lower
        or "itunes.apple.com" in url_lower
    ):
        # Only accept policy-specific app store links
        if not any(
            term in url_lower
            for term in [
                "privacy",
                "policy",
                "terms",
                "tos",
                "legal",
                "conditions",
                "agreement",
            ]
        ):
            return True
    
    # Support pages mistaken for policies
    if policy_type == "privacy" and "/support-policy" in path and "privacy" not in path:
        # Support policies that don't mention privacy are false positives
        if not any(
            term in url_lower for term in ["/data", "/personal", "/gdpr", "/private"]
        ):
            return True
    
    # Documentation mistaken for ToS
    if policy_type == "tos" and (
        "/docs" in path or "/documentation" in path or "/doc/" in path
    ):
        # Only if it doesn't explicitly mention terms
        if not any(
            term in path
            for term in ["/terms", "/tos", "/legal", "/eula", "/conditions"]
        ):
            return True
    
    # Special case for PDFs - they might be legitimate policy documents
    if url_lower.endswith(".pdf"):
        # If the PDF filename contains policy terms, it's likely legitimate
        filename = path.split("/")[-1].lower()
        policy_terms = ["privacy", "policy", "terms", "tos", "legal", "data"]
        if not any(term in filename for term in policy_terms):
            return True
    
    # Blog posts and news articles are rarely policy pages
    blog_patterns = ["/blog/", "/news/", "/article/", "/post/", "/press-release/"]
    if any(pattern in path for pattern in blog_patterns):
        # Unless they explicitly mention policies in the URL
        if policy_type == "privacy" and not any(
            term in path for term in ["/privacy", "/data-policy"]
        ):
            return True
        if policy_type == "tos" and not any(
            term in path for term in ["/terms", "/tos", "/conditions"]
        ):
            return True
    
    # Date patterns in URL typically indicate news/blog content, not policies
    if re.search(r"/\d{4}/\d{1,2}(/\d{1,2})?/", path):
        # Unless they explicitly mention policies
        if not any(
            term in path for term in policy_indicators + ["/privacy", "/terms", "/tos"]
        ):
            return True
    
    return False


def is_correct_policy_type(url: str, policy_type: str) -> bool:
    """
    Check if a URL appears to be the correct policy type (privacy vs terms).
    This helps prevent returning privacy policy when looking for ToS and vice versa.
    """
    if not url:
        return False

    url_lower = url.lower()
    parsed_url = urlparse(url_lower)
    path = parsed_url.path

    # Use generic policy detection if available
    if has_policy_detection:
        # Use the flexible pattern matching approach
        return detect_policy_page(url, policy_type)

    if policy_type == "privacy":
        # Check that this is NOT a terms URL
        if any(
            term in path
            for term in [
                "/terms",
                "/tos",
                "/terms-of-service",
                "/terms-of-use",
                "/terms-and-conditions",
                "terms.html",
                "/legal/terms",
                "/user-agreement",
                "/eula",
            ]
        ):
            # Skip this extra check if the URL also contains privacy indicators
            if not any(
                term in path
                for term in [
                    "/privacy",
                    "/data-protection",
                    "/data-policy",
                    "/gdpr",
                    "/ccpa",
                ]
            ):
            return False
    else:  # policy_type == "tos"
        # Check that this is NOT a privacy URL
        if any(
            term in path
            for term in [
                "/privacy",
                "/privacy-policy",
                "/data-protection",
                "/data-policy",
                "/gdpr",
                "/ccpa",
                "privacy.html",
                "/legal/privacy",
            ]
        ):
            # Skip this extra check if the URL also contains terms indicators
            if not any(
                term in path
                for term in ["/terms", "/tos", "/legal/terms", "/conditions", "/eula"]
            ):
            return False

    return True


def find_policy_by_class_id(
    soup: BeautifulSoup, policy_type: str, base_url: str = ""
) -> Optional[str]:
    """
    Find policy links by looking in specific structures like footers, navigation, etc.
    This is a more targeted approach using HTML structure and class/id attributes.
    
    Args:
        soup: BeautifulSoup object
        policy_type: 'privacy' or 'tos'
        base_url: The base URL to build absolute URLs
        
    Returns:
        The URL of the found policy or None
    """
    # Get a set of patterns based on policy type
    policy_terms = get_policy_terms(policy_type)
    
    # Best candidates from different methods
    candidates = []
    
    # 1. First check for standard footer elements with policy links
    footer_elements = soup.select(
        'footer, [class*="footer"], [id*="footer"], [role="contentinfo"]'
    )
    for footer in footer_elements:
        links = footer.find_all("a", href=True)
        
        # Create a list of links with their text and URL
        footer_links = []
        for link in links:
            href = link.get("href")
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
                
            # Get text - both display text and title attribute
            text = link.get_text().strip().lower()
            title = link.get("title", "").lower()
            combined_text = f"{text} {title}"
            
            # Create absolute URL if necessary
            if base_url and href.startswith("/"):
                abs_url = urljoin(base_url, href)
            else:
                abs_url = href
                
            footer_links.append(
                {"element": link, "text": combined_text, "url": abs_url}
            )
            
        # Score and sort based on relevance to the policy type
        scored_links = []
        for link in footer_links:
            url_lower = link["url"].lower()
            text = link["text"]
            score = 0
            
            # Check for exact matches first (highest priority)
            for term in policy_terms["exact_matches"]:
                if term in text:
                    score += 12  # Highest score for exact matches
                    break
                    
            # Check for policy-type specific terms in URL
            for term in policy_terms["url_patterns"]:
                if term in url_lower:
                    score += 8
                    break
                    
            # Check for general policy indicators in text
            for term in policy_terms["general_indicators"]:
                if term in text:
                    score += 5
                    break
            
            # Avoid false positives
            if is_likely_false_positive(url_lower, policy_type):
                score = 0
            
            # Only consider links with positive scores
            if score > 0:
                scored_links.append(
                    {"url": link["url"], "score": score, "method": "footer_text_match"}
                )
        
        # If we found some good candidates in this footer, add the best one
        if scored_links:
            best_match = max(scored_links, key=lambda x: x["score"])
            candidates.append(best_match)
    
    # 2. Check navigation elements
    nav_elements = soup.select('nav, [role="navigation"], [class*="nav"], [id*="nav"]')
    for nav in nav_elements:
        # Skip main navigation which likely has product links, not policy links
        if nav.get("class") and any(
            c in str(nav.get("class")).lower() for c in ["main", "primary"]
        ):
            continue
            
        links = nav.find_all("a", href=True)
        nav_links = []
        
        for link in links:
            href = link.get("href")
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
                
            # Get text - both display text and title attribute
            text = link.get_text().strip().lower()
            title = link.get("title", "").lower()
            combined_text = f"{text} {title}"
            
            # Create absolute URL if necessary
            if base_url and href.startswith("/"):
                abs_url = urljoin(base_url, href)
            else:
                abs_url = href
                
            # Avoid false positives
            if is_likely_false_positive(abs_url, policy_type):
                continue
                
            nav_links.append({"element": link, "text": combined_text, "url": abs_url})
            
        # Score links based on policy indicators
        scored_nav_links = []
        for link in nav_links:
            url_lower = link["url"].lower()
            text = link["text"]
            score = 0
            
            # Check for exact matches first
            for term in policy_terms["exact_matches"]:
                if term in text:
                    score += 10  # High score but slightly lower than footer
                    break
                    
            # Check for policy-type specific terms in URL
            for term in policy_terms["url_patterns"]:
                if term in url_lower:
                    score += 6
                    break
                    
            # Check for general policy indicators
            for term in policy_terms["general_indicators"]:
                if term in text:
                    score += 3
                    break
            
            # Only consider links with positive scores
            if score > 0:
                scored_nav_links.append(
                    {"url": link["url"], "score": score, "method": "nav_text_match"}
                )
                
        # If we found some good candidates in navigation, add the best one
        if scored_nav_links:
            best_match = max(scored_nav_links, key=lambda x: x["score"])
            candidates.append(best_match)
    
    # 3. Check for policy-specific containers by class/id
    policy_containers = soup.select(
        f'[class*="{policy_type}"], [id*="{policy_type}"], [class*="legal"], [id*="legal"]'
    )
    
    for container in policy_containers:
        links = container.find_all("a", href=True)
        container_links = []
        
        for link in links:
            href = link.get("href")
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
                
            # Get text - both display text and title attribute
            text = link.get_text().strip().lower()
            title = link.get("title", "").lower()
            combined_text = f"{text} {title}"
            
            # Create absolute URL if necessary
            if base_url and href.startswith("/"):
                abs_url = urljoin(base_url, href)
            else:
                abs_url = href
                
            # Avoid false positives
            if is_likely_false_positive(abs_url, policy_type):
                continue
                
            container_links.append(
                {"element": link, "text": combined_text, "url": abs_url}
            )
            
        # Score links based on policy indicators
        scored_container_links = []
        for link in container_links:
            url_lower = link["url"].lower()
            text = link["text"]
            score = 0
            
            # Check for exact matches first
            for term in policy_terms["exact_matches"]:
                if term in text:
                    score += 8
                    break
                    
            # Check for policy-type specific terms in URL
            for term in policy_terms["url_patterns"]:
                if term in url_lower:
                    score += 5
                    break
                    
            # Check for general policy indicators
            for term in policy_terms["general_indicators"]:
                if term in text:
                    score += 2
                    break
            
            # Only consider links with positive scores
            if score > 0:
                scored_container_links.append(
                    {"url": link["url"], "score": score, "method": "container_match"}
                )
                
        # If we found some good candidates in this container, add the best one
        if scored_container_links:
            best_match = max(scored_container_links, key=lambda x: x["score"])
            candidates.append(best_match)
    
    # Return the best match across all methods if found
    if candidates:
        best_candidate = max(candidates, key=lambda x: x["score"])
        logger.info(
            f"Found best candidate for {policy_type} via {best_candidate['method']}: {best_candidate['url']} (Score: {best_candidate['score']})"
        )
        return best_candidate["url"]
        
    return None


def get_policy_terms(policy_type: str) -> dict:
    """
    Get policy-specific terms organized by categories.
    Enhanced with comprehensive terms across multiple languages.
    
    Args:
        policy_type: 'privacy' or 'tos'
        
    Returns:
        Dictionary with categorized terms
    """
    if policy_type == "privacy":
        return {
            "exact_matches": [
                # English
                "privacy policy",
                "privacy notice",
                "data protection",
                "privacy statement",
                "privacy & cookies",
                "privacy and cookies",
                "your privacy rights",
                "cookie policy",
                "privacy settings",
                "privacy center",
                "privacy preferences",
                "privacy choices",
                # German
                "datenschutzerklärung",
                "datenschutz",
                "datenschutzrichtlinie",
                # French
                "politique de confidentialité",
                "protection des données",
                # Spanish
                "política de privacidad",
                "aviso de privacidad",
                "privacidad",
                # Italian
                "informativa sulla privacy",
                "politica della privacy",
                # Portuguese
                "política de privacidade",
                "privacidade",
                # Japanese
                "プライバシーポリシー",
                "プライバシー規約",
                # Chinese
                "隐私政策",
                "隐私权政策",
                "私隱政策",
                # Russian
                "политика конфиденциальности",
                # Generic
                "privacy",
                "personal data",
            ],
            "url_patterns": [
                # Common URL patterns
                "/privacy",
                "/privacypolicy",
                "/privacy-policy",
                "/privacy_policy",
                "/privacy-notice",
                "/privacy_notice",
                "/data-protection",
                "/data_protection",
                "/pp",
                "/datenschutz",
                "/privacidad",
                "/privacy-statement",
                "/privacy_statement",
                "/cookies",
                "/cookie-policy",
                "/cookie_policy",
                "/privacy-center",
                "/privacy_center",
                "/privacy/policy",
                "/privacy-settings",
                "/privacy-choices",
                "/privacy/policy/",
                "/privacy.html",
                "/privacy.php",
                "/privacy.aspx",
                "/privacy.htm",
                # Special cases for major sites
                "privacy.amazon",
                "privacy.microsoft",
                "privacy.apple.com",
                "privacy.google",
                "help/privacy",
                "about/privacy",
                "legal/privacy",
                "policies/privacy",
                # Parameters
                "privacy=",
                "privacy&",
                "privacy?",
                "pp=",
                "pp&",
                "pp?",
                # Paths with IDs (Amazon style)
                "nodeId=",
                "nodeID=",
                "privacynotice",
                "privacyNotice",
            ],
            "general_indicators": [
                # English
                "privacy",
                "data",
                "cookies",
                "gdpr",
                "ccpa",
                "cpra",
                "personal information",
                "data protection",
                "information collected",
                # German
                "datenschutz",
                "daten",
                # French
                "confidentialité",
                "données",
                # Spanish
                "privacidad",
                "datos",
                # Italian
                "riservatezza",
                "dati",
                # Portuguese
                "privacidade",
                "dados",
                # Common abbreviations
                "gdpr",
                "ccpa",
                "cpra",
                "pipeda",
            ],
        }
    else:  # Terms of service
        return {
            "exact_matches": [
                # English
                "terms of service",
                "terms of use",
                "terms and conditions",
                "conditions of use",
                "condition of use",
                "user agreement",
                "legal terms",
                "terms",
                "conditions",
                "service terms",
                "terms & conditions",
                "legal information",
                "legal notice",
                "website terms",
                "user terms",
                "site terms",
                # German
                "nutzungsbedingungen",
                "allgemeine geschäftsbedingungen",
                "agb",
                # French
                "conditions d'utilisation",
                "conditions générales",
                "mentions légales",
                # Spanish
                "términos de servicio",
                "términos y condiciones",
                "condiciones de uso",
                "términos de uso",
                "aviso legal",
                # Italian
                "termini di servizio",
                "termini e condizioni",
                "condizioni d'uso",
                # Portuguese
                "termos de serviço",
                "termos de uso",
                "termos e condições",
                # Japanese
                "利用規約",
                "サービス利用規約",
                # Chinese
                "服务条款",
                "使用条款",
                "使用条件",
                # Russian
                "условия использования",
                "пользовательское соглашение",
            ],
            "url_patterns": [
                # Common URL patterns
                "/terms",
                "/tos",
                "/terms-of-service",
                "/terms_of_service",
                "/termsofservice",
                "/terms-of-use",
                "/terms_of_use",
                "/termsofuse",
                "/terms-and-conditions",
                "/terms_and_conditions",
                "/conditions-of-use",
                "/condition-of-use",
                "/user-agreement",
                "/user_agreement",
                "/legal-terms",
                "/legal_terms",
                "/terms.html",
                "/tos.html",
                "/terms.php",
                "/terms.aspx",
                "/terms.htm",
                "/legal",
                "/legal-info",
                "/legal_info",
                "/legal-notices",
                "/legal_notices",
                "/eula",
                "/service-terms",
                "/service_terms",
                # URL parameters
                "terms=",
                "tos=",
                "terms&",
                "tos&",
                "terms?",
                "tos?",
                # Paths with IDs (Amazon style)
                "nodeId=",
                "nodeID=",
                "termsofuse",
                "termsOfUse",
            ],
            "general_indicators": [
                # English
                "terms",
                "conditions",
                "legal",
                "agreement",
                "eula",
                "policy",
                "service agreement",
                "use",
                "rules",
                "guidelines",
                # German
                "bedingungen",
                "nutzung",
                "agb",
                # French
                "conditions",
                "utilisation",
                "légales",
                # Spanish
                "términos",
                "condiciones",
                "uso",
                "legal",
                # Italian
                "termini",
                "condizioni",
                "uso",
                # Portuguese
                "termos",
                "condições",
                "uso",
                # Common abbreviations
                "tos",
                "toc",
                "eula",
            ],
        }


def check_policy_urls(base_url: str, policy_type: str) -> Optional[str]:
    """
    Check common URL patterns for policy pages.
    Returns the first URL that likely exists, or None if none found.
    Note: This function doesn't actually check if the URLs exist,
    it just returns potential URLs to check.
    """
    patterns = get_policy_patterns(policy_type)[1]  # Get URL patterns

    # Generate URLs to try
    urls_to_try = [urljoin(base_url, pattern) for pattern in patterns]

    return urls_to_try


def find_link_in_head(
    soup: BeautifulSoup, base_url: str, policy_type: str
) -> Optional[str]:
    """
    Find policy links in the head element.
    """
    head_element = soup.find("head")
    if not head_element:
        return None

    # Get policy patterns
    exact_patterns, url_patterns = get_policy_patterns(policy_type)

    # Check <a> tags in head
    head_links = head_element.find_all("a", href=True)
    for link in head_links:
        href = link.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        link_text = link.get_text().strip().lower()
        link_title = link.get("title", "").lower()

        # Check for policy terms in text or title
        if any(
            pattern in link_text or pattern in link_title for pattern in exact_patterns
        ):
            absolute_url = urljoin(base_url, href)
            return absolute_url

        # Check for policy patterns in href
        if any(pattern in href.lower() for pattern in url_patterns):
            absolute_url = urljoin(base_url, href)
            return absolute_url

    # Also check <link> elements with rel attributes
    link_elements = head_element.find_all("link", rel=True, href=True)
    for link in link_elements:
        rel = link.get("rel", [""])[0].lower()
        href = link.get("href", "").strip()

        if policy_type == "tos" and any(
            term in rel for term in ["terms", "tos", "legal"]
        ):
            absolute_url = urljoin(base_url, href)
            return absolute_url

        if policy_type == "privacy" and any(
            term in rel for term in ["privacy", "data"]
        ):
            absolute_url = urljoin(base_url, href)
            return absolute_url

    return None


def find_link_in_footer(
    soup: BeautifulSoup, base_url: str, policy_type: str
) -> Optional[str]:
    """
    Find policy links in footer elements.
    Uses a more flexible approach to detect policy links in footers.
    """
    # Get policy patterns
    exact_patterns, url_patterns = get_policy_patterns(policy_type)

    # Find footer elements with broader selector to capture more potential footers
    footer_elements = soup.select(
        'footer, [class*="footer"], [id*="footer"], [role="contentinfo"], [class*="bottom"], [id*="bottom"], .legal, #legal'
    )

    policy_candidates = []

    for footer in footer_elements:
        for link in footer.find_all("a", href=True):
            href = link.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue

            absolute_url = urljoin(base_url, href)

            # Combine all text attributes for more comprehensive analysis
            link_text = " ".join(
                [
                    link.get_text().strip(),
                    link.get("title", "").strip(),
                    link.get("aria-label", "").strip(),
                ]
            ).lower()

            url_lower = absolute_url.lower()
            score = 0

            # Check exact text patterns
            if any(pattern in link_text for pattern in exact_patterns):
                score += 10

            # Check URL patterns
            if any(pattern in href.lower() for pattern in url_patterns):
                score += 8

            # Additional checks for terms in both URL and text
            if policy_type == "tos":
                if (
                    "terms" in link_text
                    or "conditions" in link_text
                    or "tos" in link_text
                ):
                    score += 5

                # Prevent confusing with privacy policy links
                if "privacy" in link_text and not any(
                    term in link_text for term in ["terms", "conditions", "legal"]
                ):
                    score -= 15

            elif policy_type == "privacy":
                if "privacy" in link_text:
                    score += 5

                # Prevent confusing with terms links
                if (
                    any(
                        term in link_text
                        for term in [
                            "terms of service",
                            "terms of use",
                            "terms and conditions",
                        ]
                    )
                    and "privacy" not in link_text
                ):
                    score -= 15

            # Add candidate if it seems reasonable
            if score > 0:
                policy_candidates.append((absolute_url, score, link_text))

    # Sort by score and return the best match
    if policy_candidates:
        policy_candidates.sort(key=lambda x: x[1], reverse=True)
        return policy_candidates[0][0]

    return None


def find_link_in_html(
    soup: BeautifulSoup, base_url: str, policy_type: str
) -> Optional[str]:
    """
    Find policy links anywhere in the HTML.
    """
    # Get policy patterns
    exact_patterns, url_patterns = get_policy_patterns(policy_type)

    # Try to find using text matching first (more reliable)
    for pattern in exact_patterns:
        # Find elements containing this text
        elements = soup.find_all(string=lambda text: text and pattern in text.lower())

        for element in elements:
            # Check if this element or its parent is inside an <a> tag
            parent = element.parent
            if parent and parent.name == "a" and parent.has_attr("href"):
                href = parent["href"]
                if href and not href.startswith(("#", "javascript:", "mailto:")):
                    return urljoin(base_url, href)

            # Also check nearby <a> tags
            nearby_links = element.find_all_next("a", href=True, limit=2)
            for link in nearby_links:
                href = link.get("href", "")
                if href and not href.startswith(("#", "javascript:", "mailto:")):
                    return urljoin(base_url, href)

    # Try all links as a fallback
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        absolute_url = urljoin(base_url, href)
        link_text = link.get_text().strip().lower()

        # Check text
        if any(pattern in link_text for pattern in exact_patterns):
            return absolute_url

        # Check URL
        if any(pattern in href.lower() for pattern in url_patterns):
            # Verify it's the correct policy type
            if is_correct_policy_type(href, policy_type):
                return absolute_url

    return None


def find_policy_link(url: str, soup: BeautifulSoup, policy_type: str) -> Dict[str, Any]:
    """
    Find a policy link (ToS or Privacy) on a page.
    Returns a dictionary with:
    {
        'url': original_url,
        'policy_url': found_policy_url or None,
        'head_link': link found in head,
        'footer_link': link found in footer,
        'html_link': link found in general HTML,
        'likely_exists': whether we think the policy exists
    }
    """
    if not soup:
        return {
            "url": url,
            "policy_url": None,
            "head_link": None,
            "footer_link": None,
            "html_link": None,
            "likely_exists": False,
        }

    # Parse the URL
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    # Check if already on a policy page
    if is_on_policy_page(url, policy_type):
        return {
            "url": url,
            "policy_url": url,
            "head_link": None,
            "footer_link": None,
            "html_link": None,
            "likely_exists": True,
        }

    # Find in different parts of the page
    head_link = find_link_in_head(soup, base_url, policy_type)
    footer_link = find_link_in_footer(soup, base_url, policy_type)
    html_link = find_link_in_html(soup, base_url, policy_type)

    # Determine the best link to return
    policy_url = head_link or footer_link or html_link

    # If no link found, generate common URLs to try
    likely_exists = policy_url is not None

    return {
        "url": url,
        "policy_url": policy_url,
        "head_link": head_link,
        "footer_link": footer_link,
        "html_link": html_link,
        "likely_exists": likely_exists,
    }


def is_likely_policy_menu_toggle(element) -> bool:
    """
    Detect if an element is likely a toggle for a menu containing policy links.
    These are often found in modern websites that hide policies in dropdown/popup menus.
    """
    if not element:
        return False

    # Check element text
    text = element.get_text(strip=True).lower()
    toggle_terms = [
        "legal",
        "policies",
        "info",
        "information",
        "more",
        "menu",
        "links",
        "about",
        "help",
        "...",
        "•••",
        "options",
        "settings",
    ]

    # Check if element has icon classes
    icon_classes = [
        "menu",
        "dropdown",
        "toggle",
        "hamburger",
        "more",
        "icon",
        "dots",
        "ellipsis",
        "settings",
        "gear",
        "cog",
    ]

    has_icon_class = False
    if element.has_attr("class"):
        classes = " ".join(element.get("class", [])).lower()
        has_icon_class = any(ic in classes for ic in icon_classes)

    # Check for aria attributes that suggest toggles
    aria_expanded = element.get("aria-expanded")
    aria_controls = element.get("aria-controls")
    aria_haspopup = element.get("aria-haspopup")

    # Check for data attributes that suggest toggles
    data_toggle = element.get("data-toggle")
    data_target = element.get("data-target")

    # Scoring system
    score = 0

    # Text indicators
    if any(term in text for term in toggle_terms):
        score += 2

    # Icon class indicators
    if has_icon_class:
        score += 2

    # ARIA attribute indicators
    if aria_expanded or aria_controls or aria_haspopup:
        score += 3

    # Data attribute indicators
    if data_toggle or data_target:
        score += 2

    # Is it a button, a, or other interactive element?
    if element.name in ["button", "a", "div"] and (
        element.get("role") in ["button", "menu", "menuitem"]
        or element.get("onclick")
        or element.has_attr("href")
    ):
        score += 2

    return score >= 3  # Threshold based on experimentation


def find_hidden_policy_links(soup, policy_type="privacy") -> List[Tuple[str, float]]:
    """
    Look for policy links that might be hidden in dropdowns, modals, or other
    hard-to-detect UI patterns. Returns a list of potential URLs with confidence scores.
    """
    base_url = ""
    results = []

    # First detect potential menu toggles
    potential_toggles = []

    # Elements with icon-like appearance
    for tag in ["button", "a", "div", "span", "i"]:
        for element in soup.find_all(tag):
            if is_likely_policy_menu_toggle(element):
                potential_toggles.append(element)

    logger.info(f"Found {len(potential_toggles)} potential menu toggles")

    # For each toggle, look for nearby or controlled elements that might contain policy links
    for toggle in potential_toggles:
        # Check for controlled elements via aria-controls
        controlled_id = toggle.get("aria-controls")
        if controlled_id:
            controlled = soup.find(id=controlled_id)
            if controlled:
                # Look for policy links in the controlled element
                for a in controlled.find_all("a"):
                    if a.get("href"):
                        href = a.get("href")
                        text = a.get_text(strip=True).lower()

                        # Match policy type
                        if policy_type == "privacy" and any(
                            term in text for term in ["privacy", "data", "information"]
                        ):
                            url = urljoin(base_url, href)
                            results.append(
                                (url, 0.8)
                            )  # Higher confidence because it's explicitly in a menu

                        elif policy_type == "tos" and any(
                            term in text for term in ["terms", "conditions", "tos"]
                        ):
                            url = urljoin(base_url, href)
                            results.append((url, 0.8))

        # Look for siblings or parent's children that might be the menu
        siblings = list(toggle.parent.children) if toggle.parent else []
        for sibling in siblings:
            if sibling.name in ["div", "ul", "nav", "section"]:
                for a in sibling.find_all("a"):
                    if a.get("href"):
                        href = a.get("href")
                        text = a.get_text(strip=True).lower()

                        # Match policy type
                        if policy_type == "privacy" and any(
                            term in text for term in ["privacy", "data", "information"]
                        ):
                            url = urljoin(base_url, href)
                            results.append((url, 0.7))

                        elif policy_type == "tos" and any(
                            term in text for term in ["terms", "conditions", "tos"]
                        ):
                            url = urljoin(base_url, href)
                            results.append((url, 0.7))

    # Look for specific patterns like footer-like elements with minimal content
    minimal_footers = []

    # Find stripped-down footers that might just contain icons or minimal text
    for element in soup.find_all(["div", "footer", "section"]):
        # Check for footer-like classes
        if element.has_attr("class"):
            classes = " ".join(element.get("class", [])).lower()
            if any(
                term in classes for term in ["footer", "legal", "bottom", "copyright"]
            ):
                # Check if it's a minimal footer (few elements, mostly icons)
                if len(element.find_all()) < 15:  # Arbitrary threshold for "minimal"
                    minimal_footers.append(element)

    # In minimal footers, even small icons can be legal links
    for footer in minimal_footers:
        for a in footer.find_all("a"):
            href = a.get("href")
            if href:
                # For minimal footers, even icon-only links might be legal links
                text = a.get_text(strip=True).lower()

                # Check if it's mostly icon with little text
                if len(text) < 5:
                    # Check if the URL looks like a policy link
                    url = urljoin(base_url, href)

                    # See if the URL contains policy keywords
                    lower_url = url.lower()
                    if policy_type == "privacy" and any(
                        term in lower_url
                        for term in ["privacy", "datapolicy", "data-policy"]
                    ):
                        results.append((url, 0.6))
                    elif policy_type == "tos" and any(
                        term in lower_url for term in ["terms", "tos", "conditions"]
                    ):
                        results.append((url, 0.6))

    return results


def detect_hidden_privacy_patterns(html_content: str) -> List[Tuple[str, float]]:
    """
    Analyzes HTML content for special patterns that might indicate hidden privacy links.
    This is especially useful for sites with complex layouts where standard methods fail.
    """
    results = []

    # Check for inline JavaScript that might load legal links
    js_patterns = [
        # Common ways policies are added dynamically
        r'(?:href|url).*[\'"](.+?(?:privacy|private|data).*?)[\'"]',
        r'(?:legal|policy|policies).*[\'"](.+?)[\'"].*?(?:privacy|data-policy)',
        r'(?:menu|footer).*?privacy.*?[\'"](.+?)[\'"]',
    ]

    for pattern in js_patterns:
        matches = re.findall(pattern, html_content, re.IGNORECASE)
        for match in matches:
            # Filter out obvious false positives
            if len(match) > 10 and "(" not in match and ")" not in match:
                if not match.startswith(("http", "/")):
                    # Seems to be a relative path
                    if match.startswith("./"):
                        match = match[2:]
                    results.append((match, 0.5))
                else:
                    results.append((match, 0.7))

    # Check for hidden links in data attributes
    soup = BeautifulSoup(html_content, "html.parser")
    for element in soup.find_all(attrs={"data-url": True}):
        url = element.get("data-url")
        if "privacy" in url.lower() or "policy" in url.lower():
            results.append((url, 0.6))

    # Check JSON-like structures embedded in scripts that might contain policy info
    # This is common in modern SPAs and React/Vue apps
    json_pattern = (
        r'"(?:privacyUrl|privacyLink|privacyPolicy|dataPolicy)"\s*:\s*"([^"]+)"'
    )
    matches = re.findall(json_pattern, html_content)
    for match in matches:
        results.append((match, 0.8))  # High confidence for explicit JSON fields

    return results


def get_page_type_from_content(text, policy_type="privacy"):
    """
    Analyze page content to determine if it's a privacy policy or ToS page.
    Returns a confidence score between 0 and 1.
    """
    text = text.lower()

    if policy_type == "privacy":
        # Required content indicators
        strong_indicators = [
            "privacy policy",
            "privacy statement",
            "data policy",
            "your privacy",
            "information we collect",
            "personal data",
            "gather information",
            "cookies",
            "third party",
        ]

        # Secondary indicators
        medium_indicators = [
            "information use",
            "data usage",
            "processing of",
            "collect and use",
            "analytics",
            "data protection",
            "personal information",
            "advertising",
            "tracking",
        ]

        # Word clusters that should appear together
        word_clusters = [
            {"privacy", "information", "collect"},
            {"data", "use", "share"},
            {"cookies", "tracking", "browser"},
            {"opt", "out", "choices"},
        ]

    elif policy_type == "tos":
        # Required content indicators
        strong_indicators = [
            "terms of service",
            "terms and conditions",
            "user agreement",
            "service agreement",
            "acceptable use",
            "legal agreement",
            "conditions of use",
        ]

        # Secondary indicators
        medium_indicators = [
            "your account",
            "intellectual property",
            "terminate",
            "suspension",
            "liability",
            "disclaimer",
            "warranty",
            "rights reserved",
        ]

        # Word clusters that should appear together
        word_clusters = [
            {"terms", "service", "agree"},
            {"conditions", "use", "account"},
            {"rights", "intellectual", "property"},
            {"terminate", "suspend", "account"},
        ]

    else:
        return 0.0  # Unknown policy type

    # Initial score
    score = 0.0

    # Check for strong indicators (high value)
    for phrase in strong_indicators:
        if phrase in text:
            score += 0.2
            if score > 0.9:  # Cap at 0.9
                return 0.9

    # Check for medium indicators (medium value)
    for phrase in medium_indicators:
        if phrase in text:
            score += 0.1
            if score > 0.8:  # Cap at 0.8 for medium indicators alone
                score = 0.8

    # Check for word clusters (patterns that should appear together)
    for cluster in word_clusters:
        if all(word in text for word in cluster):
            score += 0.15
            if score > 0.9:  # Cap at 0.9
                return 0.9

    # Adjust based on document length (privacy policies tend to be substantial)
    word_count = len(text.split())
    if word_count > 1000:  # Long document
        score += 0.1
    elif word_count < 200:  # Too short, probably not a policy
        score -= 0.2

    # Final score, ensure it's between 0 and 1
    return max(0.0, min(score, 1.0))


def is_on_policy_page(soup, policy_type="privacy"):
    """
    Determine if the current page is already a policy page.
    Returns a confidence score between 0 and 1.
    """
    # Check page title
    title = soup.title.string.lower() if soup.title else ""
    title_score = 0

    if policy_type == "privacy":
        privacy_title_terms = ["privacy", "data policy", "information policy"]
        if any(term in title for term in privacy_title_terms):
            title_score = 0.8
    else:  # Terms of Service
        tos_title_terms = ["terms", "conditions", "user agreement", "legal"]
        if any(term in title for term in tos_title_terms):
            title_score = 0.8

    # Check h1, h2 headings
    headings = [h.get_text(strip=True).lower() for h in soup.find_all(["h1", "h2"])]
    heading_score = 0

    if policy_type == "privacy":
        if any("privacy" in h for h in headings):
            heading_score = 0.7
        elif any("data" in h and "policy" in h for h in headings):
            heading_score = 0.6
    else:  # Terms of Service
        if any("terms" in h for h in headings):
            heading_score = 0.7
        elif any("conditions" in h for h in headings):
            heading_score = 0.6

    # Check content
    body_text = soup.get_text(strip=True).lower()
    content_score = get_page_type_from_content(body_text, policy_type)

    # Combine scores, with content having the highest weight
    final_score = max(title_score, heading_score, content_score)

    if title_score > 0 and content_score > 0.5:
        # Both title and content indicate a policy page - highest confidence
        final_score = max(0.9, final_score)

    return final_score


def is_likely_false_positive(url, soup, policy_type="privacy"):
    """
    Check if a URL is likely to be a false positive for a policy page.
    Returns True if it's likely a false positive.
    """
    url_lower = url.lower()

    # Check if URL contains keywords suggesting it's not a policy page
    non_policy_indicators = [
        "/blog/",
        "/post/",
        "/article/",
        "/news/",
        "/download/",
        "/product/",
        "/shop/",
        "/cart/",
        "/account/",
        "/user/",
        "/profile/",
        "/login/",
        "/signup/",
        "/register/",
        "/contact/",
    ]

    if any(indicator in url_lower for indicator in non_policy_indicators):
        # URL contains a path that's typically not a policy
        return True

    # Check page title if soup is provided
    if soup and soup.title:
        title = soup.title.string.lower() if soup.title else ""

        # Wrong type of policy check
        if policy_type == "privacy" and "terms" in title and "privacy" not in title:
            return True
        if (
            policy_type == "tos"
            and "privacy" in title
            and not any(term in title for term in ["terms", "conditions"])
        ):
            return True

        # Common false positive titles
        false_positive_titles = [
            "blog",
            "news",
            "article",
            "post",
            "download",
            "product",
            "pricing",
            "login",
            "sign up",
            "register",
            "account",
        ]

        if any(term in title for term in false_positive_titles) and not (
            ("privacy" in title and policy_type == "privacy")
            or (
                any(term in title for term in ["terms", "conditions"])
                and policy_type == "tos"
            )
        ):
            return True

    # Not detected as a false positive
    return False


def is_correct_policy_type(url, soup, policy_type="privacy"):
    """
    Determine if the page is the correct type of policy (privacy or ToS).
    Returns a confidence score between 0 and 1.
    """
    url_lower = url.lower()

    # URL-based heuristics
    url_score = 0.0

    if policy_type == "privacy":
        policy_url_indicators = [
            "privacy",
            "datapolicy",
            "data-policy",
            "data-protection",
        ]
        if any(indicator in url_lower for indicator in policy_url_indicators):
            url_score = 0.7
    else:  # Terms of Service
        tos_url_indicators = [
            "terms",
            "tos",
            "conditions",
            "legal-notice",
            "user-agreement",
        ]
        if any(indicator in url_lower for indicator in tos_url_indicators):
            url_score = 0.7

    # Content-based heuristics if soup is provided
    content_score = 0.0
    if soup:
        content_score = is_on_policy_page(soup, policy_type)

    # Combine scores, with content having higher weight
    if content_score > 0:
        # We have content information
        final_score = (url_score * 0.3) + (content_score * 0.7)
    else:
        # Only URL information
        final_score = url_score

    return final_score
