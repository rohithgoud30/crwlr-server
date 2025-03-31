from urllib.parse import urlparse
import logging
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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