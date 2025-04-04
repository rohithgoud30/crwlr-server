from fastapi import APIRouter, Response
from pydantic import BaseModel, field_validator
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import re
from typing import Optional, Any, List, Tuple
import asyncio
from playwright.async_api import async_playwright
import logging
from .utils import (
    normalize_url,
    prepare_url_variations,
    get_footer_score,
    get_domain_score,
    get_common_penalties,
    is_on_policy_page,
    is_likely_article_link,
    get_root_domain,
    get_policy_patterns,
    get_policy_score,
    find_policy_by_class_id,
    is_likely_false_positive,
    is_correct_policy_type,
)
import inspect
import time
import aiohttp
from .social_platforms import (
    detect_policy_page,
    get_alternative_policy_urls,
    extract_policy_urls_from_page,
)

# Filter out the XML parsed as HTML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


class TosRequest(BaseModel):
    url: str  # Changed from HttpUrl to str to allow any input

    @field_validator("url")
    @classmethod
    def validate_and_transform_url(cls, v: str) -> str:
        """
        Basic URL validation and normalization using utils.
        """
        return normalize_url(v)


class TosResponse(BaseModel):
    url: str
    tos_url: Optional[str] = None
    success: bool
    message: str
    method_used: str = "standard"  # Indicates which method was used to find the ToS


