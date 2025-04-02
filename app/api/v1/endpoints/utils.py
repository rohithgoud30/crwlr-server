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
            r'\bprivacy\s*notice\b',
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
            'privacy policy', 'privacy notice', 'privacy statement', 
            'data protection', 'privacy', 'privacidad', 'datenschutz',
            'gdpr', 'ccpa', 'data privacy', 'notice',
            # Amazon specific patterns
            'privacy', 'notice', 'privacy & cookie notice'
        ]
        
        # Count strong privacy matches in the text
        matches = sum(1 for pattern in strong_privacy_matches if pattern in text_lower)
        score += (matches * 5.0)
        
        # Additional bonus for exact matches - Amazon often uses just "Privacy Notice"
        if 'privacy policy' in text_lower:
            score += 8.0
        elif 'privacy notice' in text_lower:
            score += 8.0  # Equal score for "Privacy Notice" which Amazon uses
            
        # Extra bonus for Amazon domain + privacy notice
        if 'amazon' in url_lower and ('privacy' in text_lower or 'notice' in text_lower):
            score += 5.0  # Extra bonus for Amazon-specific patterns
            
        if 'privacy' in text_lower and 'policy' in text_lower:
            score += 4.0
        elif 'privacy' in text_lower and 'notice' in text_lower:
            score += 4.0  # Equal score for "privacy notice" pattern
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
        url_patterns = ['/privacy', 'privacy-policy', 'privacy_policy', 'privacypolicy', 'datenschutz', 'privacy-notice', 'notice']
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
    privacy_filenames = ['privacy.html', 'privacy.php', 'privacy.htm', 'privacy.aspx', 'privacy', 'notice.html']
    tos_filenames = ['terms.html', 'tos.html', 'terms.php', 'terms.htm', 'terms.aspx', 'tos', 'terms']
    
    if policy_type == 'privacy' and any(url_lower.endswith(fname) for fname in privacy_filenames):
        score += 5.0
    elif policy_type == 'tos' and any(url_lower.endswith(fname) for fname in tos_filenames):
        score += 5.0
    
    # Special handling for Amazon
    if 'amazon' in url_lower:
        if policy_type == 'privacy' and ('privacy' in url_lower or 'notice' in url_lower):
            score += 3.0  # Boost Amazon privacy pages
            
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
        '/datenschutz', 'privacidad', '/gdpr', 'data-protection', 'data_protection',
        'privacy-notice', 'privacy_notice', 'privacy-statement', 'privacy_statement',
        'notice', 'notice.html', '/notice/'  # Added notice variations
    ]
    
    # Define strong indicators for terms of service
    terms_indicators = [
        '/terms', '/tos', 'terms-of-service', 'terms_of_service', 'termsofservice',
        'terms-of-use', 'terms_of_use', 'termsofuse', 'terms-and-conditions',
        'terms_and_conditions', 'termsandconditions', 'terms.html', 'tos.html',
        'eula', 'conditions-of-use', 'condition-of-use', 'user-agreement'
    ]
    
    # Special case for Amazon domains
    if 'amazon' in url:
        if policy_type == 'privacy' and ('notice' in url or 'privacy' in url):
            return True
    
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

