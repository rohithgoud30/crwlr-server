from urllib.parse import urlparse, urljoin
import logging
import re
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List
import inspect

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_root_domain(domain: str) -> str:
    """
    Extract the root domain from a domain string.
    Example: 'www.example.co.uk' -> 'example.co.uk'
            'sub.example.com' -> 'example.com'
    """
    parts = domain.split('.')
    if len(parts) <= 2:
        return domain
        
    # Handle special cases like co.uk, com.au, etc.
    if parts[-2] in ['co', 'com', 'org', 'gov', 'edu'] and len(parts[-1]) == 2:
        if len(parts) > 3:
            return '.'.join(parts[-3:])
        return domain
        
    return '.'.join(parts[-2:])

def normalize_url(url: str) -> str:
    """
    Normalize URLs to ensure they're valid for request processing.
    Ensures URLs begin with http:// or https:// protocol.
    
    Returns:
        str: Normalized URL with proper protocol
    """
    url = url.strip()
    
    # Remove any trailing slashes for consistency
    url = url.rstrip('/')
    
    # Handle the "t3.chat" format with dots but no protocol
    # This is an important special case where domains may be entered without protocol
    if re.match(r'^[a-zA-Z0-9][\w\.-]+\.[a-zA-Z]{2,}$', url) and not url.startswith(('http://', 'https://')):
        url = f"https://{url}"
        logger.info(f"Added protocol to domain name: {url}")
        return url
    
    # Check if URL has protocol, if not add https://
    if not url.startswith(('http://', 'https://')):
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
        if domain.startswith('www.'):
            domain_without_www = domain[4:]
            variations.append(f"{parsed.scheme}://{domain_without_www}")
        else:
            domain_with_www = f"www.{domain}"
            variations.append(f"{parsed.scheme}://{domain_with_www}")
        
        # Try HTTP instead of HTTPS as last resort
        if parsed.scheme == 'https':
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
        if parent.name in ['header', 'nav']:
            score += 2.0
            break
            
        # Check classes and IDs for header indicators
        classes = ' '.join(parent.get('class', [])).lower()
        element_id = parent.get('id', '').lower()
        
        # Header indicators
        if any(term in classes or term in element_id for term in ['header', 'nav', 'top', 'menu']):
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
        if parent.name in ['footer', 'tfoot']:
            score += 3.0
            break
            
        # Check classes and IDs
        classes = ' '.join(parent.get('class', [])).lower()
        element_id = parent.get('id', '').lower()
        
        # Strong footer indicators
        if any(term in classes or term in element_id for term in ['footer', 'bottom', 'btm']):
            score += 3.0
            break
            
        # Secondary footer indicators
        if any(term in classes or term in element_id for term in ['legal', 'copyright', 'links']):
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
        if href_domain.endswith('.' + base_domain) or base_domain.endswith('.' + href_domain):
            return 1.5
            
        # Check if the domains share a common root
        href_parts = href_domain.split('.')
        base_parts = base_domain.split('.')
        
        if len(href_parts) >= 2 and len(base_parts) >= 2:
            href_root = '.'.join(href_parts[-2:])
            base_root = '.'.join(base_parts[-2:])
            if href_root == base_root:
                return 1.0
                
        # For external domains, check if they look like legitimate policy hosts
        if any(term in href_domain for term in ['legal', 'terms', 'privacy', 'policy']):
            return 0.5
            
        # Don't heavily penalize external domains
        return 0.0
    except Exception:
        return -1.0

