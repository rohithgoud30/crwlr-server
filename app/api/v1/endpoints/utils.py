from urllib.parse import urlparse, urljoin
import logging
import re
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List, Dict, Any

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
    if "play.google.com" in url_lower and "store/apps" in url_lower:
        # Only accept privacy-specific app store links
        if not (
            "privacy" in url_lower or "policy=" in url_lower or "privacy=" in url_lower
        ):
            return True

    if "apps.apple.com" in url_lower or "itunes.apple.com" in url_lower:
        # Only accept privacy-specific app store links
        if not (
            "privacy" in url_lower or "policy=" in url_lower or "privacy=" in url_lower
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
    Check if a URL likely points to the correct policy type.
    """
    url_lower = url.lower()

    if policy_type == "tos":
        # Check that it's not a privacy policy
        if any(
            term in url_lower
            for term in ["/privacy", "privacy-policy", "privacypolicy"]
        ):
            return False

        # Check if it contains terms indicators
        return any(
            term in url_lower
            for term in ["terms", "tos", "conditions", "agreement", "legal", "eula"]
        )

    elif policy_type == "privacy":
        # Check that it's not a terms page
        if any(
            term in url_lower
            for term in ["/terms", "terms-of", "tos", "terms-and-conditions"]
        ):
            return False

        # Check if it contains privacy indicators
        return any(
            term in url_lower for term in ["privacy", "data-policy", "data-protection"]
        )

    return False


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

        # Amazon specific handling - prioritize actual privacy notice over preferences
        if any("amazon.com" in link["url"] for link in footer_links):
            amazon_links = []
            for link in footer_links:
                url_lower = link["url"].lower()
                # Direct match for privacy notice/policy
                if (
                    "privacy" in link["text"] and "notice" in link["text"]
                ) or "/privacy" in url_lower:
                    # High priority for direct privacy notice links
                    if "/privacy/notice" in url_lower or "privacy.amazon" in url_lower:
                        logger.info(
                            f"Found Amazon direct privacy notice: {link['url']}"
                        )
                        return link["url"]

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
    """
    # Get policy patterns
    exact_patterns, url_patterns = get_policy_patterns(policy_type)

    # Find footer elements
    footer_elements = soup.select(
        'footer, [class*="footer"], [id*="footer"], [role="contentinfo"]'
    )

    for footer in footer_elements:
        for link in footer.find_all("a", href=True):
            href = link.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue

            absolute_url = urljoin(base_url, href)
            link_text = link.get_text().strip().lower()

            # Check exact text patterns
            if any(pattern in link_text for pattern in exact_patterns):
                return absolute_url

            # Check URL patterns
            if any(pattern in href.lower() for pattern in url_patterns):
                return absolute_url

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