def find_tos_link(url: str, soup: BeautifulSoup) -> Optional[str]:
    """
    Find the most likely Terms of Service link on a page.
    Returns the absolute URL to the ToS if found, otherwise None.
    """
    start_time = time.time()

    try:
        # Parse the URL to get the domain
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        base_url = f"{parsed_url.scheme}://{domain}"

        # Check if we're already on a ToS page
        if is_on_policy_page(url, "tos"):
            logger.info(f"Already on a ToS page: {url}")
            return url

        # First try to find by checking head element links
        head_element = soup.find("head")
        if head_element:
            head_links = head_element.find_all("a", href=True)
            for link in head_links:
                href = link.get("href", "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:")):
                    continue

                link_text = link.get_text().strip().lower()
                link_title = link.get("title", "").lower()

                # Check for terms-related terms in link or attributes
                if (
                    "terms" in link_text
                    or "terms" in link_title
                    or "tos" in href.lower()
                    or "terms" in href.lower()
                ):

                    absolute_url = urljoin(url, href)
                    logger.info(f"Found ToS link in head: {absolute_url}")
                    return absolute_url

            # Also check link elements with rel="terms" or similar
            link_elements = head_element.find_all("link", rel=True, href=True)
            for link in link_elements:
                rel = link.get("rel", [""])[0].lower()
                href = link.get("href", "").strip()

                if "terms" in rel or "tos" in rel or "legal" in rel:
                    absolute_url = urljoin(url, href)
                    logger.info(
                        f"Found ToS link with rel attribute in head: {absolute_url}"
                    )
                    return absolute_url

        # Try to find by structure like footer, header navigation, etc.
        structural_result = find_policy_by_class_id(soup, "tos", base_url)
        if structural_result:
            logger.info(f"Found ToS link via structural search: {structural_result}")
            return structural_result

        # Special domain-specific handling for common sites
        # Rest of the function...

        # If not found, proceed with the general scoring approach (as fallback)
        logger.info("Structural search failed, proceeding with general link scoring...")

        # Parse and cache domain data (avoid repeated parsing)
        parsed_url = urlparse(url)
        current_domain = parsed_url.netloc.lower()
        base_domain = get_root_domain(current_domain)
        is_legal_page = is_on_policy_page(url, "tos")

        # Get patterns once (avoid repeated function calls)
        exact_patterns, strong_url_patterns = get_policy_patterns("tos")

        # Track high-potential links to reduce processing time
        candidates = []
        promising_candidates = (
            []
        )  # Track especially promising candidates for early return

        # Additional patterns to filter out false positives
        false_positive_patterns = [
            "/learn",
            "/tutorial",
            "/course",
            "/education",
            "/docs",
            "/documentation",
            "/guide",
            "/start",
            "/getting-started",
            "/examples",
            "/showcase",
        ]

        # Look for links only in relevant areas first (faster than processing all links)
        footer_elements = soup.select(
            'footer, [class*="footer"], [id*="footer"], [role="contentinfo"]'
        )
        nav_elements = soup.select(
            'nav, [role="navigation"], header, [class*="header"], [id*="header"]'
        )
        policy_elements = soup.select(
            '[class*="legal"], [id*="legal"], [class*="terms"], [id*="terms"], [class*="policies"], [id*="policies"]'
        )

        # Process high-value elements first
        priority_elements = footer_elements + nav_elements + policy_elements

        # Fast path - check high-priority elements first before doing full scan
        for element in priority_elements:
            for link in element.find_all("a", href=True):
                href = link.get("href", "").strip()
                if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue

                # Compute absolute URL once
                absolute_url = urljoin(url, href)

                # Skip URLs with false positive patterns
                if any(
                    pattern in absolute_url.lower()
                    for pattern in false_positive_patterns
                ):
                    continue

                # Fast check for obvious matches
                url_lower = absolute_url.lower()
                if (
                    "/terms" in url_lower
                    or "/tos" in url_lower
                    or "terms-of-service" in url_lower
                    or "/policies" in url_lower
                    or "/policies_center" in url_lower
                ):
                    # Skip the detailed analysis for obvious matches
                    if not is_likely_false_positive(absolute_url, "tos"):
                        logger.info(
                            f"Found clear ToS link in priority element: {absolute_url}"
                        )
                        return absolute_url

                # Skip privacy URLs when looking for ToS
                if "/privacy" in url_lower or "privacy-policy" in url_lower:
                    continue

                # Get text once to avoid repeating the expensive text extraction
                link_text = " ".join(
                    [
                        link.get_text().strip(),
                        link.get("title", "").strip(),
                        link.get("aria-label", "").strip(),
                    ]
                ).lower()

                # Skip privacy text
                if "privacy" in link_text and not any(
                    term in link_text for term in ["terms", "tos", "conditions"]
                ):
                    continue

                # If text has clear ToS keywords, prioritize
                if (
                    "terms of service" in link_text
                    or "terms of use" in link_text
                    or "terms and conditions" in link_text
                    or "policies" in link_text
                    or "policy" in link_text
                ):
                    if not is_likely_false_positive(absolute_url, "tos"):
                        promising_candidates.append(
                            (absolute_url, 25.0)
                        )  # Very high score for clear matches
                        # If we find >2 promising candidates, return the best one immediately
                        if len(promising_candidates) > 2:
                            promising_candidates.sort(key=lambda x: x[1], reverse=True)
                            logger.info(
                                f"Found promising ToS candidate in priority scan: {promising_candidates[0][0]}"
                            )
                            return promising_candidates[0][0]

        # If we found any promising candidates, use them
        if promising_candidates:
            promising_candidates.sort(key=lambda x: x[1], reverse=True)
            logger.info(f"Using best promising candidate: {promising_candidates[0][0]}")
            return promising_candidates[0][0]

        # Only do full scan if priority elements didn't yield results
        # Iterate through all links to find the Terms of Service
        for link in soup.find_all("a", href=True):
            href = link.get("href", "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue

            try:
                absolute_url = urljoin(url, href)

                # Skip likely false positives (early rejection)
                if is_likely_false_positive(absolute_url, "tos"):
                    continue

                # Extra check - filter out known false positive patterns
                url_lower = absolute_url.lower()
                if any(pattern in url_lower for pattern in false_positive_patterns):
                    continue

                # Extra check - skip privacy policy URLs when looking for ToS
                if "/privacy" in url_lower or "privacy-policy" in url_lower:
                    continue

                # Check if this is a different domain from the original site
                target_domain = urlparse(absolute_url).netloc.lower()
                target_base_domain = get_root_domain(target_domain)
                is_cross_domain = current_domain != target_domain

                if is_cross_domain:
                    # For cross-domain links, be stricter - require explicit terms references
                    if not any(
                        term in url_lower for term in ["/terms", "/tos", "/legal/terms"]
                    ):
                        continue

                # Ensure this is not a Privacy URL
                if not is_correct_policy_type(absolute_url, "tos"):
                    continue

                # Get text once to avoid repeated operations
                link_text = " ".join(
                    [
                        link.get_text().strip(),
                        link.get("title", "").strip(),
                        link.get("aria-label", "").strip(),
                    ]
                ).lower()

                # Skip links that are explicitly privacy policies
                if "privacy" in link_text and not any(
                    term in link_text for term in ["terms", "tos", "conditions"]
                ):
                    continue

                # Skip educational links with terms like "learn"
                if any(
                    edu_term in link_text
                    for edu_term in [
                        "learn",
                        "tutorial",
                        "course",
                        "guide",
                        "start",
                        "example",
                    ]
                ):
                    if not any(
                        term in link_text for term in ["terms", "legal", "conditions"]
                    ):
                        continue

                if len(link_text.strip()) < 3:
                    continue

                # Calculate all scores in one batch
                score = 0.0
                footer_score = get_footer_score(link)
                domain_score = get_domain_score(absolute_url, base_domain)

                if domain_score < 0:
                    continue

                # Apply domain bonuses/penalties
                href_domain = target_domain  # Reuse already parsed domain

                if href_domain == current_domain:
                    score += 15.0  # Same-domain bonus
                elif is_cross_domain:
                    score -= 8.0  # Cross-domain penalty

                if is_legal_page and href_domain != base_domain:
                    continue

                # Apply exact pattern bonuses
                if any(re.search(pattern, link_text) for pattern in exact_patterns):
                    score += 6.0

                # Apply URL pattern bonuses
                if any(pattern in url_lower for pattern in strong_url_patterns):
                    score += 4.0

                # Add special bonus for terms links in footers
                if footer_score > 0 and ("terms" in link_text or "legal" in link_text):
                    score += 5.0

                # Apply policy score bonuses
                score += get_policy_score(link_text, absolute_url, "tos")

                # Apply common penalties
                for pattern, penalty in get_common_penalties():
                    if pattern in url_lower:
                        score += penalty

                # Combine all factors for final score
                final_score = (
                    (score * 1.5) + (footer_score * 2.5) + (domain_score * 1.0)
                )

                # Set dynamic threshold based on context
                threshold = 5.0
                if footer_score > 0:
                    threshold = 4.0
                if any(re.search(pattern, link_text) for pattern in exact_patterns):
                    threshold = 3.5

                if final_score > threshold:
                    candidates.append((absolute_url, final_score))

            except Exception as e:
                logger.error(f"Error processing link: {e}")
                continue

        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            logger.info(
                f"Sorted ToS policy candidates: {candidates[:3]}"
            )  # Only log top 3 for efficiency
            return candidates[0][0]

        return None

    except Exception as e:
        logger.error(f"Error processing ToS link: {e}")
        return None


def verify_tos_link(session: requests.Session, tos_link: str, headers: dict) -> bool:
    """
    Verify that a candidate ToS link actually points to a terms page.
    This function tries to visit the link and check content, with fallbacks for access issues.
    """
    try:
        logger.info(f"Verifying candidate ToS link: {tos_link}")

        tos_link_lower = tos_link.lower()
        parsed_url = urlparse(tos_link_lower)
        domain = parsed_url.netloc
        path = parsed_url.path
        query = parsed_url.query

        # Fast path - check if this URL matches policy patterns
        if detect_policy_page(tos_link, "tos"):
            logger.info(f"URL {tos_link} matches ToS patterns")
            return True

        # STEP 1: Fast path for obvious ToS URLs - don't even need to visit these
        # Common ToS patterns in URL paths
        primary_tos_patterns = [
            "/legal/terms",
            "/terms-of-service",
            "/terms-of-use",
            "/tos",
            "/terms.html",
            "/legal/terms-of-service",
            "terms-conditions",
            "conditions-of-use",
            "user-agreement",
            "/legal/terms-of-use",
            "/terms-and-conditions",
            "/policies",
            "/policies_center",
            "/terms",
            "/policies?ref=pf",  # Common pattern without hardcoding specific sites
        ]

        # Check for obvious ToS path patterns
        if (
            any(pattern in path for pattern in primary_tos_patterns)
            or "policies" in path
        ):
            logger.info(f"URL has primary ToS path pattern: {tos_link}")
            # For these high-confidence URLs, accept more easily even with access issues
            high_confidence = True

            # If the URL contains "policies" with no other indicators, we need additional checks
            # to distinguish between general policies pages and actual ToS pages
            if "policies" in path and not any(
                p in path for p in ["/terms", "/tos", "conditions"]
            ):
                # Use our pattern detection instead of hardcoded domains
                if detect_policy_page(tos_link, "tos"):
                    return True
                # For other sites, be more cautious with just "policies"
                high_confidence = False
            else:
                return True
        else:
            high_confidence = False

        # Special case: App store ToS checks for app-specific vs. general terms
        if "apple.com" in domain and any(
            pattern in path
            for pattern in ["/legal/terms", "/terms-of-service", "/terms"]
        ):
            # Get the caller function name for context-aware decision
            caller_frame = inspect.currentframe().f_back
            if caller_frame and "app_store_" in caller_frame.f_code.co_name:
                logger.warning(
                    f"Looking for app-specific terms rather than general terms"
                )
                return False

        # STEP 2: Check URL patterns before making any requests
        # Check for ToS-related query parameters
        if any(
            param in query
            for param in ["cou", "terms", "conditions", "tos", "legal", "ref=pf"]
        ):
            # Valid ToS link with parameters
            logger.info(f"URL has ToS-related query parameters: {tos_link}")
            return True

        # Check for obvious ToS path patterns
        if any(pattern in path for pattern in primary_tos_patterns):
            logger.info(f"URL has primary ToS path pattern: {tos_link}")
            # For these high-confidence URLs, accept more easily even with access issues
            high_confidence = True
        else:
            high_confidence = False

        # Check for obvious non-ToS patterns in URL - reject early
        if not high_confidence and any(
            pattern in query for pattern in ["utm_", "source=", "campaign="]
        ):
            # Only reject if no ToS indicators in path
            if not any(
                tos_term in path for tos_term in ["terms", "conditions", "tos", "legal"]
            ):
                # Special case for help centers
                if not (
                    "/help/" in path
                    and any(term in query for term in ["cou", "terms", "legal"])
                ):
                    logger.warning(
                        f"Rejecting URL with tracking params and no ToS indicators: {tos_link}"
                    )
                    return False

        # STEP 3: Try to fetch the page and analyze content
        try:
            # Add additional headers to help with access
            enhanced_headers = headers.copy()
            enhanced_headers.update(
                {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Referer": f"https://{domain}/",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "same-origin",
                }
            )

            # Make the request
            response = session.get(tos_link, headers=enhanced_headers, timeout=15)

            # Handle different status codes
            if response.status_code == 200:
                # Got content, analyze it
                pass
            elif response.status_code in [403, 429]:
                # Blocked or rate-limited
                logger.warning(
                    f"Access denied (status {response.status_code}) for {tos_link}"
                )
                # For high-confidence URLs, accept even with access issues
                if high_confidence:
                    logger.info(
                        f"Accepting high-confidence ToS URL despite access issues: {tos_link}"
                    )
                    return True

                # Check if domain is in known ToS URLs list
                known_tos_urls = {}  # This should be defined elsewhere in your code
                if domain in known_tos_urls:
                    for pattern in known_tos_urls[domain]:
                        if pattern in path:
                            logger.info(
                                f"Accepting known ToS pattern despite access issues: {tos_link}"
                            )
                            return True

                # Otherwise, need to reject due to lack of verification
                return False
            else:
                # Other error codes
                logger.warning(
                    f"ToS verification failed: status code {response.status_code} for {tos_link}"
                )
                if high_confidence:
                    return True
                return False

            # Parse the content
            soup = BeautifulSoup(response.text, "html.parser")

            # STEP 4: Analyze page content
            # Check page title
            title_elem = soup.find("title")
            if title_elem:
                title_text = title_elem.text.lower()

                # Terms indicators in title
                tos_title_terms = [
                    "terms",
                    "conditions",
                    "terms of service",
                    "terms of use",
                    "legal",
                    "user agreement",
                    "eula",
                    "policies",  # Added policies as valid title term
                ]

                if any(term in title_text for term in tos_title_terms):
                    logger.info(f"Found terms indicator in page title: {title_text}")
                    return True

                # Non-ToS indicators in title
                not_tos_title_terms = [
                    "products",
                    "pricing",
                    "signup",
                    "login",
                    "register",
                    "features",
                    "download",
                    "shop",
                    "cart",
                    "buy",
                ]

                # Only reject if multiple non-ToS indicators and high confidence
                non_tos_matches = sum(
                    1 for term in not_tos_title_terms if term in title_text
                )
                if (
                    non_tos_matches >= 2
                    and not any(term in title_text for term in tos_title_terms)
                    and not high_confidence
                ):
                    logger.warning(
                        f"Page title suggests this is not a ToS page: {title_text}"
                    )
                    return False

            # Check for headers with terms indicators
            headers_with_terms = soup.find_all(
                ["h1", "h2", "h3", "h4"],
                string=lambda s: s
                and any(
                    term in s.lower()
                    for term in [
                        "terms",
                        "conditions",
                        "legal",
                        "user agreement",
                        "acceptable use",
                        "service agreement",
                        "eula",
                        "policies",  # Added policies as valid header term
                    ]
                ),
            )

            if headers_with_terms:
                logger.info(
                    f"Found {len(headers_with_terms)} headers with terms indicators"
                )
                return True

            # Check for terms containers
            terms_containers = soup.find_all(
                ["div", "section"],
                id=lambda i: i
                and any(
                    term in i.lower()
                    for term in ["terms", "conditions", "legal", "tos"]
                ),
            )

            if not terms_containers:
                terms_containers = soup.find_all(
                    ["div", "section"],
                    class_=lambda c: c
                    and any(
                        term in c.lower()
                        for term in ["terms", "conditions", "legal", "tos"]
                    ),
                )

            if terms_containers:
                # Filter out footer containers that just contain links to legal pages
                valid_containers = []
                for container in terms_containers:
                    # Ignore small containers (like footers with just links)
                    container_text = container.get_text().strip()

                    # Skip containers that are likely navigation/footer elements
                    classes = container.get("class", [])
                    class_str = " ".join(classes).lower() if classes else ""

                    # Skip obvious footer/navigation containers
                    if any(
                        nav_term in class_str
                        for nav_term in ["footer", "nav", "menu", "copyright"]
                    ):
                        # Require more substantial content for these containers
                        if len(container_text) < 300:  # Footer links are usually brief
                            continue

                    # Check for actual terms content, not just links to terms
                    legal_phrases = [
                        "terms of service",
                        "terms of use",
                        "terms and conditions",
                        "user agreement",
                        "accept these terms",
                        "agree to these terms",
                        "legally binding",
                        "liability",
                        "disclaimer",
                        "warranty",
                        "intellectual property",
                        "privacy policy",
                        "governing law",
                    ]

                    # Require multiple legal phrases for a valid container
                    matches = sum(
                        1
                        for phrase in legal_phrases
                        if phrase in container_text.lower()
                    )
                    if (
                        matches >= 3 and len(container_text) > 500
                    ):  # Need substantial content
                        valid_containers.append(container)

                # Only accept if we have valid containers with actual terms content
                if valid_containers:
                    logger.info(
                        f"Found {len(valid_containers)} containers with substantial terms content"
                    )
                    return True
                else:
                    logger.warning(
                        "Found terms containers but they appear to be navigation/footer elements"
                    )
                    # Continue checks rather than accepting immediately

            # Look for paragraphs with terms content
            paragraphs_with_terms = soup.find_all(
                "p",
                string=lambda s: s
                and any(
                    phrase in s.lower()
                    for phrase in [
                        "terms of service",
                        "terms of use",
                        "terms and conditions",
                        "user agreement",
                        "accept these terms",
                        "agree to these terms",
                        "service agreement",
                        "legally binding",
                        "legal agreement",
                        "eula",
                        "end user license",
                    ]
                ),
            )

            if paragraphs_with_terms:
                # Check that we have substantial paragraph content (not just links)
                substantial_paragraphs = [
                    p for p in paragraphs_with_terms if len(p.get_text()) > 100
                ]
                if substantial_paragraphs:
                    logger.info(
                        f"Found {len(substantial_paragraphs)} substantial paragraphs with terms content"
                    )
                    return True

            # Check for bulleted lists with legal terms
            list_elements = soup.find_all(["ol", "ul"])
            list_with_terms = []

            for list_elem in list_elements:
                # Skip navigation and menu lists
                if list_elem.parent:
                    parent_classes = list_elem.parent.get("class", [])
                    parent_class_str = (
                        " ".join(parent_classes).lower() if parent_classes else ""
                    )
                    if any(
                        nav_term in parent_class_str
                        for nav_term in ["nav", "menu", "footer", "header"]
                    ):
                        continue

                list_items = list_elem.find_all("li")
                if len(list_items) >= 3:
                    # Convert list items to text
                    list_texts = [
                        item.get_text().strip().lower() for item in list_items
                    ]

                    # Check for short navigation items (likely menu)
                    if all(len(text) < 30 for text in list_texts):
                        continue

                    # Check for terms commonly found in footer/navigation lists
                    nav_terms = [
                        "home",
                        "products",
                        "services",
                        "about",
                        "contact",
                        "support",
                        "blog",
                        "news",
                        "sign in",
                        "login",
                        "register",
                    ]

                    # Skip lists that are likely navigation
                    nav_matches = sum(
                        1
                        for text in list_texts
                        if any(nav in text for nav in nav_terms)
                    )
                    if nav_matches > 2:
                        continue

                    # Join all text for deeper analysis
                    list_text = " ".join(list_texts)

                    # Look for legal terms with higher threshold
                    legal_terms = [
                        "terms of service",
                        "terms of use",
                        "terms and conditions",
                        "user agreement",
                        "accept these terms",
                        "agree to these terms",
                        "legally binding",
                        "liability",
                        "disclaimer",
                        "warranty",
                        "intellectual property",
                        "copyright",
                        "termination",
                        "prohibited",
                        "applicable law",
                        "jurisdiction",
                        "arbitration",
                        "dispute resolution",
                        "limitation of liability",
                        "indemnification",
                        "severability",
                    ]

                    # Require multiple specific legal terms for a terms-related list
                    term_matches = [term for term in legal_terms if term in list_text]
                    if (
                        len(term_matches) >= 2 and len(list_text) > 200
                    ):  # Need substantial content
                        list_with_terms.append(
                            {"element": list_elem, "matches": term_matches}
                        )

            if list_with_terms:
                # Don't accept terms based on just a list unless high confidence or many matches
                if high_confidence or any(
                    len(item["matches"]) >= 3 for item in list_with_terms
                ):
                    logger.info(
                        f"Found {len(list_with_terms)} lists with terms content: {[item['matches'] for item in list_with_terms]}"
                    )
                    return True
                else:
                    logger.warning(
                        "Found lists with some terms content but not enough for certainty"
                    )
                    # Continue to checks instead of accepting

            # Check for legal term density
            page_text = soup.get_text().lower()
            legal_terms = [
                "terms of service",
                "terms of use",
                "terms and conditions",
                "user agreement",
                "accept these terms",
                "agree to these terms",
                "legally binding",
                "liability",
                "disclaimer",
                "warranty",
                "intellectual property",
                "copyright",
                "termination",
                "privacy",
                "applicable law",
                "jurisdiction",
                "arbitration",
                "dispute resolution",
                "limitation of liability",
                "indemnification",
                "severability",
                "entire agreement",
                "modification",
                "waiver",
                "governing law",
            ]

            term_count = sum(1 for term in legal_terms if term in page_text)

            if term_count >= 5:  # Lower threshold for high confidence URLs
                logger.info(f"Found {term_count} legal terms in page content")
                return True

            # Final check for high confidence URLs - be more lenient
            if high_confidence:
                logger.info(
                    f"Accepting high confidence ToS URL despite limited content indicators: {tos_link}"
                )
                return True

            # Not enough evidence
            logger.warning(f"Insufficient indicators that {tos_link} is a ToS page")
            return False

        except requests.RequestException as e:
            logger.warning(f"Request error for {tos_link}: {str(e)}")
            # For high confidence URLs, accept even with access issues
            if high_confidence:
                logger.info(
                    f"Accepting high confidence ToS URL despite access issues: {tos_link}"
                )
                return True
            return False

    except Exception as e:
        logger.error(f"Error verifying ToS link: {str(e)}")
        return False


async def standard_tos_finder(
    variations_to_try: List[Tuple[str, str]], headers: dict, session: requests.Session
) -> TosResponse:
    """
    Try to find ToS link using standard requests + BeautifulSoup method.
    With fallback to privacy policy page if direct terms detection isn't reliable.
    """
    all_potential_tos_links = []

    # First check if this URL matches policy patterns - if so, try common paths
    first_url = variations_to_try[0][0]

    # Get common policy URLs to try first
    policy_urls = get_alternative_policy_urls(first_url, "tos")
    if policy_urls:
        logger.info(f"Trying {len(policy_urls)} common policy URLs first")
        for policy_url in policy_urls:
            try:
                logger.info(f"Checking policy URL: {policy_url}")

                # Check if this URL passes verification
                if detect_policy_page(policy_url, "tos"):
                    return TosResponse(
                        url=first_url,
                        tos_url=policy_url,
                        success=True,
                        message=f"Terms of Service found using policy pattern matching",
                        method_used="pattern_match",
                    )

                # Otherwise try to access the URL
                try:
                    response = session.head(
                        policy_url, headers=headers, timeout=10, allow_redirects=True
                    )
                    if response.status_code < 400:
                        return TosResponse(
                            url=first_url,
                            tos_url=policy_url,
                            success=True,
                            message=f"Terms of Service found using common policy URL",
                            method_used="common_policy_url",
                        )
                except Exception as e:
                    logger.warning(f"Error checking policy URL {policy_url}: {str(e)}")
                    continue

            except Exception as e:
                logger.error(f"Error processing policy URL {policy_url}: {str(e)}")
                continue

    # Continue with standard approach if pattern-matching approach failed
    for url, variation_type in variations_to_try:
        try:
            logger.info(f"Trying URL variation: {url} ({variation_type})")

            # First do a HEAD request to check for redirects
            head_response = session.head(
                url, headers=headers, timeout=10, allow_redirects=True
            )
            head_response.raise_for_status()

            # Get the final URL after redirects
            final_url = head_response.url
            if final_url != url:
                logger.info(f"Followed redirect: {url} -> {final_url}")

            # Now get the content of the final URL
            logger.info(f"Fetching content from {final_url}")
            response = session.get(final_url, headers=headers, timeout=15)
            response.raise_for_status()

            # Parse the HTML content
            soup = BeautifulSoup(response.text, "html.parser")

            # Find the ToS link from the page
            logger.info(f"Searching for ToS link in {final_url}")
            tos_link = find_tos_link(final_url, soup)

            # If standard search didn't find anything, try pattern-based extraction
            if not tos_link:
                logger.info(
                    f"Standard method didn't find ToS link, trying pattern-based extraction"
                )
                policy_candidates = extract_policy_urls_from_page(
                    final_url, soup, "tos"
                )

                if policy_candidates:
                    logger.info(
                        f"Found {len(policy_candidates)} policy candidates via pattern extraction"
                    )
                    # Try each candidate
                    for candidate in policy_candidates:
                        if detect_policy_page(candidate, "tos"):
                            logger.info(f"Candidate {candidate} matches ToS patterns")
                            tos_link = candidate
                            break

            if tos_link:
                # Additional check for false positives
                if is_likely_false_positive(tos_link, "tos"):
                    logger.warning(
                        f"Found link {tos_link} appears to be a false positive, skipping"
                    )
                    continue

                # Check if this is a correct policy type
                if not is_correct_policy_type(tos_link, "tos"):
                    logger.warning(
                        f"Found link {tos_link} appears to be a privacy policy, not ToS, skipping"
                    )
                    continue

                # Ensure the link is absolute
                if tos_link.startswith("/"):
                    parsed_final_url = urlparse(final_url)
                    base_url = f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                    tos_link = urljoin(base_url, tos_link)
                    logger.info(f"Converted relative URL to absolute URL: {tos_link}")

                # Verify that this is actually a ToS page by visiting it
                if not verify_tos_link(session, tos_link, headers):
                    logger.warning(
                        f"Candidate ToS link failed verification: {tos_link}"
                    )
                    # Add to potential links for further inspection
                    all_potential_tos_links.append(
                        (tos_link, final_url, variation_type, "failed_verification")
                    )
                    continue

                # If we make it here, we have a verified ToS link
                logger.info(
                    f"Verified ToS link: {tos_link} in {final_url} ({variation_type})"
                )
                return TosResponse(
                    url=final_url,  # Return the actual URL after redirects
                    tos_url=tos_link,
                    success=True,
                    message=f"Verified Terms of Service link found on final destination page: {final_url}"
                    + (
                        f" (Found at {variation_type})"
                        if variation_type != "original exact url"
                        else ""
                    ),
                    method_used="standard_verified",
                )
            else:
                logger.info(f"No ToS link found in {final_url} ({variation_type})")

                # If direct ToS detection failed, try to find a privacy policy link
                from .privacy import find_privacy_link

                logger.info(f"Trying to find privacy policy link in {final_url}")
                privacy_link = find_privacy_link(final_url, soup)

                if privacy_link:
                    # Make it absolute if needed
                    if privacy_link.startswith("/"):
                        parsed_final_url = urlparse(final_url)
                        base_url = (
                            f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                        )
                        privacy_link = urljoin(base_url, privacy_link)

                    logger.info(
                        f"Found privacy link: {privacy_link}, will check this page for ToS"
                    )
                    try:
                        # Visit the privacy policy page to find ToS link there
                        privacy_response = session.get(
                            privacy_link, headers=headers, timeout=15
                        )
                        privacy_soup = BeautifulSoup(
                            privacy_response.text, "html.parser"
                        )

                        # Now look for terms links in the privacy page
                        terms_from_privacy = find_tos_link(privacy_link, privacy_soup)

                        if terms_from_privacy:
                            # Make it absolute if needed
                            if terms_from_privacy.startswith("/"):
                                parsed_privacy_url = urlparse(privacy_link)
                                base_url = f"{parsed_privacy_url.scheme}://{parsed_privacy_url.netloc}"
                                terms_from_privacy = urljoin(
                                    base_url, terms_from_privacy
                                )

                            # Verify this ToS link
                            if verify_tos_link(session, terms_from_privacy, headers):
                                logger.info(
                                    f"Verified ToS link from privacy page: {terms_from_privacy}"
                                )
                                return TosResponse(
                                    url=final_url,
                                    tos_url=terms_from_privacy,
                                    success=True,
                                    message=f"Verified Terms of Service link found via privacy policy page from: {final_url}",
                                    method_used="privacy_page_to_terms_verified",
                                )
                            else:
                                logger.warning(
                                    f"ToS link from privacy page failed verification: {terms_from_privacy}"
                                )
                    except Exception as e:
                        logger.error(f"Error finding ToS via privacy page: {str(e)}")

        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({variation_type}): {str(e)}")
            continue
        except Exception as e:
            logger.error(f"Exception for {url} ({variation_type}): {str(e)}")
            continue

    # If we get here and have collected potential links, check them in order
    if all_potential_tos_links:
        logger.info(f"Checking {len(all_potential_tos_links)} potential ToS links")
        # Check any links that were classified as potential "learn" links
        for tos_link, final_url, variation_type, reason in all_potential_tos_links:
            # For each potential link, try to determine if it's a valid terms by looking at its content
            try:
                logger.info(f"Double-checking potential ToS link: {tos_link}")
                link_response = session.get(tos_link, headers=headers, timeout=15)
                link_soup = BeautifulSoup(link_response.text, "html.parser")

                # Check page title and headers for terms-related keywords
                title = link_soup.find("title")
                title_text = title.get_text().lower() if title else ""

                h1_elements = link_soup.find_all("h1")
                h1_texts = [h1.get_text().lower() for h1 in h1_elements]

                # If the page has terms-related keywords in title or headings
                terms_keywords = [
                    "terms",
                    "conditions",
                    "tos",
                    "terms of service",
                    "terms of use",
                    "legal",
                    "agreement",
                ]

                if any(keyword in title_text for keyword in terms_keywords) or any(
                    any(keyword in h1 for keyword in terms_keywords) for h1 in h1_texts
                ):
                    logger.info(f"Verified ToS link based on page content: {tos_link}")
                    return TosResponse(
                        url=final_url,
                        tos_url=tos_link,
                        success=True,
                        message=f"Terms of Service link verified by content analysis: {tos_link}",
                        method_used="content_verification",
                    )
            except Exception as e:
                logger.error(f"Error verifying potential ToS link {tos_link}: {str(e)}")

    # Try one more fallback - look for terms in the footer of the main page
    try:
        original_url = variations_to_try[0][0]
        response = session.get(original_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # Look specifically for footer links with terms-related text
        footer_elements = soup.find_all(
            ["footer", "div"],
            class_=lambda c: c and ("footer" in c.lower() if c else False),
        )
        footer_elements += soup.find_all(
            ["footer", "div"],
            id=lambda i: i and ("footer" in i.lower() if i else False),
        )

        for footer in footer_elements:
            for link in footer.find_all("a", href=True):
                href = link.get("href", "").strip()
                if not href:
                    continue

                link_text = link.get_text().lower().strip()
                if any(term in link_text for term in ["terms", "conditions", "legal"]):
                    # This looks like a terms link in the footer
                    absolute_url = urljoin(original_url, href)
                    logger.info(f"Found potential terms link in footer: {absolute_url}")

                    # Skip learn pages and other likely false positives
                    if "/learn" in absolute_url.lower() or is_likely_false_positive(
                        absolute_url, "tos"
                    ):
                        continue

                    # Verify this footer ToS link
                    if verify_tos_link(session, absolute_url, headers):
                        logger.info(f"Verified terms link from footer: {absolute_url}")
                        return TosResponse(
                            url=original_url,
                            tos_url=absolute_url,
                            success=True,
                            message=f"Verified Terms of Service link found in footer: {absolute_url}",
                            method_used="footer_search_verified",
                        )
                    else:
                        logger.warning(
                            f"Footer terms link failed verification: {absolute_url}"
                        )

        # Try to extract provider platform information
        site_host = urlparse(original_url).netloc.lower()
        platform_info = detect_site_platform(soup, original_url)

        if platform_info:
            platform_name, platform_domain = platform_info
            logger.info(f"Detected site platform: {platform_name} ({platform_domain})")

            # If the site is using a platform, try the platform's legal pages
            if platform_domain and platform_domain != site_host:
                # First try direct access to the main ToS page - this is most reliable
                primary_tos_url = f"https://{platform_domain}/legal/terms"
                try:
                    primary_tos_response = session.get(
                        primary_tos_url, headers=headers, timeout=15
                    )
                    if primary_tos_response.status_code == 200:
                        # Check if this is actually a ToS page
                        if verify_tos_link(session, primary_tos_url, headers):
                            logger.info(
                                f"Found primary ToS link for platform: {primary_tos_url}"
                            )
                            return TosResponse(
                                url=original_url,
                                tos_url=primary_tos_url,
                                success=True,
                                message=f"Terms of Service found via platform ({platform_name}): {primary_tos_url}",
                                method_used="platform_primary_tos",
                            )
                except Exception as e:
                    logger.error(f"Error checking primary ToS URL: {str(e)}")

                # Then try the legal hub page to find other ToS links
                platform_legal = f"https://{platform_domain}/legal"
                try:
                    platform_response = session.get(
                        platform_legal, headers=headers, timeout=15
                    )
                    if platform_response.status_code == 200:
                        platform_soup = BeautifulSoup(
                            platform_response.text, "html.parser"
                        )
                        platform_links = platform_soup.find_all("a", href=True)

                        # Prioritize links by importance
                        primary_terms_links = []
                        secondary_terms_links = []

                        for link in platform_links:
                            href = link.get("href", "").strip()
                            if not href:
                                continue

                            link_text = link.get_text().lower().strip()
                            absolute_url = urljoin(platform_legal, href)

                            # Skip non-terms links
                            if not any(
                                term in link_text for term in ["terms", "conditions"]
                            ):
                                continue

                            # Categorize by importance
                            href_lower = absolute_url.lower()

                            # Primary ToS links - general Terms of Service/Use
                            if (
                                "/terms" in href_lower or "/tos" in href_lower
                            ) and not any(
                                specific in href_lower
                                for specific in [
                                    "/event-",
                                    "/partner-",
                                    "/enterprise-",
                                    "/services-",
                                    "/specific-",
                                ]
                            ):
                                primary_terms_links.append((absolute_url, link_text))
                            # Secondary ToS links - specific terms
                            else:
                                secondary_terms_links.append((absolute_url, link_text))

                        # First check primary links
                        for absolute_url, link_text in primary_terms_links:
                            logger.info(f"Checking primary terms link: {absolute_url}")
                            if verify_tos_link(session, absolute_url, headers):
                                return TosResponse(
                                    url=original_url,
                                    tos_url=absolute_url,
                                    success=True,
                                    message=f"Terms of Service found via platform ({platform_name}) legal page: {absolute_url}",
                                    method_used="platform_legal_primary",
                                )

                        # Then check secondary links only if no primary links work
                        for absolute_url, link_text in secondary_terms_links:
                            logger.info(
                                f"Checking secondary terms link: {absolute_url}"
                            )
                            if verify_tos_link(session, absolute_url, headers):
                                return TosResponse(
                                    url=original_url,
                                    tos_url=absolute_url,
                                    success=True,
                                    message=f"Specific Terms of Service found via platform ({platform_name}) legal page: {absolute_url}",
                                    method_used="platform_legal_secondary",
                                )
                except Exception as e:
                    logger.error(f"Error with platform legal fallback: {str(e)}")

    except Exception as e:
        logger.error(f"Error in footer fallback search: {str(e)}")

    return TosResponse(
        url=variations_to_try[0][0],  # Use the original URL
        success=False,
        message="No Terms of Service link found with standard method",
        method_used="standard_failed",
    )


def detect_site_platform(
    soup: BeautifulSoup, url: str
) -> Tuple[Optional[str], Optional[str]]:
    """
    Detect what platform/framework a site is using by examining the HTML.
    Returns a tuple of (platform_name, platform_domain) if detected, or (None, None) if not.
    """
    try:
        html_content = str(soup)

        # Look for common platform indicators in the HTML
        platforms = [
            # (platform name, detection string, legal domain)
            ("Vercel", "vercel.com", "vercel.com"),
            ("Netlify", "netlify.app", "netlify.com"),
            ("Wix", "wix.com", "wix.com"),
            ("Shopify", "shopify.com", "shopify.com"),
            ("WordPress", "wp-content", "wordpress.com"),
            ("Squarespace", "squarespace.com", "squarespace.com"),
            ("GitHub Pages", "github.io", "github.com"),
            ("Webflow", "webflow.com", "webflow.com"),
            ("Cloudflare Pages", "pages.dev", "cloudflare.com"),
            ("Firebase", "firebaseapp.com", "firebase.google.com"),
            ("AWS Amplify", "amplifyapp.com", "aws.amazon.com"),
            ("Heroku", "herokuapp.com", "heroku.com"),
        ]

        # Check for platform indicators in the HTML
        for platform_name, detection_string, platform_domain in platforms:
            if detection_string in html_content:
                logger.info(f"Detected platform: {platform_name} based on HTML content")
                return platform_name, platform_domain

        # Check for platform-specific meta tags
        generator_tag = soup.find("meta", {"name": "generator"})
        if generator_tag and generator_tag.get("content"):
            generator_content = generator_tag.get("content").lower()

            if "wordpress" in generator_content:
                return "WordPress", "wordpress.com"
            elif "wix" in generator_content:
                return "Wix", "wix.com"
            elif "shopify" in generator_content:
                return "Shopify", "shopify.com"
            elif "squarespace" in generator_content:
                return "Squarespace", "squarespace.com"
            elif "webflow" in generator_content:
                return "Webflow", "webflow.com"

        # Check for platform in URL
        url_lower = url.lower()
        for platform_name, detection_string, platform_domain in platforms:
            if detection_string in url_lower:
                logger.info(f"Detected platform: {platform_name} based on URL")
                return platform_name, platform_domain

        return None, None
    except Exception as e:
        logger.error(f"Error detecting platform: {str(e)}")
        return None, None


@router.post(
    "/tos",
    response_model=TosResponse,
    responses={
        200: {"description": "Terms of Service found successfully"},
        404: {"description": "Terms of Service not found", "model": TosResponse},
    },
)
async def find_tos(request: TosRequest, response: Response) -> TosResponse:
    """
    Takes a base URL and returns the Terms of Service page URL.
    This endpoint accepts partial URLs like 'google.com' and will
    automatically add the 'https://' protocol prefix if needed.
    """
    original_url = request.url
    logger.info(f"Processing ToS request for URL: {original_url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }

    # Try multiple methods to find the ToS
    url_variations = prepare_url_variations(original_url)

    # Create a list of (url, variation_type) tuples for standard finder
    variations_to_try = [(url, "original") for url in url_variations]

    # Approach 1: Standard web requests with fallbacks
    with requests.Session() as session:
        standard_result = await standard_tos_finder(variations_to_try, headers, session)

    if standard_result.success:
        return standard_result

    # Approach 2: Try common URL patterns
    common_patterns_result = await try_common_tos_patterns(original_url, headers)

    if common_patterns_result.success:
        return common_patterns_result

    # Approach 3: Use Playwright for JavaScript-rendered content
    playwright_result = await playwright_tos_finder(original_url)

    if playwright_result.success:
        return playwright_result

    # Approach 4: Final fallback - scan complete HTML as last resort
    logger.info(
        f"All previous methods failed, trying complete HTML scan for {original_url}"
    )
    html_scan_result = await scan_html_for_tos_links(original_url)

    if html_scan_result.success:
        return html_scan_result

    # If all methods have failed, return a failure response
    logger.warning(
        f"No Terms of Service link found for {original_url} after trying all methods"
    )
    return TosResponse(
        url=original_url,
        success=False,
        message="No Terms of Service link found after trying all available methods",
        method_used="all_methods_failed",
    )


async def handle_app_store_tos(url: str, headers: dict) -> TosResponse:
    """
    Special handling for App Store URLs - first get the privacy policy link,
    then try to find the ToS link on the same domain as the privacy policy.
    """
    try:
        logger.info(f"Using specialized App Store ToS handling for: {url}")

        # First, try to get the app name for better logging
        session = requests.Session()
        app_name = None
        app_id = None

        # Parse URL to get app ID
        parsed_url = urlparse(url)
        if parsed_url.path:
            id_match = re.search(r"/id(\d+)", parsed_url.path)
            if id_match:
                app_id = id_match.group(1)

        try:
            response = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, "html.parser")

            # Look for app name
            title_elem = soup.find("title")
            if title_elem:
                app_name = title_elem.text.strip().split("-")[0].strip()

            if not app_name:
                h1_elem = soup.find("h1")
                if h1_elem:
                    app_name = h1_elem.text.strip()
        except Exception as e:
            logger.error(f"Error extracting app name: {str(e)}")

        app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}' if app_id else ''}"

        # Step 1: Try to find the privacy policy of the app and derive ToS from there
        logger.info(
            f"Looking for privacy policy link to derive ToS link for {app_info}"
        )

        # Import here to avoid circular imports
        from .privacy import find_privacy_link

        # First, we find the privacy policy of the app
        try:
            response = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, "html.parser")

            # Look for privacy policy links
            privacy_link = find_privacy_link(url, soup)

            if privacy_link:
                logger.info(
                    f"Found privacy policy link for App Store item: {privacy_link}"
                )

                # Make sure the link is absolute
                if privacy_link.startswith("/"):
                    privacy_link = urljoin(url, privacy_link)

                # Get the base domain of the privacy policy
                pp_parsed = urlparse(privacy_link)
                pp_base_domain = f"{pp_parsed.scheme}://{pp_parsed.netloc}"
                logger.info(
                    f"Extracted base domain from privacy policy: {pp_base_domain}"
                )

                # New Step: Try common ToS paths directly on the privacy policy domain first
                # This addresses the issue where we find privacy policy at controlgame.com/privacy/
                # and want to directly check controlgame.com/terms without checking the privacy page first
                logger.info(
                    f"Trying common ToS paths directly on privacy policy domain: {pp_base_domain}"
                )

                # Extract privacy path components to create matching terms paths
                pp_path = pp_parsed.path
                logger.info(f"Privacy policy path: {pp_path}")

                # If the privacy URL contains specific patterns, try corresponding terms patterns
                specific_candidates = []

                if "/privacy" in pp_path:
                    # If we have /privacy, try /terms
                    terms_path = pp_path.replace("/privacy", "/terms")
                    specific_candidates.append(terms_path)

                if "/privacy-policy" in pp_path:
                    # If we have /privacy-policy, try /terms-of-service, /terms-of-use, etc.
                    specific_candidates.append(
                        pp_path.replace("/privacy-policy", "/terms-of-service")
                    )
                    specific_candidates.append(
                        pp_path.replace("/privacy-policy", "/terms-of-use")
                    )
                    specific_candidates.append(
                        pp_path.replace("/privacy-policy", "/terms-and-conditions")
                    )

                # Regular common paths
                common_tos_paths = [
                    "/terms",
                    "/tos",
                    "/terms-of-service",
                    "/terms-of-use",
                    "/terms-and-conditions",
                    "/legal/terms",
                    "/legal",
                    "/terms.html",
                    "/legal/terms.html",
                    "/eula",
                ]

                # Try specific domain-based candidates first
                for path in specific_candidates:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(
                            f"Checking candidate ToS URL based on privacy path: {candidate_tos_url}"
                        )

                        # Skip if this is Apple's domain - we want app-specific terms only
                        candidate_parsed = urlparse(candidate_tos_url)
                        if candidate_parsed.netloc == "www.apple.com":
                            logger.warning(
                                f"Skipping Apple's domain for candidate ToS URL: {candidate_tos_url} - we only want app-specific terms"
                            )
                            continue

                        tos_check_response = session.get(
                            candidate_tos_url, headers=headers, timeout=15
                        )
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                # Extra check: don't return Apple's general terms
                                if "apple.com/legal/terms" in candidate_tos_url:
                                    logger.warning(
                                        f"Rejecting Apple's general terms: {candidate_tos_url}"
                                    )
                                    continue

                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found at path matching privacy policy path for {app_info}",
                                    method_used="app_store_pp_matching_path",
                                )
                    except Exception as e:
                        logger.error(
                            f"Error checking specific ToS path {path}: {str(e)}"
                        )

                # Then try common paths
                for path in common_tos_paths:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(
                            f"Checking candidate ToS URL directly: {candidate_tos_url}"
                        )

                        # Skip if this is Apple's domain - we want app-specific terms only
                        candidate_parsed = urlparse(candidate_tos_url)
                        if candidate_parsed.netloc == "www.apple.com":
                            logger.warning(
                                f"Skipping Apple's domain for candidate ToS URL: {candidate_tos_url} - we only want app-specific terms"
                            )
                            continue

                        tos_check_response = session.get(
                            candidate_tos_url, headers=headers, timeout=15
                        )
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                # Extra check: don't return Apple's general terms
                                if "apple.com/legal/terms" in candidate_tos_url:
                                    logger.warning(
                                        f"Rejecting Apple's general terms: {candidate_tos_url}"
                                    )
                                    continue

                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found directly on privacy policy domain for {app_info}",
                                    method_used="app_store_pp_domain_direct",
                                )
                    except Exception as e:
                        logger.error(f"Error checking ToS path {path}: {str(e)}")

                # If direct domain approach failed, try to get the ToS from the privacy page
                try:
                    # First try to visit the privacy page to find ToS links
                    pp_response = session.get(privacy_link, headers=headers, timeout=15)
                    pp_soup = BeautifulSoup(pp_response.text, "html.parser")

                    # Search for ToS links on the privacy page
                    tos_from_pp = find_tos_link(privacy_link, pp_soup)

                    if tos_from_pp:
                        # Skip if this is Apple's domain - we want app-specific terms only
                        tos_parsed = urlparse(tos_from_pp)
                        if tos_parsed.netloc == "www.apple.com":
                            logger.warning(
                                f"Skipping Apple's domain for ToS URL found on privacy page: {tos_from_pp} - we only want app-specific terms"
                            )
                            # We don't return anything here, let the function continue to check other methods
                        else:
                            # Extra check: don't return Apple's general terms
                            if "apple.com/legal/terms" in tos_from_pp:
                                logger.warning(
                                    f"Rejecting Apple's general terms: {tos_from_pp}"
                                )
                            else:
                                # Verify this is actually a ToS link
                                if verify_tos_link(session, tos_from_pp, headers):
                                    return TosResponse(
                                        url=url,
                                        tos_url=tos_from_pp,
                                        success=True,
                                        message=f"Terms of Service found via app's privacy policy page for {app_info}",
                                        method_used="app_store_pp_to_tos",
                                    )
                except Exception as e:
                    logger.error(f"Error fetching privacy page: {str(e)}")
        except Exception as e:
            logger.error(f"Error in App Store ToS detection: {str(e)}")

        # If we get here and haven't found app-specific terms, return failure
        logger.warning(f"No app-specific Terms of Service found for {app_info}")
        return TosResponse(
            url=url,
            success=False,
            message=f"No app-specific Terms of Service found for {app_info}. Apple's general terms will not be used as a substitute.",
            method_used="app_store_no_specific_terms",
        )

    except Exception as e:
        logger.error(f"Error in App Store ToS handler: {str(e)}")
        return TosResponse(
            url=url,
            success=False,
            message=f"Error handling App Store URL for ToS: {str(e)}",
            method_used="app_store_failed",
        )


