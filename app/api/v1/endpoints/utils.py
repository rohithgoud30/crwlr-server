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
    """Calculate a score based on whether the link is in a footer or similar bottom section."""
    score = 0.0
    
    # Check if the link itself is in a footer-like element
    parent = link.parent
    depth = 0
    max_depth = 5  # Don't go too far up the tree
    
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

def get_policy_score(link_text: str, href_lower: str, policy_type: str) -> float:
    """
    Calculate score based on policy-specific terms in link text and URL.
    Heavily prioritizes specific terms over general legal terms.
    """
    score = 0.0
    
    if policy_type == 'tos':
        # Check link text for ToS terms first with very high score
        tos_terms = ['terms of service', 'terms of use', 'terms', 'tos', 'user agreement']
        if any(term in link_text.lower() for term in tos_terms):
            score += 10.0  # Very high score for ToS terms
        
        # Check URL path for ToS terms with high score
        if any(term in href_lower for term in ['/terms', '/tos', '/terms-of-service', '/terms-of-use']):
            score += 8.0  # High score for ToS URLs
            
        # Heavily penalize legal paths when looking for ToS
        if '/legal' in href_lower and not any(term in href_lower for term in ['/terms', '/tos']):
            score -= 5.0  # Strong penalty for legal without terms
            
        # Small boost for general terms, but much less than ToS terms
        if 'conditions' in link_text.lower() or 'legal' in link_text.lower():
            score += 0.5
            
    else:  # privacy
        # Check link text for privacy terms first with very high score
        privacy_terms = ['privacy', 'gdpr', 'data protection', 'data privacy']
        if any(term in link_text.lower() for term in privacy_terms):
            score += 10.0  # Very high score for privacy terms
        
        # Check URL path for privacy terms with high score
        if any(term in href_lower for term in ['/privacy', '/gdpr', '/data-protection', '/data-privacy']):
            score += 8.0  # High score for privacy URLs
            
        # Heavily penalize legal paths when looking for privacy
        if '/legal' in href_lower and not any(term in href_lower for term in ['/privacy', '/gdpr', '/data-protection']):
            score -= 5.0  # Strong penalty for legal without privacy
            
        # Small boost for data-related terms, but much less than privacy terms
        if 'data' in link_text.lower():
            score += 0.5
    
    return score

def is_likely_false_positive(url: str, policy_type: str) -> bool:
    """
    Checks if a URL is likely to be a false positive match for a policy page.
    
    Args:
        url: The URL to check
        policy_type: Either 'privacy' or 'tos'
        
    Returns:
        True if the URL is likely a false positive, False otherwise
    """
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    
    # Common false positive patterns
    false_positive_patterns = [
        '/comments', 
        '/questions',
        '/comments-and-questions',
        '/contact',
        '/contact-us',
        '/feedback',
        '/about',
        '/about-us',
        '/faq',
        '/help',
        '/support'
    ]
    
    # Check if the path matches any false positive pattern
    for pattern in false_positive_patterns:
        if pattern in path:
            # For exact matches or patterns at the end of the path
            if path.endswith(pattern) or path.endswith(f"{pattern}/"):
                return True
    
    return False

def is_correct_policy_type(url: str, policy_type: str) -> bool:
    """
    Strictly checks if a URL matches the expected policy type.
    Prevents confusing privacy policy URLs with ToS URLs and vice versa.
    
    Args:
        url: The URL to check
        policy_type: Either 'privacy' or 'tos'
        
    Returns:
        True if the URL matches the policy type, False otherwise
    """
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    
    # Get keywords for opposite policy type to exclude
    opposing_keywords = []
    
    if policy_type == 'privacy':
        # For privacy links, exclude URLs with clear ToS indicators
        opposing_keywords = [
            '/terms', 
            '/tos',
            '/terms-of-service',
            '/terms-of-use',
            'terms_of_service',
            'termsofservice',
            'termsofuse',
            'terms_of_use',
            'terms-and-conditions',
            'eula',
            'user-agreement'
        ]
    else:  # tos
        # For ToS links, exclude URLs with clear privacy indicators
        opposing_keywords = [
            '/privacy',
            '/privacy-policy',
            'privacy_policy',
            'privacypolicy',
            '/gdpr',
            '/data-protection',
            'datenschutz'
        ]
    
    # If URL contains opposing keywords, it's not the correct policy type
    for keyword in opposing_keywords:
        if keyword in path:
            return False
    
    # For stricter checking, also examine the link text if available
    link_text = ''
    
    # If URL contains expected keywords, it's probably the right type
    if policy_type == 'privacy':
        expected_keywords = ['/privacy', '/privacy-policy', '/gdpr', '/data-protection']
        if any(keyword in path for keyword in expected_keywords):
            return True
    else:  # tos
        expected_keywords = ['/terms', '/tos', '/terms-of-service', '/terms-of-use', '/legal']
        if any(keyword in path for keyword in expected_keywords):
            return True
    
    # If we couldn't determine from the URL alone, default to true
    # The scoring system should handle the rest
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