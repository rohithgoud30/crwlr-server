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
    """Returns exact match patterns and strong URL patterns based on policy type."""
    if policy_type == 'privacy':
        # Exact match patterns for privacy
        exact_patterns = [
            r'(?:^|\s)privacy(?:\s|$)', 
            r'(?:^|\s)privacy(?:\s|-)policy(?:\s|$)', 
            r'(?:^|\s)data(?:\s|-)protection(?:\s|$)',
            r'(?:^|\s)your(?:\s|-)privacy(?:\s|$)',
            r'(?:^|\s)pp(?:\s|$)',
            r'(?:^|\s)privacy(?:\s|-|_)notice(?:\s|$)',
            r'(?:^|\s)privacy(?:\s|-|_)policy(?:\s|$)',
            r'(?:^|\s)policy(?:\s|$)'
        ]
        
        # Strong URL patterns for privacy
        strong_url_patterns = [
            '/privacy',
            '/privacy-policy',
            '/policy',
            '/privacy_policy',
            '/privacypolicy',
            '/privacy-notice',
            '/gdpr',
            '/data-protection',
            '/datenschutz'  # German privacy
        ]
        
    else:  # tos patterns
        # Exact match patterns for ToS
        exact_patterns = [
            r'(?:^|\s)terms(?:\s|$)',
            r'(?:^|\s)terms(?:\s|-)of(?:\s|-)(?:service|use)(?:\s|$)',
            r'(?:^|\s)(?:service|use)(?:\s|-)terms(?:\s|$)',
            r'(?:^|\s)terms(?:\s|-)and(?:\s|-)conditions(?:\s|$)',
            r'(?:^|\s)(?:user(?:\s|-)|)agreement(?:\s|$)',
            r'(?:^|\s)legal(?:\s|$)',
            r'(?:^|\s)conditions(?:\s|-)of(?:\s|-)use(?:\s|$)',
            r'(?:^|\s)tos(?:\s|$)'
        ]
        
        # Strong URL patterns for ToS
        strong_url_patterns = [
            '/terms',
            '/tos',
            '/terms-of-service',
            '/terms-of-use',
            '/terms_of_service',
            '/termsofservice',
            '/termsofuse',
            '/terms_of_use',
            '/terms-and-conditions',
            '/legal',
            '/conditions',
            '/eula',
            '/user-agreement'
        ]
    
    return exact_patterns, strong_url_patterns

def get_policy_score(link_text: str, url: str, policy_type: str) -> float:
    """
    Calculate a score for how likely a link is to be the policy we're looking for.
    
    Args:
        link_text: The text content of the link
        url: The URL of the link
        policy_type: Either 'privacy' or 'tos'
        
    Returns:
        A float score indicating likelihood of being the target policy
    """
    score = 0.0
    
    # Prevent document/API links from being mistaken for ToS
    if policy_type == 'tos' and ('/docs' in url or '/documentation' in url or '/api' in url or '/doc/' in url):
        if not any(term in url for term in [
            '/terms', '/tos', '/terms-of-service', '/terms-of-use', 
            '/legal/terms', '/eula', '/conditions-of-use'
        ]):
            return -5.0  # Strong negative score for documentation URLs
    
    # Handle each policy type differently
    if policy_type == 'privacy':
        # Strong positive matches for privacy
        pri_exact_terms = [
            r'\bprivacy\b', 
            r'\bprivacy policy\b', 
            r'\bdata protection\b',
            r'\bgdpr\b',
            r'\bccpa\b',
            r'\bprivate policy\b',
            r'\bdata privacy\b',
            r'\bprivacidad\b',  # Spanish
            r'\bdatenschutz\b'  # German
        ]
        
        for term in pri_exact_terms:
            if re.search(term, link_text):
                score += 3.0
                break
                
        # URL path patterns for privacy
        if '/privacy' in url or '/privacypolicy' in url or '/privacy-policy' in url:
            score += 2.5
        elif '/data-protection' in url or '/datenschutz' in url:
            score += 2.0
        elif '/gdpr' in url or '/ccpa' in url:
            score += 2.0
            
        # Secondary text patterns
        secondary_terms = [
            r'\bdata\b.*\bcollect', 
            r'\bcookie', 
            r'\buser data\b', 
            r'\bpersonal information\b',
            r'\binformation we collect\b'
        ]
        
        for term in secondary_terms:
            if re.search(term, link_text):
                score += 0.5
                break
    
    elif policy_type == 'tos':
        # Strong positive matches for ToS
        tos_exact_terms = [
            r'\bterms\b',
            r'\bterms of service\b', 
            r'\bterms of use\b',
            r'\buser agreement\b',
            r'\bconditions\b',
            r'\bterms and conditions\b',
            r'\beula\b',
            r'\bend user license\b',
            r'\blegal\b',
            r'\btos\b',
            r'\bterms & conditions\b',
            r'\bcondiciones\b',  # Spanish
            r'\bnutzungsbedingungen\b'  # German
        ]
        
        for term in tos_exact_terms:
            if re.search(term, link_text):
                score += 3.0
                break
                
        # Avoid documentation links with "terms" used in a different context
        if re.search(r'\bterminology\b', link_text) and ('doc' in url or 'api' in url):
            score -= 4.0
                
        # URL path patterns for ToS
        if '/terms' in url or '/tos' in url or '/terms-of-service' in url:
            score += 2.5
        elif '/terms-of-use' in url or '/terms-conditions' in url:
            score += 2.0
        elif '/legal' in url and not ('/privacy' in url or '/copyright' in url):
            score += 1.5
        elif '/eula' in url or '/user-agreement' in url:
            score += 2.0
            
        # Secondary text patterns
        secondary_terms = [
            r'\bagree', 
            r'\bacceptance\b', 
            r'\bservice\b.*\bpolicy\b',
            r'\blicense\b',
            r'\baccount\b.*\bterms\b'
        ]
        
        for term in secondary_terms:
            if re.search(term, link_text):
                score += 0.5
                break
    
    # Common legal terms (less weight)
    legal_terms = [
        r'\blegal\b', 
        r'\bpolicy\b', 
        r'\bcopyright\b',
        r'\blicense\b',
        r'\brights\b',
        r'\bobligations\b'
    ]
    
    for term in legal_terms:
        if re.search(term, link_text):
            score += 0.25
            break
            
    # Negative patterns specifically for technical documentation
    if '/docs' in url or '/documentation' in url or '/api' in url:
        # Check if link text suggests technical documentation
        doc_terms = [
            r'\bapi\b', 
            r'\bguide\b', 
            r'\btutorial\b',
            r'\bfunction\b',
            r'\bmethod\b',
            r'\bdocumentation\b',
            r'\bexamples\b',
            r'\bcode\b',
            r'\bdevelopers?\b'
        ]
        
        for term in doc_terms:
            if re.search(term, link_text):
                score -= 3.0  # Strong negative for technical docs
                break
    
    return score

