from urllib.parse import urlparse, urljoin
import logging
import re
from bs4 import BeautifulSoup
from typing import Optional, Tuple, List

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
    Check if a URL is likely to be a false positive (not actually a policy page)
    using contextual analysis instead of hardcoded domains.
    
    Optimized version with early returns for better performance.
    """
    # Fast initial check for common policy paths - these should never be false positives
    url_lower = url.lower()
    
    # Fastest path: Direct word match in URL path for obvious policy pages
    if '/privacy' in url_lower or '/terms' in url_lower or '/tos' in url_lower or '/policy' in url_lower:
        return False
        
    # Parse the URL only if we need more analysis (avoid parsing in common cases)
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    
    # Check for policy indicators in the query (only if we need to parse deeper)
    query = parsed_url.query.lower()
    if 'privacy' in query or 'policy' in query:
        if policy_type == 'privacy':
            return False
    
    if 'terms' in query or 'tos' in query:
        if policy_type == 'tos':
            return False
            
    # Additional policy indicators in path (less common cases)
    policy_indicators = ['/policies', '/legal', '/gdpr', '/data-protection']
    if any(indicator in path for indicator in policy_indicators):
        return False
    
    # Fast path for media files (almost always false positives)
    file_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.zip', '.mp4', '.mp3', '.exe']
    if any(url_lower.endswith(ext) for ext in file_extensions):
        return True
    
    # Social media paths (common false positives)
    social_media_paths = [
        '/share', '/status/', '/post/', '/photo/', '/video/',
        '/profile/', '/user/', '/account/', '/page/', '/channel/'
    ]
    
    if any(pattern in path for pattern in social_media_paths):
        # Unless it explicitly has policy in the path
        if 'privacy' not in path and 'policy' not in path and 'terms' not in path and 'legal' not in path:
            return True
    
    # App store links that aren't privacy-related
    if 'play.google.com' in url_lower and 'store/apps' in url_lower:
        # Only accept privacy-specific app store links
        if not ('privacy' in url_lower or 'policy=' in url_lower or 'privacy=' in url_lower):
            return True
    
    if 'apps.apple.com' in url_lower or 'itunes.apple.com' in url_lower:
        # Only accept privacy-specific app store links
        if not ('privacy' in url_lower or 'policy=' in url_lower or 'privacy=' in url_lower):
            return True
    
    # Support pages mistaken for policies
    if policy_type == 'privacy' and '/support-policy' in path and 'privacy' not in path:
        # Support policies that don't mention privacy are false positives
        if not any(term in url_lower for term in ['/data', '/personal', '/gdpr', '/private']):
            return True
    
    # Documentation mistaken for ToS
    if policy_type == 'tos' and ('/docs' in path or '/documentation' in path or '/doc/' in path):
        # Only if it doesn't explicitly mention terms
        if not any(term in path for term in ['/terms', '/tos', '/legal', '/eula', '/conditions']):
            return True
    
    # Special case for PDFs - they might be legitimate policy documents
    if url_lower.endswith('.pdf'):
        # If the PDF filename contains policy terms, it's likely legitimate
        filename = path.split('/')[-1].lower()
        policy_terms = ['privacy', 'policy', 'terms', 'tos', 'legal', 'data']
        if not any(term in filename for term in policy_terms):
            return True
    
    # Blog posts and news articles are rarely policy pages
    blog_patterns = ['/blog/', '/news/', '/article/', '/post/', '/press-release/']
    if any(pattern in path for pattern in blog_patterns):
        # Unless they explicitly mention policies in the URL
        if policy_type == 'privacy' and not any(term in path for term in ['/privacy', '/data-policy']):
            return True
        if policy_type == 'tos' and not any(term in path for term in ['/terms', '/tos', '/conditions']):
            return True
    
    # Date patterns in URL typically indicate news/blog content, not policies
    if re.search(r'/\d{4}/\d{1,2}(/\d{1,2})?/', path):
        # Unless they explicitly mention policies
        if not any(term in path for term in policy_indicators + ['/privacy', '/terms', '/tos']):
            return True
    
    return False

def is_correct_policy_type(url: str, policy_type: str) -> bool:
    """Check if a URL is likely to be the correct policy type based on URL patterns."""
    # Case insensitive check
    url = url.lower()
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    query = parsed_url.query.lower()
    
    # Define strong indicators for privacy policies
    privacy_indicators = [
        '/privacy', 'privacy-policy', 'privacy_policy', 'privacypolicy', 'privacy.htm',
        'privacy-notice', 'privacy_notice', 'PrivacyNotice', 'privacy-statement',
        '/datenschutz', 'privacidad', '/gdpr', 'data-protection', 'data_protection',
        'privacy-notice', 'privacy_notice', 'privacy-statement', 'privacy_statement'
    ]
    
    # Additional path indicators for privacy that are sometimes embedded
    additional_privacy_path_indicators = [
        'ref=footer_privacy', 'ref=privacy', 'privacy_statement', 'privacy-policy'
    ]
    
    # Define strong indicators for terms of service
    terms_indicators = [
        '/terms', '/tos', 'terms-of-service', 'terms_of_service', 'termsofservice',
        'terms-of-use', 'terms_of_use', 'termsofuse', 'terms-and-conditions',
        'terms_and_conditions', 'termsandconditions', 'terms.html', 'tos.html',
        'eula', 'conditions-of-use', 'condition-of-use', 'user-agreement'
    ]
    
    # Additional path indicators for ToS that are sometimes embedded
    additional_terms_path_indicators = [
        'ref=footer_conditions', 'ref=footer_cou', 'ref=terms', 'ref=footer_terms', 
        'conditions-of-use', 'user-agreement', 'legal-terms'
    ]
    
    # Check for privacy-specific or terms-specific query parameters
    if policy_type == 'privacy':
        # Check for privacy references in query string
        if any(term in query for term in ['privacy', 'datenschutz', 'gdpr', 'ccpa']):
            return True
        # Check for node IDs that are known to be privacy policies
        if 'nodeid=468496' in query:  # Amazon's privacy policy node ID
            return True
    else:  # Terms of Service
        # Check for terms references in query string
        if any(term in query for term in ['terms', 'tos', 'conditions', 'eula']):
            return True
        # Check for node IDs that are known to be terms pages
        if 'nodeid=508088' in query:  # Amazon's conditions of use node ID
            return True
    
    # Check for additional path indicators embedded in the path
    if policy_type == 'privacy':
        if any(indicator in path for indicator in additional_privacy_path_indicators):
            return True
    else:  # Terms of Service
        if any(indicator in path for indicator in additional_terms_path_indicators):
            return True
    
    # Special case: Combined "legal" pages might contain both
    if '/legal' in url or '/policies' in url:
        # Check for explicit indicators in the URL
        if policy_type == 'privacy':
            return not any(ti in url for ti in terms_indicators) or any(pi in url for pi in privacy_indicators)
        else:  # ToS
            return not any(pi in url for pi in privacy_indicators) or any(ti in url for ti in terms_indicators)
    
    # Special case: Help center pages are common for policy documents
    if '/help/' in path or '/customer/display' in path:
        # For help pages, check if there are clear indicators in the path or query
        if policy_type == 'privacy':
            if 'privacy' in path or 'privacy' in query:
                return True
            # Check for common help center patterns for privacy
            if 'data' in query or 'data-protection' in path:
                return True
        else:  # ToS
            if 'terms' in path or 'tos' in path or 'terms' in query or 'tos' in query:
                return True
            # Check for common help center patterns for terms
            if 'conditions' in path or 'conditions' in query or 'cou' in query:
                return True
    
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

def find_policy_by_class_id(soup: BeautifulSoup, policy_type: str = 'privacy', base_url: str = "") -> Optional[str]:
    """
    Find policy links by looking for specific class, ID attributes, and common structural elements.
    Prioritizes links found in footers, navigations, and specific sections.
    Returns an absolute URL if found.
    
    Optimized version with early returns and cached operations for better performance.
    """
    # Define terms to search for based on policy type (cached at function level)
    if policy_type == 'privacy':
        keywords = ['privacy', 'privacy policy', 'data protection', 'privacy notice', 'Privacy Notice', 'datenschutz']
        url_keywords = ['/privacy', 'privacy-policy', 'privacy_policy', 'privacypolicy', 'privacy.html']
    else:  # Terms of service
        keywords = ['terms', 'terms of service', 'terms of use', 'terms and conditions', 'legal', 'tos', 'conditions of use', 'eula']
        url_keywords = ['/terms', '/tos', 'terms-of-service', 'terms.html', 'legal-terms', 'eula']

    # Cache for found candidates to avoid duplicate processing
    candidates = []
    existing_urls = set()

    # Fast path: Check footer links first (highest probability area)
    footer_elements = soup.find_all(['footer'])
    if not footer_elements:  # Try by class/id if no footer tag
        footer_elements = soup.find_all(['div'], class_=lambda c: c and ('footer' in c.lower() if c else False))
        footer_elements += soup.find_all(['div'], id=lambda i: i and ('footer' in i.lower() if i else False))
        footer_elements += soup.find_all(role='contentinfo')  # Standard footer role
    
    # Process footer links with high priority
    for element in footer_elements:
        for link in element.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue

            # Make URL absolute
            absolute_url = urljoin(base_url, href)
            if absolute_url in existing_urls:
                continue
                
            existing_urls.add(absolute_url)
            
            # Fast check: direct keyword match in link text
            link_text = link.get_text().lower().strip()
            
            # Fast path for very clear matches in footers (early return for obvious matches)
            for keyword in keywords:
                if keyword.lower() in link_text:
                    # Skip validation for obvious matches in footer
                    if not is_likely_false_positive(absolute_url, policy_type) and is_correct_policy_type(absolute_url, policy_type):
                        logger.info(f"Found best candidate for {policy_type} via footer_text_match: {absolute_url} (Score: 12)")
                        return absolute_url
            
            # Check URL keywords for footer links
            href_lower = absolute_url.lower()
            for url_keyword in url_keywords:
                if url_keyword in href_lower:
                    candidates.append((absolute_url, 10, "footer_href_match"))
                    break
    
    # If we have high-confidence footer candidates, return the best one
    footer_candidates = [c for c in candidates if c[2].startswith("footer_") and c[1] >= 10]
    if footer_candidates:
        # Skip validation for high-confidence footer matches
        best_candidate = max(footer_candidates, key=lambda x: x[1])
        if not is_likely_false_positive(best_candidate[0], policy_type) and is_correct_policy_type(best_candidate[0], policy_type):
            logger.info(f"Found best candidate for {policy_type} via {best_candidate[2]}: {best_candidate[0]} (Score: {best_candidate[1]})")
            return best_candidate[0]

    # Continue with navigation elements (more focused search)
    nav_elements = soup.find_all(['nav'])
    nav_elements += soup.find_all(role='navigation')
    
    for element in nav_elements:
        for link in element.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue

            absolute_url = urljoin(base_url, href)
            if absolute_url in existing_urls:
                continue
                
            existing_urls.add(absolute_url)

            # Fast check for navigation links
            link_text = link.get_text().lower().strip()
            for keyword in keywords:
                if keyword.lower() in link_text:
                    candidates.append((absolute_url, 8 + 2, "nav_text_match"))
                    break  # Found a match, move to next link
                
            # Check URL keywords
            href_lower = absolute_url.lower()
            for url_keyword in url_keywords:
                if url_keyword in href_lower:
                    candidates.append((absolute_url, 8, "nav_href_match"))
                    break
    
    # If we have strong navigation candidates, check them now
    nav_candidates = [c for c in candidates if c[2].startswith("nav_") and c[1] >= 8]
    if nav_candidates:
        best_candidate = max(nav_candidates, key=lambda x: x[1])
        if not is_likely_false_positive(best_candidate[0], policy_type) and is_correct_policy_type(best_candidate[0], policy_type):
            logger.info(f"Found best candidate for {policy_type} via {best_candidate[2]}: {best_candidate[0]} (Score: {best_candidate[1]})")
            return best_candidate[0]

    # Continue with common legal/policy sections - but only if we haven't found strong candidates yet
    common_section_selectors = [
        {'class': lambda c: c and any(s in c.lower() for s in ['legal', 'policies', 'company', 'about']) if c else False},
        {'id': lambda i: i and any(s in i.lower() for s in ['legal', 'policies', 'company', 'about']) if i else False}
    ]
    
    for selector in common_section_selectors:
        section_elements = soup.find_all(['div', 'section'], **selector)
        for element in section_elements:
            for link in element.find_all('a', href=True):
                href = link.get('href', '').strip()
                if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                    continue

                absolute_url = urljoin(base_url, href)
                if absolute_url in existing_urls:
                    continue
                    
                existing_urls.add(absolute_url)

                link_text = link.get_text().lower().strip()
                for keyword in keywords:
                    if keyword.lower() in link_text:
                        candidates.append((absolute_url, 6 + 2, "section_text_match"))
                        break
                
                href_lower = absolute_url.lower()
                for url_keyword in url_keywords:
                    if url_keyword in href_lower:
                        candidates.append((absolute_url, 6, "section_href_match"))
                        break

    # Check for common policy class/id patterns - but only process new links
    policy_class_patterns = ['privacy-link', 'privacy_link', 'privacyLink', 'privacy-policy', 
                          'terms-link', 'terms_link', 'termsLink', 'terms-of-service',
                          'legal-link', 'legal_link', 'legalLink']
    
    for pattern in policy_class_patterns:
        for element in soup.find_all(class_=lambda c: c and pattern.lower() in c.lower() if c else False):
            # If it's a link itself
            if element.name == 'a' and element.has_attr('href'):
                href = element.get('href', '').strip()
                if href and not href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                    absolute_url = urljoin(base_url, href)
                    if absolute_url not in existing_urls:
                        candidates.append((absolute_url, 5, "policy_class"))
                        existing_urls.add(absolute_url)
            # If it contains links
            else:
                for link in element.find_all('a', href=True):
                    href = link.get('href', '').strip()
                    if href and not href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                        absolute_url = urljoin(base_url, href)
                        if absolute_url not in existing_urls:
                            candidates.append((absolute_url, 4, "policy_container_class"))
                            existing_urls.add(absolute_url)

    # Only run fallback all-links search if we don't have good candidates yet
    if not candidates or max(c[1] for c in candidates) < 5:
        # Limited fallback - only look at links with specific text patterns
        # This is much faster than searching all links
        for link in soup.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
                
            # Skip checking links we've already processed
            absolute_url = urljoin(base_url, href)
            if absolute_url in existing_urls:
                continue
                
            link_text = link.get_text().lower().strip()
            # Only consider links with policy-related text (much faster than checking all links)
            if any(keyword.lower() in link_text for keyword in keywords):
                candidates.append((absolute_url, 3, "text_match"))
                existing_urls.add(absolute_url)

    # Filter and sort candidates - focus on the most promising ones first
    filtered_candidates = []
    for url_cand, score, reason in candidates:
        # Skip likely false positives first - only need to check highest-scoring candidates
        if is_likely_false_positive(url_cand, policy_type):
            continue
        # Check if the type seems correct
        if not is_correct_policy_type(url_cand, policy_type):
            continue
        filtered_candidates.append((url_cand, score, reason))

    # Sort candidates by score
    filtered_candidates.sort(key=lambda x: x[1], reverse=True)
    
    if filtered_candidates:
        best_candidate = filtered_candidates[0]
        logger.info(f"Found best candidate for {policy_type} via {best_candidate[2]}: {best_candidate[0]} (Score: {best_candidate[1]})")
        return best_candidate[0]
    
    logger.info(f"No suitable candidate found for {policy_type} via structural search.")
    return None