def get_common_penalties() -> list:
    """Get common penalty patterns for policy links."""
    return [
        ('/blog/', -5.0),
        ('/news/', -5.0),
        ('/article/', -5.0),
        ('/press/', -5.0),
        ('/2023/', -5.0),
        ('/2024/', -5.0),
        ('/posts/', -5.0),
        ('/category/', -5.0),
        ('/tag/', -5.0),
        ('/search/', -5.0),
        ('/product/', -5.0),
        ('/services/', -5.0),
        ('/solutions/', -5.0),
        ('/ai/', -5.0),
        ('/cloud/', -5.0),
        ('/digital/', -5.0),
        ('/enterprise/', -5.0),
        ('/platform/', -5.0),
        ('/technology/', -5.0),
        ('/consulting/', -5.0)
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
        "/video/"
    ]
    
    # Check if URL contains article indicators
    for indicator in article_indicators:
        if indicator in href_lower:
            return True
    
    # Check for date patterns in URL paths
    date_pattern = re.compile(r'/\d{4}/\d{1,2}/\d{1,2}/')
    if date_pattern.search(href_lower):
        return True
    
    # Check if URL is from a known news domain
    parsed_url = urlparse(full_url)
    domain = parsed_url.netloc.lower()
    
    # Get the root domain for comparison
    root_domain = get_root_domain(domain)
    
    # Only consider it a policy link if it clearly has policy terms in the path
    if root_domain in [
        'reuters.com',
        'nytimes.com',
        'washingtonpost.com',
        'cnn.com',
        'bbc.com',
        'forbes.com',
        'bloomberg.com',
        'wsj.com',
        'ft.com',
        'economist.com'
    ]:
        # For news sites, be extra careful
        # Only consider it a policy link if it clearly has policy terms in the path
        if not any(term in parsed_url.path.lower() for term in ['/privacy', '/terms', '/tos', '/legal']):
            return True
    
    return False

def is_on_policy_page(url: str, policy_type: str) -> bool:
    """Check if we're already on a policy page."""
    base_url_path = urlparse(url).path.lower()
    
    if policy_type == 'tos':
        # Check ToS-specific terms first and ONLY
        tos_terms = ['/terms', '/tos', '/terms-of-service', '/terms-of-use', '/user-agreement']
        if any(term in base_url_path for term in tos_terms):
            return True
        # Do not check legal terms for ToS - we want to be strict
    elif policy_type == 'privacy':
        # Check privacy-specific terms first and ONLY
        privacy_terms = ['/privacy', '/privacy-policy', '/data-protection', '/gdpr', '/data-privacy']
        if any(term in base_url_path for term in privacy_terms):
            return True
        # Do not check legal terms for privacy - we want to be strict
    return False

def get_policy_patterns(policy_type: str) -> Tuple[List[str], List[str]]:
    """Get regex patterns and URL patterns for a specific policy type."""
    if policy_type == 'privacy':
        exact_patterns = [
            r'\bprivacy\s*policy\b',
            r'\bdata\s*protection\b',
            r'\bprivacy\s*notice\b',  # This will match regardless of case
            r'\bPrivacy\s*Notice\b',  # Added explicit uppercase version
            r'\bprivacy\s*statement\b',
            r'\bcookie\s*policy\b',
            r'\bcookies\s*policy\b',
            r'\bprivacy\s*preferences\b',
            r'\bgdpr\b',
            r'\bdatenschutz\b',  # German privacy
            r'\bpolitica\s*de\s*privacidad\b',  # Spanish privacy policy
            r'\bprivacidad\b'  # Spanish privacy
        ]
        strong_url_patterns = [
            'privacy.htm',
            'privacy-policy',
            'privacy_policy',
            '/privacy/',
            'privacy-notice',
            'privacy_notice',
            'datenschutz',  # German privacy
            'privacidad',  # Spanish privacy
            'cookie-policy',
            'policy-privacy',
            '/gdpr/'
        ]
    else:  # ToS
        exact_patterns = [
            r'\bterms\s*of\s*service\b', 
            r'\bterms\s*of\s*use\b',
            r'\bterms\s*&\s*conditions\b',
            r'\bterms\s*and\s*conditions\b',
            r'\bconditions\s*of\s*use\b',
            r'\bConditions\s*of\s*Use\b',  # Added explicit uppercase version
            r'\bcondition\s*of\s*use\b',  # Added singular form
            r'\bterms\s*of\s*sale\b',
            r'\blegal\s*terms\b',
            r'\bterms\b(?!\s*of\s*privacy)',  # terms but not "terms of privacy"
            r'\blegal\b(?!\s*privacy)',  # legal but not "legal privacy"
            r'\buser\s*agreement\b',
            r'\beula\b',
            r'\blicense\s*agreement\b',
            r'\bterminos\s*y\s*condiciones\b',  # Spanish terms and conditions
            r'\bterms\s*&\s*conditions\b',
            r'\bagb\b'  # German terms (Allgemeine Geschäftsbedingungen)
        ]
        strong_url_patterns = [
            'terms-of-service',
            'terms-of-use',
            'terms_of_service',
            'terms_of_use',
            'tos.htm',
            'tos.html',
            'terms.htm',
            '/terms/',
            '/tos/',
            'terms-and-conditions',
            'terms_and_conditions',
            'legal-terms',
            'conditions-of-use',
            'condition-of-use',  # Added singular form
            'eula',
            'user-agreement',
            'terminos-y-condiciones',  # Spanish terms
            'agb'  # German terms
        ]
    return exact_patterns, strong_url_patterns