def is_likely_false_positive(url: str, policy_type: str) -> bool:
    """
    Check if a URL is likely to be a false positive (not actually a policy page).
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
    
    return False

def is_correct_policy_type(url: str, expected_type: str) -> bool:
    """
    Check if a URL is likely to be the correct policy type we're looking for.
    This helps avoid misclassification between privacy policies and terms of service.
    
    Args:
        url: The URL to check
        expected_type: Either 'privacy' or 'tos'
        
    Returns:
        True if the URL appears to be the correct policy type, False otherwise
    """
    url_lower = url.lower()
    
    # For documentation pages, require very explicit ToS indicators
    if '/docs' in url_lower or '/documentation' in url_lower or '/doc/' in url_lower:
        if expected_type == 'tos':
            # Only accept as ToS if it explicitly contains terms keywords in the URL
            return any(term in url_lower for term in [
                '/terms', '/tos', '/terms-of-service', '/terms-of-use', 
                '/terms-and-conditions', '/legal/terms', '/eula', 
                '/conditions-of-use', '/user-agreement'
            ])
        return False  # For privacy policies, don't accept doc pages
    
    # For regular policy detection:
    if expected_type == 'privacy':
        # Strong privacy patterns
        privacy_patterns = [
            '/privacy', '/privacy-policy', '/data-protection', '/data-privacy',
            '/personal-data', '/gdpr', '/ccpa', '/privacypolicy', '/pp'
        ]
        
        # If the URL strongly indicates privacy policy, return True
        if any(pattern in url_lower for pattern in privacy_patterns):
            return True
            
        # Check for terms patterns - if we're looking for privacy but it's a terms URL, return False
        terms_patterns = [
            '/terms', '/tos', '/terms-of-service', '/terms-of-use', 
            '/terms-and-conditions', '/legal/terms', '/conditions', 
            '/eula', '/user-agreement'
        ]
        
        if any(pattern in url_lower for pattern in terms_patterns):
            # If it contains both privacy and terms, it might be a combined policy
            if any(pattern in url_lower for pattern in privacy_patterns):
                return True
            return False
            
    elif expected_type == 'tos':
        # Strong terms patterns
        terms_patterns = [
            '/terms', '/tos', '/terms-of-service', '/terms-of-use', 
            '/terms-and-conditions', '/legal/terms', '/conditions', 
            '/eula', '/user-agreement'
        ]
        
        # If the URL strongly indicates terms, return True
        if any(pattern in url_lower for pattern in terms_patterns):
            return True
            
        # Check for privacy patterns - if we're looking for terms but it's a privacy URL, return False
        privacy_patterns = [
            '/privacy', '/privacy-policy', '/data-protection', '/data-privacy',
            '/personal-data', '/gdpr', '/ccpa'
        ]
        
        if any(pattern in url_lower for pattern in privacy_patterns):
            # If it contains both terms and privacy, it might be a combined policy
            if any(pattern in url_lower for pattern in terms_patterns):
                return True
            return False
    
    # If no strong patterns were found, assume it might be the right type
    # This handles general legal pages that could contain either policy type
    return True

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
            'policy',
            'data protection', 
            'gdpr', 
            'data policy', 
            'personal data'
        ]
    else:  # tos
        keywords = ['terms', 'conditions', 'terms of service', 'terms of use', 'legal']
    
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
            
            # Check URL path for specific patterns
            url_path = urlparse(href).path.lower() if href.startswith(('http://', 'https://')) else href.lower()
            
            # Exact path matches for privacy
            privacy_path_patterns = ['/privacy', '/privacy-policy', '/policy']
            
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
                                
                            link_text = ' '.join([
                                link.get_text().strip(),
                                link.get('title', '').strip(),
                                link.get('aria-label', '').strip()
                            ]).lower()
                            
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
            
            # Ensure this URL is not a ToS URL
            if not is_correct_policy_type(absolute_url, policy_type):
                continue
                
            url_path = urlparse(href).path.lower() if href.startswith(('http://', 'https://')) else href.lower()
            
            if any(pattern in url_path for pattern in ['/privacy', '/privacy-policy', '/policy']):
                try:
                    return absolute_url
                except Exception:
                    continue
                
            link_text = link.get_text().strip().lower()
            if link_text in ['privacy', 'privacy policy', 'privacy-policy', 'policy']:
                try:
                    return absolute_url
                except Exception:
                    continue
    
    return None