async def handle_play_store_tos(url: str, headers: dict) -> TosResponse:
    """
    Special handling for Google Play Store URLs - first get the privacy policy link,
    then try to find the ToS link on the same domain as the privacy policy.
    """
    try:
        logger.info(f"Using specialized Play Store ToS handling for: {url}")

        # First, try to get the app name and ID for better logging
        session = requests.Session()
        app_name = None
        app_id = None

        # Parse URL to get app ID
        parsed_url = urlparse(url)
        query_params = parsed_url.query
        query_dict = {
            param.split("=")[0]: param.split("=")[1]
            for param in query_params.split("&")
            if "=" in param
        }
        app_id = query_dict.get("id")

        try:
            response = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.text, "html.parser")

            # Look for app name
            title_elem = soup.find("title")
            if title_elem:
                app_name = title_elem.text.strip().split("-")[0].strip()

            if not app_name:
                h1_elem = soup.find("h1")
                if h1_elem:
                    app_name = h1_elem.text.strip()
        except Exception as e:
            logger.error(f"Error extracting app name: {str(e)}")

        app_info = f"App {'(' + app_name + ')' if app_name else f'ID {app_id}' if app_id else ''}"

        # Step 1: Try to find the privacy policy link and use that to locate ToS
        logger.info(
            f"Looking for privacy policy link to derive ToS link for {app_info}"
        )

        # Import here to avoid circular imports
        from .privacy import find_privacy_link

        # First, we try to see if there's an app data safety page
        data_safety_url = url
        if app_id:
            data_safety_url = (
                f"https://play.google.com/store/apps/datasafety?id={app_id}"
            )

        try:
            data_safety_response = session.get(
                data_safety_url, headers=headers, timeout=15
            )
            data_safety_soup = BeautifulSoup(data_safety_response.text, "html.parser")
            privacy_link = find_privacy_link(data_safety_url, data_safety_soup)

            if not privacy_link:
                # Try the main app page
                response = session.get(url, headers=headers, timeout=15)
                soup = BeautifulSoup(response.text, "html.parser")
                privacy_link = find_privacy_link(url, soup)

            if privacy_link:
                logger.info(
                    f"Found privacy policy link for Play Store item: {privacy_link}"
                )

                # Make sure the link is absolute
                if privacy_link.startswith("/"):
                    privacy_link = urljoin(url, privacy_link)

                # Get the base domain of the privacy policy
                pp_parsed = urlparse(privacy_link)
                pp_base_domain = f"{pp_parsed.scheme}://{pp_parsed.netloc}"
                logger.info(
                    f"Extracted base domain from privacy policy: {pp_base_domain}"
                )

                # New Step: Try common ToS paths directly on the privacy policy domain first
                # This addresses the issue where we find privacy policy at example.com/privacy/
                # and want to directly check example.com/terms without visiting the privacy page first
                logger.info(
                    f"Trying common ToS paths directly on privacy policy domain: {pp_base_domain}"
                )

                # Extract privacy path components to create matching terms paths
                pp_path = pp_parsed.path
                logger.info(f"Privacy policy path: {pp_path}")

                # If the privacy URL contains specific patterns, try corresponding terms patterns
                specific_candidates = []

                if "/privacy" in pp_path:
                    # If we have /privacy, try /terms
                    terms_path = pp_path.replace("/privacy", "/terms")
                    specific_candidates.append(terms_path)

                if "/privacy-policy" in pp_path:
                    # If we have /privacy-policy, try /terms-of-service, /terms-of-use, etc.
                    specific_candidates.append(
                        pp_path.replace("/privacy-policy", "/terms-of-service")
                    )
                    specific_candidates.append(
                        pp_path.replace("/privacy-policy", "/terms-of-use")
                    )
                    specific_candidates.append(
                        pp_path.replace("/privacy-policy", "/terms-and-conditions")
                    )

                # Regular common paths
                common_tos_paths = [
                    "/terms",
                    "/tos",
                    "/terms-of-service",
                    "/terms-of-use",
                    "/terms-and-conditions",
                    "/legal/terms",
                    "/legal",
                    "/terms.html",
                    "/legal/terms.html",
                    "/eula",
                ]

                # Try specific domain-based candidates first
                for path in specific_candidates:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(
                            f"Checking candidate ToS URL based on privacy path: {candidate_tos_url}"
                        )

                        tos_check_response = session.get(
                            candidate_tos_url, headers=headers, timeout=15
                        )
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found at path matching privacy policy path for {app_info}",
                                    method_used="play_store_pp_matching_path",
                                )
                    except Exception as e:
                        logger.error(
                            f"Error checking specific ToS path {path}: {str(e)}"
                        )

                # Then try common paths
                for path in common_tos_paths:
                    try:
                        candidate_tos_url = pp_base_domain + path
                        logger.info(
                            f"Checking candidate ToS URL directly: {candidate_tos_url}"
                        )

                        tos_check_response = session.get(
                            candidate_tos_url, headers=headers, timeout=15
                        )
                        if tos_check_response.status_code == 200:
                            if verify_tos_link(session, candidate_tos_url, headers):
                                return TosResponse(
                                    url=url,
                                    tos_url=candidate_tos_url,
                                    success=True,
                                    message=f"Terms of Service found directly on privacy policy domain for {app_info}",
                                    method_used="play_store_pp_domain_direct",
                                )
                    except Exception as e:
                        logger.error(f"Error checking ToS path {path}: {str(e)}")

                # If direct domain approach failed, try to get the ToS from the privacy page
                try:
                    # First try to visit the privacy page to find ToS links
                    pp_response = session.get(privacy_link, headers=headers, timeout=15)
                    pp_soup = BeautifulSoup(pp_response.text, "html.parser")

                    # Search for ToS links on the privacy page
                    tos_from_pp = find_tos_link(privacy_link, pp_soup)

                    if tos_from_pp:
                        # Verify this is actually a ToS link
                        if verify_tos_link(session, tos_from_pp, headers):
                            return TosResponse(
                                url=url,
                                tos_url=tos_from_pp,
                                success=True,
                                message=f"Terms of Service found via app's privacy policy page for {app_info}",
                                method_used="play_store_pp_to_tos",
                            )
                except Exception as e:
                    logger.error(f"Error fetching privacy page: {str(e)}")
        except Exception as e:
            logger.error(f"Error in Play Store ToS detection: {str(e)}")

        # If we get here, we couldn't find a developer-specific ToS through the privacy policy
        # Instead of falling back to Google's standard ToS, return a failure response
        logger.warning(f"No Terms of Service found for Play Store app: {app_info}")
        return TosResponse(
            url=url,
            success=False,
            message=f"No Terms of Service found for Play Store app: {app_info}",
            method_used="play_store_no_tos_found",
        )

    except Exception as e:
        logger.error(f"Error in Play Store ToS handler: {str(e)}")
        return TosResponse(
            url=url,
            success=False,
            message=f"Error handling Play Store URL for ToS: {str(e)}",
            method_used="play_store_failed",
        )


