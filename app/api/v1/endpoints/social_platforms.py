"""
Utility functions for handling policy pages on various platforms.
Uses flexible pattern matching rather than hardcoded platform rules.
"""

import logging
from urllib.parse import urlparse, urljoin, parse_qs
from typing import Optional, List, Dict, Tuple
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Generic path patterns for policy pages
TOS_PATTERNS = [
    # Path patterns
    r"/(?:terms|tos)(?:/|$)",
    r"/(?:terms-of-service|terms-of-use|terms-and-conditions)(?:/|$)",
    r"/(?:legal|legal-info)(?:/|$)",
    r"/(?:policies|policy)(?:/|$)",
    r"/(?:user-agreement|eula)(?:/|$)",
    # Query patterns
    r"\?(?:.*&)?(?:terms|tos|legal)=",
    r"\?(?:.*&)?ref=(?:legal|terms|tos|pf)",
]

PRIVACY_PATTERNS = [
    # Path patterns
    r"/(?:privacy|privacy-policy)(?:/|$)",
    r"/(?:data-protection|data-policy)(?:/|$)",
    r"/(?:privacy/policy)(?:/|$)",
    r"/(?:legal|legal/privacy)(?:/|$)",
    r"/(?:about/privacy)(?:/|$)",
    r"/(?:confidentiality|confidential)(?:/|$)",
    # Query patterns
    r"\?(?:.*&)?privacy=",
    r"\?(?:.*&)?(?:.*entry_point=.*footer)",
    # Additional URL patterns
    r"/(?:.*privacy.*policy.*)",
    r"/(?:.*privacy.*statement.*)",
    r"/(?:.*data.*protection.*)",
    r"/(?:.*privacypolicy.*)",
    r"/(?:.*datenschutz.*)",  # German
    r"/(?:.*confidentialite.*)",  # French
    r"/(?:.*privacidad.*)",  # Spanish
]


def detect_policy_page(url: str, policy_type: str) -> bool:
    """
    Generic policy page detection using regex patterns instead of
    hardcoded platform-specific rules.

    Args:
        url: The URL to check
        policy_type: Either 'tos' or 'privacy'

    Returns:
        True if the URL matches policy page patterns, False otherwise
    """
    if not url:
        return False

    url_lower = url.lower()
    parsed_url = urlparse(url_lower)
    path = parsed_url.path
    query = parsed_url.query
    path_and_query = f"{path}?{query}" if query else path

    # Select appropriate patterns based on policy type
    patterns = TOS_PATTERNS if policy_type == "tos" else PRIVACY_PATTERNS

    # Check if the URL matches any of the patterns
    for pattern in patterns:
        if re.search(pattern, path_and_query, re.IGNORECASE):
            logger.info(f"URL {url} matched policy pattern: {pattern}")
            return True

    return False