def get_policy_score(text: str, url: str, policy_type: str) -> float:
    """Calculate a score for a link based on its text and URL."""
    logger.debug(f"Scoring {policy_type} candidate: {url} with text: {text}")
    
    text_lower = text.lower()
    url_lower = url.lower()
    
    score = 0.0
    
    # Apply negative score for wrong policy type
    if policy_type == 'privacy':
        # Check if this is likely a ToS URL rather than privacy
        if any(term in url_lower for term in ['/terms', 'tos.', 'termsofservice', '/tos/']):
            logger.debug(f"Penalizing for likely ToS URL patterns: {url}")
            score -= 12.0
        if any(term in text_lower for term in ['terms of service', 'terms and conditions', 'terms of use']):
            logger.debug(f"Penalizing for ToS text patterns: {text_lower}")
            score -= 10.0
    else:  # ToS
        # Check if this is likely a privacy URL rather than ToS
        if any(term in url_lower for term in ['/privacy', 'privacypolicy', '/gdpr']):
            logger.debug(f"Penalizing for likely privacy URL patterns: {url}")
            score -= 12.0
        if any(term in text_lower for term in ['privacy policy', 'privacy notice', 'data protection']):
            logger.debug(f"Penalizing for privacy text patterns: {text_lower}")
            score -= 10.0
        
        # New: Apply stronger penalties for non-ToS legal pages
        non_tos_indicators = [
            'community guidelines', 'copyright', 'dmca', 'intellectual property',
            'dsa', 'citation', 'referencing', 'attribution', 'guidelines',
            'trademark', 'takedown'
        ]
        
        for indicator in non_tos_indicators:
            if indicator in text_lower:
                score -= 20.0
                logger.debug(f"Applying strong penalty for non-ToS legal page: {indicator} in {text_lower}")
                break
                
        # New: Check URL for non-ToS indicators
        url_non_tos_indicators = [
            'community-guidelines', 'community_guidelines', 'communityguidelines',
            'copyright', 'dmca', 'ip-policy', 'dsa', 'citation'
        ]
        
        if any(indicator in url_lower for indicator in url_non_tos_indicators):
            score -= 25.0
            logger.debug(f"Applying strong URL-based penalty for non-ToS page: {url}")
    
    # Handle combined policies - less specific but still valid
    combined_patterns = [
        'legal', 'policies', 'legal notices', 'legal information'
    ]
    
    # Handle support policies - we want to avoid these
    support_patterns = [
        'help center', 'support center', 'contact', 'faq', 'customer support'
    ]
    
    # Check for combined policy hits
    combined_matches = sum(1 for pattern in combined_patterns if pattern in text_lower)
    if combined_matches > 0:
        score += (combined_matches * 1.0)
        
    # Check for support pattern hits (negative)
    support_matches = sum(1 for pattern in support_patterns if pattern in text_lower)
    if support_matches > 0:
        score -= (support_matches * 3.0)
    
    # Strong matches for privacy policy
    if policy_type == 'privacy':
        strong_privacy_matches = [
            'privacy policy', 'privacy notice', 'Privacy Notice', 
            'privacy statement', 'data protection', 'privacy', 
            'privacidad', 'datenschutz', 'gdpr', 'ccpa', 'data privacy'
        ]
        
        # Count strong privacy matches in the text
        matches = sum(1 for pattern in strong_privacy_matches if pattern in text_lower)
        score += (matches * 5.0)
        
        # Additional bonus for explicit matches
        if 'privacy policy' in text_lower or 'privacy notice' in text_lower or 'Privacy Notice' in text:
            score += 8.0
            
        if 'privacy' in text_lower and ('policy' in text_lower or 'notice' in text_lower):
            score += 4.0
    else:  # ToS
        strong_terms_matches = [
            'terms of service', 'terms of use', 'terms and conditions',
            'conditions of use', 'condition of use', 'user agreement', 
            'terms', 'tos', 'eula', 'legal terms',
            'términos', 'agb', 'nutzungsbedingungen'  # Spanish and German terms
        ]
        
        # Count strong terms matches in the text
        matches = sum(1 for pattern in strong_terms_matches if pattern in text_lower)
        score += (matches * 5.0)
        
        # New: Add MUCH stronger bonus for exact ToS matches to outweigh other legal pages
        exact_tos_matches = [
            'terms of service', 'terms of use', 'terms and conditions', 
            'user agreement', 'service agreement', 'general terms'
        ]
        
        if any(exact_match in text_lower for exact_match in exact_tos_matches):
            score += 30.0
            logger.debug(f"Applying strong bonus for exact ToS text match: {text_lower}")
        
        # Check for explicit "terms" in text and URL for maximum confidence
        if ('terms' in text_lower or 'tos' in text_lower) and ('/terms' in url_lower or '/tos' in url_lower):
            score += 20.0
            logger.debug(f"Applying strong bonus for 'terms' in both text and URL: {text_lower}, {url_lower}")
            
        # Additional bonus for explicit matches
        if any(exact in text_lower for exact in ['terms of service', 'terms of use', 'terms and conditions', 'conditions of use']):
            score += 8.0
    
    # Technical documentation penalties
    tech_doc_patterns = [
        'api documentation', 'developer', 'technical documentation', 
        'sdk', 'integration', 'api terms'
    ]
    
    # Penalize technical documentation links for ToS search
    if policy_type == 'tos':
        tech_matches = sum(1 for pattern in tech_doc_patterns if pattern in text_lower)
        if tech_matches > 0:
            score -= (tech_matches * 4.0)
    
    # URL-based scoring
    if policy_type == 'privacy':
        url_patterns = ['/privacy', 'privacy-policy', 'privacy_policy', 'privacypolicy', 'datenschutz']
        url_matches = sum(1 for pattern in url_patterns if pattern in url_lower)
        score += (url_matches * 3.0)
    else:  # ToS
        url_patterns = ['/terms', '/tos', 'terms-of-service', 'terms-of-use', 'termsofservice', 
                      'conditions-of-use', 'condition-of-use']
        url_matches = sum(1 for pattern in url_patterns if pattern in url_lower)
        score += (url_matches * 3.0)
        
        # New: Give much stronger boost to URLs that are most likely ToS
        strong_tos_urls = [
            '/terms-of-service', '/terms-of-use', '/terms-and-conditions',
            '/tos.html', '/terms.html', '/terms/', '/tos/', '/general-terms/'
        ]
        if any(pattern in url_lower for pattern in strong_tos_urls):
            score += 25.0
            logger.debug(f"Applying strong bonus for clear ToS URL pattern: {url_lower}")
    
    # Additional URL pattern scoring
    if re.search(r'/(?:legal|policies)/(?:privacy|data)', url_lower):
        if policy_type == 'privacy':
            score += 4.0
        else:
            score -= 2.0  # Penalty if looking for ToS
            
    if re.search(r'/(?:legal|policies)/(?:terms|tos)', url_lower):
        if policy_type == 'tos':
            score += 4.0
        else:
            score -= 2.0  # Penalty if looking for privacy
    
    # Exact filename matches
    privacy_filenames = ['privacy.html', 'privacy.php', 'privacy.htm', 'privacy.aspx', 'privacy']
    tos_filenames = ['terms.html', 'tos.html', 'terms.php', 'terms.htm', 'terms.aspx', 'tos', 'terms']
    
    if policy_type == 'privacy' and any(url_lower.endswith(fname) for fname in privacy_filenames):
        score += 5.0
    elif policy_type == 'tos' and any(url_lower.endswith(fname) for fname in tos_filenames):
        score += 5.0
        
    logger.debug(f"Final score for {policy_type} candidate: {url} = {score}")
    return score