async def playwright_tos_finder(url: str) -> TosResponse:
    """
    Find Terms of Service links using Playwright for JavaScript-rendered content.
    This is a fallback method for when the standard approach fails.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()
            page.set_default_timeout(45000)  # 45 seconds

            try:
                logger.info(f"Trying to load URL with Playwright: {url}")
                await page.goto(url, wait_until="networkidle", timeout=60000)
                final_url = page.url
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")

                # First check if we're already on a policy page
                if is_on_policy_page(final_url, "tos"):
                    logger.info(f"Already on a ToS page: {final_url}")
                    await browser.close()
                    return TosResponse(
                        url=final_url,
                        tos_url=final_url,
                        success=True,
                        message="Already on a Terms of Service page",
                        method_used="playwright_already_on_tos",
                    )

                tos_link = find_tos_link(final_url, soup)

                if not tos_link:
                    # Try to find and click buttons that might reveal ToS content
                    consent_buttons = await page.query_selector_all(
                        'button:text-matches("(accept|agree|got it|cookie|consent)", "i")'
                    )
                    for button in consent_buttons:
                        try:
                            await button.click()
                            await page.wait_for_timeout(1000)
                            content_after_click = await page.content()
                            soup_after_click = BeautifulSoup(
                                content_after_click, "html.parser"
                            )
                            tos_link = find_tos_link(final_url, soup_after_click)
                            if tos_link:
                                break
                        except:
                            continue

                    if not tos_link:
                        # Try to explicitly look for policy links with common text patterns
                        logger.info("Trying explicit link text search with Playwright")
                        policy_links = await page.query_selector_all(
                            'a:has-text("Terms"), a:has-text("Terms of Service"), a:has-text("Terms of Use"), a:has-text("Terms and Conditions"), a:has-text("Legal"), a:has-text("Policies"), a:has-text("User Agreement")'
                        )
                        for link in policy_links:
                            try:
                                href = await link.get_attribute("href")
                                if href:
                                    # Check if this is likely a ToS link
                                    href_lower = href.lower()
                                    if any(
                                        pattern in href_lower
                                        for pattern in [
                                            "/terms",
                                            "/tos",
                                            "/legal",
                                            "/policies",
                                            "terms-of-service",
                                            "terms-of-use",
                                            "terms-and-conditions",
                                        ]
                                    ):
                                        absolute_url = urljoin(final_url, href)
                                        if not is_likely_false_positive(
                                            absolute_url, "tos"
                                        ):
                                            tos_link = absolute_url
                                            logger.info(
                                                f"Found ToS link by explicit search: {tos_link}"
                                            )
                                            break
                            except Exception as e:
                                logger.error(
                                    f"Error processing link in Playwright search: {str(e)}"
                                )
                            continue

                await browser.close()

                if tos_link:
                    # Additional check for false positives
                    if is_likely_false_positive(tos_link, "tos"):
                        logger.warning(
                            f"Found link {tos_link} appears to be a false positive, skipping"
                        )
                        return TosResponse(
                            url=final_url,
                            success=False,
                            message=f"Found ToS link was a false positive: {tos_link}",
                            method_used="playwright_false_positive",
                        )

                    # Check if this is a correct policy type
                    if not is_correct_policy_type(tos_link, "tos"):
                        logger.warning(
                            f"Found link {tos_link} appears to be a privacy policy, not ToS"
                        )
                        return TosResponse(
                            url=final_url,
                            success=False,
                            message=f"Found link appears to be a privacy policy, not Terms of Service: {tos_link}",
                            method_used="playwright_wrong_policy_type",
                        )

                    # Ensure the link is absolute
                    if tos_link.startswith("/"):
                        parsed_final_url = urlparse(final_url)
                        base_url = (
                            f"{parsed_final_url.scheme}://{parsed_final_url.netloc}"
                        )
                        tos_link = urljoin(base_url, tos_link)
                        logger.info(
                            f"Converted relative URL to absolute URL: {tos_link}"
                        )

                    return TosResponse(
                        url=final_url,
                        tos_url=tos_link,
                        success=True,
                        message=f"Terms of Service link found using JavaScript-enabled browser rendering on page: {final_url}",
                        method_used="playwright",
                    )
                else:
                    return TosResponse(
                        url=final_url,
                        success=False,
                        message=f"No Terms of Service link found even with JavaScript-enabled browser rendering on page: {final_url}",
                        method_used="playwright_failed",
                    )

            except Exception as e:
                await browser.close()
                if "Timeout" in str(e) or "timeout" in str(e).lower():
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Timeout while loading page with Playwright: {url}. The site may be slow or blocking automated access.",
                        method_used="playwright_failed_timeout",
                    )
                elif "Navigation failed" in str(e) or "ERR_CONNECTION" in str(e):
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Navigation failed for {url}. The site may be unavailable or blocking automated access.",
                        method_used="playwright_failed_navigation",
                    )
                else:
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Error using Playwright to process URL {url}: {str(e)}",
                        method_used="playwright_failed",
                    )

    except Exception as e:
        error_msg = f"Error using Playwright to process URL {url}: {str(e)}"
        logger.error(error_msg)
        return TosResponse(
            url=url, success=False, message=error_msg, method_used="playwright_failed"
        )


async def try_common_tos_patterns(url: str, headers: dict) -> TosResponse:
    """
    Try common URL patterns for finding ToS links.
    This method uses a systematic approach to try common ToS URL patterns.
    """
    try:
        # Parse the URL
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        scheme = parsed_url.scheme
        base_url = f"{scheme}://{domain}"

        logger.info(f"Trying common ToS patterns for {base_url}")

        # Common ToS URL patterns to try
        common_patterns = [
            "/policies",
            "/policies?ref=pf",
            "/legal/terms",
            "/terms",
            "/terms-of-service",
            "/terms-of-use",
            "/terms-and-conditions",
            "/tos",
            "/legal",
            "/about/terms",
            "/about/legal",
            "/legal/user-agreement",
            "/user-agreement",
        ]

        async with aiohttp.ClientSession() as session:
            for pattern in common_patterns:
                try_url = urljoin(base_url, pattern)
                logger.info(f"Trying pattern: {try_url}")

                try:
                    async with session.get(
                        try_url,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status < 400:
                            logger.info(f"Successfully accessed: {try_url}")

                            # If URL contains obvious ToS indicators, accept it
                            url_lower = try_url.lower()
                            if any(
                                term in url_lower
                                for term in [
                                    "/terms",
                                    "/tos",
                                    "/policies",
                                    "/legal/terms",
                                    "terms-of-service",
                                    "terms-conditions",
                                ]
                            ):
                                logger.info(
                                    f"Found ToS URL with direct pattern match: {try_url}"
                                )
                                return TosResponse(
                                    url=url,
                                    tos_url=try_url,
                                    success=True,
                                    message=f"Terms of Service found via common pattern matching",
                                    method_used="common_pattern_match",
                                )
                except Exception as e:
                    logger.warning(f"Error trying pattern {try_url}: {str(e)}")
                    continue

    except Exception as e:
        logger.error(f"Error in common pattern search: {str(e)}")

        # If no patterns worked, return failure
        return TosResponse(
            url=url,
            success=False,
            message="No Terms of Service link found via common patterns",
            method_used="common_patterns_failed",
        )


async def scan_html_for_tos_links(url: str) -> TosResponse:
    """
    Final fallback method that downloads and scans the complete HTML for policy links
    before concluding that no ToS link exists.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            )

            page = await context.new_page()
            logger.info(f"Final fallback: Downloading full HTML from {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning(f"Error loading page in fallback method: {str(e)}")
                # Continue anyway with whatever content we have

            # Get the final URL after any redirects
            final_url = page.url

            # Get the complete HTML content
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")

            # Look for any links that might be policy-related
            all_links = soup.find_all("a", href=True)

            # Score all links for ToS likelihood
            tos_candidates = []

            for link in all_links:
                href = link.get("href", "").strip()
                if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                    continue

                absolute_url = urljoin(final_url, href)
                url_lower = absolute_url.lower()
                text = link.get_text().strip().lower()

                # Skip if clearly a privacy policy
                if "/privacy" in url_lower or "privacy policy" in text:
                    continue

                score = 0

                # Check URL for ToS indicators
                tos_url_indicators = [
                    "/terms",
                    "/tos",
                    "/terms-of-service",
                    "terms-conditions",
                    "/legal/terms",
                    "/conditions",
                    "/legal",
                    "/policies",
                ]
                for indicator in tos_url_indicators:
                    if indicator in url_lower:
                        score += 5
                        break

                # Check text for ToS indicators
                tos_text_indicators = [
                    "terms",
                    "conditions",
                    "legal",
                    "terms of service",
                    "terms of use",
                    "user agreement",
                    "policies",
                ]
                for indicator in tos_text_indicators:
                    if indicator in text:
                        score += 3
                        break

                # Check for footer placement (often indicates policy links)
                parent_footer = link.find_parent(
                    ["footer", "div"],
                    class_=lambda x: x and ("footer" in x.lower() if x else False),
                )
                if parent_footer:
                    score += 2

                if score > 0:
                    tos_candidates.append((absolute_url, score, text))

            await browser.close()

            # Sort by score and take the highest
            if tos_candidates:
                tos_candidates.sort(key=lambda x: x[1], reverse=True)
                best_tos = tos_candidates[0][0]
                best_score = tos_candidates[0][1]
                best_text = tos_candidates[0][2]

                logger.info(
                    f"Found potential ToS link through HTML scan: {best_tos} (score: {best_score}, text: {best_text})"
                )

                # Only use if score is reasonably good
                if best_score >= 5:
                    return TosResponse(
                        url=final_url,
                        tos_url=best_tos,
                        success=True,
                        message=f"Terms of Service link found through final HTML scan: {best_tos}",
                        method_used="html_scan",
                    )

            # No suitable candidates found
            return TosResponse(
                url=final_url,
                success=False,
                message="No Terms of Service link found even after scanning all HTML content",
                method_used="html_scan_failed",
            )

    except Exception as e:
        logger.error(f"Error in HTML scanning fallback: {str(e)}")
        return TosResponse(
            url=url,
            success=False,
            message=f"Error during final HTML scan: {str(e)}",
            method_used="html_scan_error",
        )


# Rest of the file stays the same