def extract_policy_urls_from_page(url: str, soup, policy_type: str) -> List[str]:
    """
    Extract potential policy URLs from page content based on text patterns.

    Args:
        url: The base URL
        soup: BeautifulSoup object of the page
        policy_type: Either 'tos' or 'privacy'

    Returns:
        List of potential policy URLs
    """
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    candidates = []

    # Select appropriate text patterns based on policy type
    if policy_type == "tos":
        text_patterns = [
            r"terms.*(?:service|use)",
            r"(?:terms|tos)",
            r"legal.*(?:terms|agreement)",
            r"(?:user|service).*agreement",
            r"terms.*conditions",
            r"policies",
        ]
    else:  # privacy
        text_patterns = [
            r"privacy.*(?:policy|notice|statement)",
            r"privacy",
            r"data.*(?:policy|protection)",
        ]

    # Find links with matching text
    for link in soup.find_all("a", href=True):
        href = link.get("href", "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        # Get link text from multiple attributes for better matching
        link_text = " ".join(
            [
                link.get_text().strip(),
                link.get("title", "").strip(),
                link.get("aria-label", "").strip(),
            ]
        ).lower()

        # Check if link text matches any pattern
        if any(
            re.search(pattern, link_text, re.IGNORECASE) for pattern in text_patterns
        ):
            absolute_url = urljoin(url, href)
            candidates.append(absolute_url)

    return candidates


def get_alternative_policy_urls(url: str, policy_type: str) -> List[str]:
    """
    Generate alternative policy URLs based on common patterns.

    Args:
        url: The base URL of the site
        policy_type: Either 'tos' or 'privacy'

    Returns:
        List of potential policy URLs to try
    """
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

    alternatives = []

    if policy_type == "tos":
        common_paths = [
            "/terms",
            "/tos",
            "/terms-of-service",
            "/terms-of-use",
            "/terms-and-conditions",
            "/legal/terms",
            "/legal",
            "/policies",
            "/policies?ref=pf",  # Common pattern but not platform-specific
        ]
    else:  # privacy
        common_paths = [
            "/privacy",
            "/privacy-policy",
            "/data-policy",
            "/data-protection",
            "/legal/privacy",
            "/privacy/policy",
        ]

    for path in common_paths:
        alternatives.append(urljoin(base_url, path))

    return alternatives


def analyze_link_context(url, soup, policy_type="privacy") -> List[Tuple[str, float]]:
    """
    Perform enhanced analysis of link context to find policy links.
    This function examines both the link text and its surrounding content.

    Args:
        url: The base URL of the page
        soup: BeautifulSoup object of the page
        policy_type: Either 'tos' or 'privacy'

    Returns:
        List of tuples containing URLs and their confidence score,
        ordered by confidence (highest first)
    """
    base_url = url
    base_domain = urlparse(url).netloc
    results = []

    # Set up patterns based on policy type
    if policy_type == "privacy":
        keywords = [
            "privacy",
            "privacy policy",
            "data policy",
            "data protection",
            "personal data",
            "personal information",
            "datenschutz",
            "privacidad",
            "vie privée",
        ]
        # Expanded keyword list for more challenging sites
        extended_keywords = [
            "privacy",
            "private",
            "data",
            "personal",
            "information",
            "policy",
            "datenschutz",
            "privacidad",
            "confidential",
        ]
    else:  # tos
        keywords = [
            "terms",
            "terms of service",
            "terms of use",
            "conditions",
            "tos",
            "user agreement",
            "legal",
            "nutzungsbedingungen",
            "términos",
            "conditions d'utilisation",
        ]
        # Expanded keyword list
        extended_keywords = [
            "terms",
            "service",
            "use",
            "legal",
            "conditions",
            "agreement",
            "accept",
        ]

    # Get all links
    links = soup.find_all("a", href=True)

    # First check: special handling for specific patterns
    # Check for footer buttons or special UI patterns
    for link in links:
        href = link.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        abs_url = urljoin(base_url, href)

        # Check if this is a known policy pattern
        if detect_policy_page(abs_url, policy_type):
            # This URL matches known policy patterns - high confidence
            results.append((abs_url, 8.0))
            continue

        # Handle special footer/legal section patterns
        parent_div = link.find_parent(
            "div",
            class_=lambda c: c
            and any(term in c.lower() for term in ["footer", "legal", "bottom"]),
        )
        if parent_div:
            # This link is in a footer or legal section
            link_text = link.get_text(strip=True).lower()

            # Check if the link text contains any of the (extended) keywords
            if any(keyword in link_text for keyword in extended_keywords):
                # This is a strong match in a footer - high confidence
                results.append((abs_url, 7.0))
                continue

            # Link is in footer but text doesn't match - lower confidence
            if policy_type == "privacy" and "privacy" in abs_url.lower():
                results.append((abs_url, 5.0))
            elif policy_type == "tos" and any(
                term in abs_url.lower() for term in ["terms", "tos"]
            ):
                results.append((abs_url, 5.0))

    # Second check: analyze all remaining links with more detailed scoring
    for link in links:
        href = link.get("href", "")
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue

        abs_url = urljoin(base_url, href)

        # Skip if we already scored this URL in the first pass
        if any(url == abs_url for url, _ in results):
            continue

        # Initialize score
        score = 0.0

        # Score link text matches
        link_text = link.get_text(strip=True).lower()

        # Direct keyword match in link text (highest value)
        if any(keyword.lower() in link_text for keyword in keywords):
            score += 5.0
        # Partial keyword match using extended list
        elif any(keyword.lower() in link_text for keyword in extended_keywords):
            score += 2.5

        # Special handling for links with minimal or icon-only text (common in modern UIs)
        if len(link_text) < 3:
            # Check for title or aria-label on icon-based links
            title = link.get("title", "").lower()
            aria_label = link.get("aria-label", "").lower()

            if any(keyword in title for keyword in keywords) or any(
                keyword in aria_label for keyword in keywords
            ):
                score += 4.0

            # Check URL for policy keywords
            if policy_type == "privacy" and "privacy" in abs_url.lower():
                score += 3.0
            elif policy_type == "tos" and any(
                term in abs_url.lower() for term in ["terms", "tos", "conditions"]
            ):
                score += 3.0

        # Check parent elements for context
        parent = link.parent
        depth = 0

        while parent and parent.name and depth < 5:
            # Check parent's classes and ID for footer/legal indicators
            classes = " ".join(parent.get("class", [])).lower()
            parent_id = parent.get("id", "").lower()

            # Common container class patterns for footer/legal sections
            container_classes = [
                "footer",
                "legal",
                "links",
                "policy",
                "bottom",
                "copyright",
                "info",
            ]

            # Check classes
            for container_class in container_classes:
                if container_class in classes:
                    score += 1.5
                    break

            # Check ID
            parent_id_lower = parent_id.lower()
            for container_class in container_classes:
                if container_class in parent_id_lower:
                    score += 1.5
                    break

            # Check if parent is a footer or legal section
            if parent.name == "footer":
                score += 2.0

            # Move to next parent level
            parent = parent.parent
            depth += 1

        # Look at sibling links for context
        siblings = link.find_parent().find_all("a", href=True) if link.parent else []
        for sibling in siblings:
            if sibling == link:
                continue

            sibling_text = sibling.get_text().strip().lower()
            # If this link is near other policy links, it's more likely to be a policy
            if policy_type == "privacy" and any(
                kw in sibling_text for kw in ["terms", "cookie", "legal"]
            ):
                score += 1.0
            elif policy_type == "tos" and any(
                kw in sibling_text for kw in ["privacy", "cookie", "legal"]
            ):
                score += 1.0

        # Check for footer location
        if is_in_footer(link):
            score += 2.0

        # If score is significant, add to results
        if score > 3.0:
            results.append((abs_url, score))

    # Sort by confidence score
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def is_in_footer(element):
    """Check if an element is within a footer section."""
    parent = element.parent
    depth = 0

    while parent and depth < 5:
        if parent.name == "footer":
            return True

        # Check for footer class/id indicators
        classes = parent.get("class", [])
        class_text = " ".join(classes).lower() if classes else ""

        if "footer" in class_text or "bottom" in class_text:
            return True

        parent_id = parent.get("id", "").lower()
        if "footer" in parent_id or "bottom" in parent_id:
            return True

        parent = parent.parent
        depth += 1

    return False