def is_likely_false_positive(url: str, policy_type: str) -> bool:
    """
    Check if a URL is likely to be a false positive (not actually a policy page).
    More flexible with different naming conventions.
    """
    url_lower = url.lower()
    
    # Common false positives for both policy types
    common_false_positives = [
        'twitter.com', 'facebook.com', 'instagram.com', 'linkedin.com',
        'youtube.com', 'accounts.google.com', 'plus.google.com',
        'pinterest.com', 'snapchat.com', 'apple.com/app-store', 'play.google.com'
    ]
    
    for domain in common_false_positives:
        if domain in url_lower:
            return True
    
    # Strong indicators of non-ToS pages that should be rejected
    if policy_type == 'tos':
        non_tos_indicators = [
            'community-guidelines', 'community_guidelines', 'communityguidelines',
            'copyright', 'dmca', 'intellectual-property', 'ip-policy',
            'dsa', 'citation', 'referencing', 'attribution',
            'right', '/faq/', '/help/', '/support/'
        ]
        
        if any(indicator in url_lower for indicator in non_tos_indicators) and not (
            '/terms' in url_lower or 
            '/tos' in url_lower or 
            'terms-of-service' in url_lower or 
            'terms-of-use' in url_lower
        ):
            # If the URL contains one of these indicators but doesn't also have a strong
            # ToS term, consider it a false positive
            return True
    
    # Check for "support policy" in privacy detection - this is a false positive
    # But only if it doesn't also mention privacy or data
    if policy_type == 'privacy':
        if '/support-policy' in url_lower and 'privacy' not in url_lower:
            # Check if it might be a valid privacy policy despite having "support" in the name
            # For example, some sites might have "Support & Privacy Policy"
            if any(term in url_lower for term in ['/data', '/personal', '/gdpr', '/private']):
                return False  # It might be a valid privacy policy despite having "support" in the name
            return True  # Likely a support policy, not a privacy policy
            
    # Check for documentation links - these are often false positives for ToS
    if policy_type == 'tos' and ('/docs' in url_lower or '/documentation' in url_lower or '/doc/' in url_lower or url_lower.endswith('/doc')):
        # Make sure it's not actually ToS in documentation section
        if not any(term in url_lower for term in ['/terms', '/tos', '/legal', '/eula', '/conditions']):
            return True
    
    # These file types are rarely policy pages
    file_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.pdf', '.zip', '.mp4', '.mp3', '.exe']
    for ext in file_extensions:
        if url_lower.endswith(ext):
            return True
            
    # Blog posts and news articles are rarely policy pages
    blog_patterns = ['/blog/', '/news/', '/article/', '/post/', '/press-release/']
    if any(pattern in url_lower for pattern in blog_patterns):
        # Unless they explicitly mention policies in the URL
        if policy_type == 'privacy' and not any(term in url_lower for term in ['/privacy', '/data-policy']):
            return True
        if policy_type == 'tos' and not any(term in url_lower for term in ['/terms', '/tos', '/conditions']):
            return True
    
    return False