def find_policy_by_class_id(soup, policy_type: str) -> Optional[str]:
    """
    Find policy links by analyzing HTML class and ID attributes.
    This function focuses on elements with footer-related classes/IDs and
    checks if they contain policy-related terms.
    
    Args:
        soup: BeautifulSoup object of the page
        policy_type: Either 'privacy' or 'tos'
        
    Returns:
        URL of the policy page if found, otherwise None
    """
    base_url = ""
    for meta in soup.find_all('meta', {'property': 'og:url'}):
        if meta.get('content'):
            base_url = meta.get('content')
            break
    
    # If no og:url found, try to find base tag
    if not base_url:
        base_tag = soup.find('base', href=True)
        if base_tag:
            base_url = base_tag.get('href')
    
    # If still no base URL, use current URL from existing context if available
    if not base_url:
        # We'll handle this when returning the URL
        pass
    
    # Determine keywords based on policy type
    if policy_type == 'privacy':
        keywords = [
            'privacy', 
            'privacy policy', 
            'privacy-policy',
            'data protection', 
            'gdpr', 
            'data policy', 
            'personal data'
        ]
        # For privacy, we want to explicitly exclude support policies
        negative_keywords = ['support policy', 'support-policy']
    else:  # tos
        keywords = ['terms', 'conditions', 'terms of service', 'terms of use', 'legal']
        negative_keywords = []
    
    # Find elements with footer-related classes or IDs
    footer_elements = []
    
    # Look for actual footer elements
    footer_elements.extend(soup.find_all('footer'))
    
    # Look for elements with footer in class or ID
    for element in soup.find_all(attrs=True):
        attrs = element.attrs
        for attr_name in ['class', 'id']:
            if attr_name in attrs:
                attr_value = attrs[attr_name]
                if isinstance(attr_value, list):
                    attr_value = ' '.join(attr_value)
                
                if attr_value and 'footer' in attr_value.lower():
                    footer_elements.append(element)
                    break
    
    # Process found footer elements
    for footer in footer_elements:
        for link in footer.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
                
            # Make the URL absolute if possible
            if base_url:
                absolute_url = urljoin(base_url, href)
            else:
                # If no base URL is available, use the href as is
                absolute_url = href
                
            # Skip likely false positives
            if is_likely_false_positive(absolute_url, policy_type):
                continue
                
            # Check if this URL matches the expected policy type
            if not is_correct_policy_type(absolute_url, policy_type):
                continue
                
            # Check link text for keywords
            link_text = ' '.join([
                link.get_text().strip(),
                link.get('title', '').strip(),
                link.get('aria-label', '').strip()
            ]).lower()
            
            # Skip links containing negative keywords
            if any(neg_keyword in link_text.lower() for neg_keyword in negative_keywords):
                continue
                
            # Skip links with support-policy in URL path for privacy detection
            if policy_type == 'privacy' and '/support-policy' in absolute_url.lower():
                continue
                
            # Check URL path for specific patterns
            url_path = urlparse(href).path.lower() if href.startswith(('http://', 'https://')) else href.lower()
            
            # Exact path matches for privacy
            privacy_path_patterns = ['/privacy', '/privacy-policy', '/policy']
            if policy_type == 'privacy' and '/support-policy' in url_path:
                continue  # Skip support policy for privacy detection
            
            # Check if any keyword is in the link text or href
            if (policy_type == 'privacy' and any(pattern in url_path for pattern in privacy_path_patterns)) or \
               any(keyword in link_text for keyword in keywords) or \
               any(keyword in href.lower() for keyword in keywords):
                try:
                    # Skip likely false positives
                    if is_likely_false_positive(absolute_url, policy_type):
                        continue
                    
                    # Double-check policy type
                    if not is_correct_policy_type(absolute_url, policy_type):
                        continue
                        
                    return absolute_url
                except Exception:
                    continue
    
    # If nothing found in footer elements, look for elements with policy-related classes/IDs
    if policy_type == 'privacy':
        policy_class_patterns = ['privacy', 'privacy-policy', 'policy', 'legal', 'data-protection']
    else:
        policy_class_patterns = ['terms', 'tos', 'legal', 'conditions']
    
    for pattern in policy_class_patterns:
        for element in soup.find_all(attrs=True):
            attrs = element.attrs
            for attr_name in ['class', 'id']:
                if attr_name in attrs:
                    attr_value = attrs[attr_name]
                    if isinstance(attr_value, list):
                        attr_value = ' '.join(attr_value)
                    
                    if attr_value and pattern in attr_value.lower():
                        # Check if this element contains links with our keywords
                        for link in element.find_all('a', href=True):
                            href = link.get('href', '').strip()
                            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                                continue
                                
                            # Skip likely false positives
                            absolute_url = urljoin(base_url, href) if base_url else href
                            if is_likely_false_positive(absolute_url, policy_type):
                                continue
                                
                            # Check if this URL matches the expected policy type
                            if not is_correct_policy_type(absolute_url, policy_type):
                                continue
                                
                            # Skip support-policy for privacy detection
                            if policy_type == 'privacy' and '/support-policy' in absolute_url.lower():
                                continue
                                
                            link_text = ' '.join([
                                link.get_text().strip(),
                                link.get('title', '').strip(),
                                link.get('aria-label', '').strip()
                            ]).lower()
                            
                            # Skip links containing negative keywords
                            if any(neg_keyword in link_text.lower() for neg_keyword in negative_keywords):
                                continue
                            
                            # URL path patterns check
                            url_path = urlparse(href).path.lower() if href.startswith(('http://', 'https://')) else href.lower()
                            privacy_path_patterns = ['/privacy', '/privacy-policy', '/policy']
                            
                            # For this match, we'll be more strict about relevant keywords
                            if (policy_type == 'privacy' and any(pattern in url_path for pattern in privacy_path_patterns)) or \
                               any(keyword in link_text for keyword in keywords):
                                try:
                                    # Skip likely false positives
                                    if is_likely_false_positive(absolute_url, policy_type):
                                        continue
                                    
                                    # Double-check policy type
                                    if not is_correct_policy_type(absolute_url, policy_type):
                                        continue
                                        
                                    return absolute_url
                                except Exception:
                                    continue
    
    # Look for any links with very specific privacy policy paths or text
    if policy_type == 'privacy':
        for link in soup.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('javascript:', 'mailto:', 'tel:', '#')):
                continue
            
            # Skip likely false positives
            absolute_url = urljoin(base_url, href) if base_url else href
            if is_likely_false_positive(absolute_url, policy_type):
                continue
            
            # Skip support-policy for privacy detection
            if '/support-policy' in absolute_url.lower():
                continue
            
            # Ensure this URL is not a ToS URL
            if not is_correct_policy_type(absolute_url, policy_type):
                continue
                
            url_path = urlparse(href).path.lower() if href.startswith(('http://', 'https://')) else href.lower()
            
            if any(pattern in url_path for pattern in ['/privacy', '/privacy-policy', '/policy']):
                # Skip support-policy
                if '/support-policy' not in url_path:
                    try:
                        return absolute_url
                    except Exception:
                        continue
                
            link_text = link.get_text().strip().lower()
            if link_text in ['privacy', 'privacy policy', 'privacy-policy', 'policy']:
                # Skip links containing negative keywords
                if not any(neg_keyword in link_text for neg_keyword in negative_keywords):
                    try:
                        return absolute_url
                    except Exception:
                        continue
    
    return None

def make_url_absolute(url: str, base_url: str) -> str:
    """Convert relative URLs to absolute URLs."""
    if not url:
        return ""
        
    # If the URL starts with a single slash, it's relative to the domain root
    if url.startswith('/') and not url.startswith('//'):
        parsed_base = urlparse(base_url)
        base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"
        return urljoin(base_domain, url)
    
    # If it starts with // (protocol-relative), add the protocol
    if url.startswith('//'):
        parsed_base = urlparse(base_url)
        return f"{parsed_base.scheme}:{url}"
        
    # If it's a relative URL without a leading slash, join with the base URL
    if not url.startswith(('http://', 'https://', '//')):
        return urljoin(base_url, url)
    
    # It's already an absolute URL
    return url