def is_correct_policy_type(url: str, policy_type: str) -> bool:
    """Check if a URL is likely to be the correct policy type based on URL patterns."""
    # Case insensitive check
    url = url.lower()
    
    # Define strong indicators for privacy policies
    privacy_indicators = [
        '/privacy', 'privacy-policy', 'privacy_policy', 'privacypolicy', 'privacy.htm',
        'privacy-notice', 'privacy_notice', 'PrivacyNotice', 'privacy-statement',
        '/datenschutz', 'privacidad', '/gdpr', 'data-protection', 'data_protection',
        'privacy-notice', 'privacy_notice', 'privacy-statement', 'privacy_statement'
    ]
    
    # Define strong indicators for terms of service
    terms_indicators = [
        '/terms', '/tos', 'terms-of-service', 'terms_of_service', 'termsofservice',
        'terms-of-use', 'terms_of_use', 'termsofuse', 'terms-and-conditions',
        'terms_and_conditions', 'termsandconditions', 'terms.html', 'tos.html',
        'eula', 'conditions-of-use', 'condition-of-use', 'user-agreement'
    ]
    
    # Special case: Combined "legal" pages might contain both
    if '/legal' in url or '/policies' in url:
        # Check for explicit indicators in the URL
        if policy_type == 'privacy':
            return not any(ti in url for ti in terms_indicators) or any(pi in url for pi in privacy_indicators)
        else:  # ToS
            return not any(pi in url for pi in privacy_indicators) or any(ti in url for ti in terms_indicators)
    
    # Special case: Documentation might use "terms" in a different context
    if policy_type == 'tos' and any(doc in url for doc in ['/documentation', '/docs/', '/api/', '/doc/']):
        # Ensure it's explicitly a terms page, not just documentation that happens to use the word "terms"
        if not any(ti in url for ti in ['/terms/', '/tos/', '/terms-of-', 'terms.html', 'tos.html', 'eula', 'conditions-of-use']):
            return False
    
    # Regular URL pattern matching
    if policy_type == 'privacy':
        # Strong check: Terms URLs should never be classified as privacy
        if any(ti in url for ti in terms_indicators):
            return False
        # Check if it matches any privacy patterns
        return any(pi in url for pi in privacy_indicators)
    else:  # ToS
        # Strong check: Privacy URLs should never be classified as terms
        if any(pi in url for pi in privacy_indicators):
            return False
        # Check if it matches any terms patterns
        return any(ti in url for ti in terms_indicators)
    
    # Conservative default: if we can't determine the type, consider it a mismatch
    return False

def find_policy_by_class_id(soup: BeautifulSoup, policy_type: str = 'privacy') -> Optional[str]:
    """
    Find policy links by looking for specific class and ID attributes commonly used for links.
    This gives higher priority to specific implementation patterns commonly used.
    """
    candidates = []
    
    # Define terms to search for based on policy type
    if policy_type == 'privacy':
        keywords = ['privacy', 'privacy policy', 'data protection', 'privacy notice', 'Privacy Notice', 'datenschutz']
    else:  # Terms of service
        keywords = ['terms', 'terms of service', 'terms of use', 'terms and conditions', 'legal', 'tos', 'conditions of use', 'eula']

    # Look for footer tags
    footer_elements = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower() if c else False))
    footer_elements += soup.find_all(['footer', 'div'], id=lambda i: i and ('footer' in i.lower() if i else False))
    
    # Check links in footer first (these are most reliable)
    for footer in footer_elements:
        for link in footer.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue

            # Get link text and look for policy keywords
            link_text = link.get_text().lower().strip()
            
            for keyword in keywords:
                if keyword.lower() in link_text:
                    candidates.append((href, 10, "footer_text_match"))  # Higher score for footer links
                    break
                    
            # Also check href for policy keywords
            href_lower = href.lower()
            if policy_type == 'privacy':
                if any(term in href_lower for term in ['/privacy', 'privacy-policy', 'privacy_policy', 'privacypolicy', 'privacy.html']):
                    candidates.append((href, 8, "footer_href_match"))
            else:  # ToS
                if any(term in href_lower for term in ['/terms', '/tos', 'terms-of-service', 'terms.html', 'legal-terms']):
                    candidates.append((href, 8, "footer_href_match"))

    # Find elements with common class/id patterns for policies
    policy_class_patterns = ['privacy-link', 'privacy_link', 'privacyLink', 'privacy-policy', 
                           'terms-link', 'terms_link', 'termsLink', 'terms-of-service',
                           'legal-link', 'legal_link', 'legalLink']
    
    for pattern in policy_class_patterns:
        # Check for elements with matching class
        for element in soup.find_all(class_=lambda c: c and pattern.lower() in c.lower() if c else False):
            # If it's a link itself
            if element.name == 'a' and element.has_attr('href'):
                href = element.get('href', '').strip()
                if href and not href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                    candidates.append((href, 9, "policy_class"))
            # If it contains links
            else:
                for link in element.find_all('a', href=True):
                    href = link.get('href', '').strip()
                    if href and not href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                        candidates.append((href, 7, "policy_container_class"))

    # If no candidates found, look for all links with policy-related text
    if not candidates:
        for link in soup.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
                
            link_text = link.get_text().lower().strip()
            
            # Check for policy keywords in link text
            for keyword in keywords:
                if keyword.lower() in link_text:
                    candidates.append((href, 5, "text_match"))
                    break

    # Sort candidates by score
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    if candidates:
        # Return the highest-scoring candidate
        return candidates[0][0]
    
    return None

def find_policy_link_prioritized(url: str, soup: BeautifulSoup, policy_type: str = 'tos') -> Optional[str]:
    """
    Find policy links (ToS or Privacy Policy) in a prioritized order:
    1. Check specific class/ID patterns first
    2. Check footer elements
    3. Check header elements
    4. Check all links with policy-related text
    
    Args:
        url: The URL of the page being searched
        soup: BeautifulSoup object of the page
        policy_type: Either 'tos' or 'privacy'
        
    Returns:
        URL of the policy page if found, None otherwise
    """
    logger.info(f"Searching for {policy_type} link using prioritized approach")
    
    # Store information about how the link was found in the frame locals
    # This will be used by the calling functions to determine method_used
    method_info = ""
    
    # 1. First try the class/ID based approach (highest priority)
    class_id_result = find_policy_by_class_id(soup, policy_type)
    if class_id_result:
        logger.info(f"Found {policy_type} link by class/ID: {class_id_result}")
        method_info = "found_by_class_ID"
        # Store method info in the caller's frame locals
        try:
            inspect.currentframe().f_back.f_locals['method_info'] = method_info
        except:
            pass
        return class_id_result
    
    base_domain = urlparse(url).netloc.lower()
    is_policy_page = is_on_policy_page(url, policy_type)
    exact_patterns, strong_url_patterns = get_policy_patterns(policy_type)
    
    # Get current domain info for domain-matching rules
    current_domain = urlparse(url).netloc.lower()
    
    # 2. Check footer elements second (very common location)
    footer_candidates = []
    footer_elements = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower() if c else False))
    footer_elements += soup.find_all(['footer', 'div'], id=lambda i: i and ('footer' in i.lower() if i else False))
    
    for footer in footer_elements:
        for link in footer.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
                
            try:
                absolute_url = urljoin(url, href)
                
                # Skip likely false positives
                if is_likely_false_positive(absolute_url, policy_type):
                    continue
                
                # Check if this is correct policy type
                if not is_correct_policy_type(absolute_url, policy_type):
                    continue
                
                link_text = ' '.join([
                    link.get_text().strip(),
                    link.get('title', '').strip(),
                    absolute_url
                ]).lower()
                
                score = get_policy_score(link_text, absolute_url, policy_type)
                if score <= 0:
                    continue
                
                domain_score = get_domain_score(absolute_url, base_domain)
                if domain_score < 0:
                    continue
                
                # Generic scoring adjustments for ToS links
                if policy_type == 'tos':
                    # Penalize links that are known to be not ToS related
                    if any(term in absolute_url.lower() or term in link_text.lower() for term in 
                           ['community-guidelines', 'copyright', 'dsa', 'right', 'citation']):
                        score -= 15.0
                        logger.info(f"Applying penalty to link that appears to be non-ToS content: {absolute_url}")
                    
                    # Boost actual terms links without domain-specific checks
                    if ('terms' in link_text.lower() or 'terms' in absolute_url.lower()):
                        score += 10.0
                        logger.info(f"Boosting score for link that explicitly mentions terms: {absolute_url}")
                        
                        # Additional boost for stronger terms indicators in link text or URL
                        if any(indicator in link_text.lower() or indicator in absolute_url.lower() for indicator in 
                              ['terms of service', 'terms-of-service', 'terms and conditions', 'terms-and-conditions']):
                            score += 5.0
                
                final_score = (score * 2.0) + (3.0 * 3.0) + (domain_score * 1.0)  # Higher footer score
                
                if final_score > 5.0:  # Lower threshold for footer
                    footer_candidates.append((absolute_url, final_score))
            except Exception as e:
                logger.error(f"Error processing footer link: {e}")
                continue
    
    if footer_candidates:
        footer_candidates.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"Found {policy_type} link in footer: {footer_candidates[0][0]}")
        method_info = "found_in_footer"
        # Store method info in the caller's frame locals
        try:
            inspect.currentframe().f_back.f_locals['method_info'] = method_info
        except:
            pass
        return footer_candidates[0][0]
    
    # 3. Check header elements third
    header_candidates = []
    header_elements = soup.find_all(['header', 'nav', 'div'], class_=lambda c: c and any(term in c.lower() for term in ['header', 'nav', 'menu', 'top']) if c else False)
    header_elements += soup.find_all(['header', 'nav', 'div'], id=lambda i: i and any(term in i.lower() for term in ['header', 'nav', 'menu', 'top']) if i else False)
    
    for header in header_elements:
        for link in header.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
                
            try:
                absolute_url = urljoin(url, href)
                
                # Skip likely false positives
                if is_likely_false_positive(absolute_url, policy_type):
                    continue
                
                # Check if this is correct policy type
                if not is_correct_policy_type(absolute_url, policy_type):
                    continue
                
                link_text = ' '.join([
                    link.get_text().strip(),
                    link.get('title', '').strip(),
                    absolute_url
                ]).lower()
                
                score = get_policy_score(link_text, absolute_url, policy_type)
                if score <= 0:
                    continue
                
                domain_score = get_domain_score(absolute_url, base_domain)
                if domain_score < 0:
                    continue
                
                # Apply the same generic scoring adjustments
                if policy_type == 'tos':
                    if any(term in absolute_url.lower() or term in link_text.lower() for term in 
                           ['community-guidelines', 'copyright', 'dsa', 'right', 'citation']):
                        score -= 15.0
                    if ('terms' in link_text.lower() or 'terms' in absolute_url.lower()):
                        score += 10.0
                        # Additional boost for stronger terms indicators
                        if any(indicator in link_text.lower() or indicator in absolute_url.lower() for indicator in 
                              ['terms of service', 'terms-of-service', 'terms and conditions', 'terms-and-conditions']):
                            score += 5.0
                
                final_score = (score * 2.0) + (2.0 * 3.0) + (domain_score * 1.0)  # Slightly lower than footer
                
                if final_score > 6.0:  # Higher threshold for header
                    header_candidates.append((absolute_url, final_score))
            except Exception as e:
                logger.error(f"Error processing header link: {e}")
                continue
    
    if header_candidates:
        header_candidates.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"Found {policy_type} link in header: {header_candidates[0][0]}")
        method_info = "found_in_header"
        # Store method info in the caller's frame locals
        try:
            inspect.currentframe().f_back.f_locals['method_info'] = method_info
        except:
            pass
        return header_candidates[0][0]
    
    # 4. Finally, check all links with policy-related text
    all_candidates = []
    for link in soup.find_all('a', href=True):
        href = link.get('href', '').strip()
        if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
            continue
            
        try:
            absolute_url = urljoin(url, href)
            
            # Skip likely false positives
            if is_likely_false_positive(absolute_url, policy_type):
                continue
            
            # Check if this is a different domain from the original site
            target_domain = urlparse(absolute_url).netloc.lower()
            base_domain = get_root_domain(current_domain)
            target_base_domain = get_root_domain(target_domain)
            
            # For cross-domain links, apply strict checks
            if target_base_domain != base_domain:
                if policy_type == 'tos' and not any(term in absolute_url.lower() for term in ['/terms', '/tos', 'terms-of-service']):
                    continue
                elif policy_type == 'privacy' and not any(term in absolute_url.lower() for term in ['/privacy', 'privacy-policy']):
                    continue
            
            # Ensure this is the correct policy type
            if not is_correct_policy_type(absolute_url, policy_type):
                continue
            
            link_text = ' '.join([
                link.get_text().strip(),
                link.get('title', '').strip(),
                absolute_url
            ]).lower()
            
            score = get_policy_score(link_text, absolute_url, policy_type)
            
            if score <= 0:
                continue
            
            domain_score = get_domain_score(absolute_url, base_domain)
            
            if domain_score < 0:
                continue
            
            href_domain = urlparse(absolute_url).netloc.lower()
            
            # Domain-specific scoring adjustments
            if href_domain == current_domain:
                # Strongly prefer same-domain links
                score += 15.0
            elif target_base_domain != base_domain:
                # Apply penalty for cross-domain links
                score -= 8.0
                
            if is_policy_page and href_domain != base_domain:
                continue
            
            if any(re.search(pattern, link_text) for pattern in exact_patterns):
                score += 6.0
            
            href_lower = absolute_url.lower()
            if any(pattern in href_lower for pattern in strong_url_patterns):
                score += 4.0
            
            for pattern, penalty in get_common_penalties():
                if pattern in href_lower:
                    score += penalty
            
            # Apply the same generic scoring adjustments
            if policy_type == 'tos':
                if any(term in absolute_url.lower() or term in link_text.lower() for term in 
                       ['community-guidelines', 'copyright', 'dsa', 'right', 'citation']):
                    score -= 15.0
                if ('terms' in link_text.lower() or 'terms' in absolute_url.lower()):
                    score += 10.0
                    # Additional boost for stronger terms indicators
                    if any(indicator in link_text.lower() or indicator in absolute_url.lower() for indicator in 
                          ['terms of service', 'terms-of-service', 'terms and conditions', 'terms-and-conditions']):
                        score += 5.0
            
            final_score = (score * 2.0) + (get_footer_score(link) * 3.0) + (domain_score * 1.0)
            
            threshold = 7.0  # Higher threshold for general links
            
            if final_score > threshold:
                all_candidates.append((absolute_url, final_score))
        
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            continue
    
    if all_candidates:
        all_candidates.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"Found {policy_type} link in general scan: {all_candidates[0][0]}")
        method_info = "found_in_general_scan"
        # Store method info in the caller's frame locals
        try:
            inspect.currentframe().f_back.f_locals['method_info'] = method_info
        except:
            pass
        return all_candidates[0][0]
    
    logger.info(f"No {policy_type} link found using any method")
    method_info = "not_found"
    # Store method info in the caller's frame locals
    try:
        inspect.currentframe().f_back.f_locals['method_info'] = method_info
    except:
        pass
    return None