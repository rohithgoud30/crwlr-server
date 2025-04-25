from fastapi import APIRouter, Response, HTTPException, status
import logging
import requests
from bs4 import BeautifulSoup
import re
from typing import Optional
from urllib.parse import urlparse, urljoin

from app.models.company_info import CompanyInfoRequest, CompanyInfoResponse

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

def get_base_url(url: str) -> str:
    """Extract the base URL from a given URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"

def sanitize_url(url: str) -> str:
    """
    Sanitize and validate URLs to ensure they are valid.
    
    If the URL is severely malformed or clearly invalid, returns an empty string
    instead of attempting to fix it.
    """
    if not url:
        logger.warning("Empty URL provided")
        return ""
        
    # Trim whitespace and control characters
    url = url.strip().strip('\r\n\t')
    
    # Log the original URL for debugging
    logger.info(f"Validating URL: {url}")
    
    try:
        # Fix only the most common minor issues
        # Add protocol if missing
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        
        # Validate the URL structure
        parsed = urlparse(url)
        
        # Check for severely malformed URLs
        if not parsed.netloc or '.' not in parsed.netloc:
            logger.warning(f"Invalid domain in URL: {url}")
            return ""
            
        # Check for nonsensical URL patterns that indicate a malformed URL
        if re.match(r'https?://[a-z]+s?://', url):
            # Invalid patterns like https://ttps://
            logger.warning(f"Malformed URL with invalid protocol pattern: {url}")
            return ""
            
        # Additional validation to ensure domain has a valid TLD
        domain_parts = parsed.netloc.split('.')
        if len(domain_parts) < 2 or len(domain_parts[-1]) < 2:
            logger.warning(f"Domain lacks valid TLD: {url}")
            return ""
            
        logger.info(f"URL validated: {url}")
        return url
    except Exception as e:
        logger.error(f"Error validating URL {url}: {str(e)}")
        return ""

def normalize_url(url: str) -> str:
    """Normalize URL to handle common variations"""
    if not url:
        return url
    
    # Remove trailing slashes, fragments and normalize to lowercase
    url = url.lower().split('#')[0].rstrip('/')
    
    # Ensure URL has protocol
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
        
    return url

async def extract_company_info(url: str) -> tuple:
    """
    Extract company name and logo from a website.
    
    Returns:
    - Tuple of (company_name, logo_url, success, message)
    """
    try:
        # Validate and normalize the URL
        sanitized_url = sanitize_url(url)
        if not sanitized_url:
            return "Unknown Company", "/placeholder.svg?height=48&width=48", False, f"Invalid URL '{url}'"
            
        normalized_url = normalize_url(sanitized_url)
        
        # Default values in case extraction fails
        default_logo_url = "/placeholder.svg?height=48&width=48"
        domain = urlparse(normalized_url).netloc
        
        # Default company name from domain
        if domain.startswith('www.'):
            domain = domain[4:]
        default_company_name = domain.split('.')[0].capitalize()
        
        # Initialize with defaults
        company_name = default_company_name
        logo_url = default_logo_url
        success = False
        message = "Initialization"
        
        # Try to fetch the page
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(normalized_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        base_url = get_base_url(normalized_url)
        
        # 1. Try to extract company name from title tag
        if soup.title and soup.title.string:
            title_text = soup.title.string.strip()
            # Clean up title (remove common suffixes like "- Home", "| Official Website", etc.)
            common_suffixes = [
                " - Home", " | Home", " - Official Website", " | Official Website",
                " - Official Site", " | Official Site"
            ]
            for suffix in common_suffixes:
                if title_text.endswith(suffix):
                    title_text = title_text[:-len(suffix)]
            
            # Remove domain name if it appears in title
            domain_parts = domain.split('.')
            if len(domain_parts) > 1:
                domain_name = domain_parts[0].lower()
                if domain_name in title_text.lower():
                    company_name = title_text.strip()
                else:
                    # If no domain match, use the title but limit length
                    company_name = title_text[:50].strip()
        
        # 2. Try to extract logo from various sources
        logo_found = False
        
        # Method 1: Look for meta tags with 'logo' in the property/name
        meta_logo_tags = soup.find_all('meta', attrs={'property': lambda x: x and 'logo' in x.lower() if x else False})
        if not meta_logo_tags:
            meta_logo_tags = soup.find_all('meta', attrs={'name': lambda x: x and 'logo' in x.lower() if x else False})
        
        if meta_logo_tags:
            for tag in meta_logo_tags:
                if tag.get('content'):
                    logo_url = urljoin(base_url, tag['content'])
                    logo_found = True
                    break
        
        # Method 2: Look for OpenGraph image
        if not logo_found:
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                logo_url = urljoin(base_url, og_image['content'])
                logo_found = True
        
        # Method 3: Look for schema.org structured data with logo
        if not logo_found:
            schema_tags = soup.find_all('script', type='application/ld+json')
            for tag in schema_tags:
                try:
                    import json
                    data = json.loads(tag.string)
                    if isinstance(data, dict) and 'logo' in data:
                        if isinstance(data['logo'], str):
                            logo_url = urljoin(base_url, data['logo'])
                            logo_found = True
                            break
                        elif isinstance(data['logo'], dict) and 'url' in data['logo']:
                            logo_url = urljoin(base_url, data['logo']['url'])
                            logo_found = True
                            break
                except Exception as e:
                    logger.warning(f"Error parsing JSON-LD: {e}")
                    continue
        
        # Method 4: Look for common logo class/id patterns
        if not logo_found:
            logo_selectors = [
                'img.logo', 'img#logo', '.logo img', '#logo img',
                'img.brand', 'img#brand', '.brand img', '#brand img',
                'img.site-logo', 'img#site-logo', '.site-logo img', '#site-logo img',
                'header img', '.header img', '#header img',
                '.navbar-brand img', '.brand-logo img'
            ]
            
            for selector in logo_selectors:
                try:
                    logo_img = soup.select_one(selector)
                    if logo_img and logo_img.get('src'):
                        logo_url = urljoin(base_url, logo_img['src'])
                        logo_found = True
                        break
                except Exception as e:
                    logger.warning(f"Error with selector {selector}: {e}")
                    continue
        
        # Method 5: Fall back to favicon if nothing else worked
        if not logo_found:
            # Check for link rel="icon" or rel="shortcut icon"
            favicon_link = soup.find('link', rel=lambda r: r and ('icon' in r.lower() if r else False))
            if favicon_link and favicon_link.get('href'):
                logo_url = urljoin(base_url, favicon_link['href'])
                logo_found = True
            else:
                # Use Google's favicon service as last resort
                logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                logo_found = True
        
        success = True
        message = "Successfully extracted company information"
        
        # Verify logo URL is valid
        if logo_found:
            try:
                # Don't download the image, just check if it exists with a HEAD request
                logo_test = requests.head(logo_url, timeout=5)
                if logo_test.status_code >= 400:
                    logger.warning(f"Logo URL returned error: {logo_test.status_code}")
                    logo_url = default_logo_url
            except Exception as e:
                logger.warning(f"Error verifying logo URL: {e}")
                logo_url = default_logo_url
        
        return company_name, logo_url, success, message
        
    except Exception as e:
        logger.error(f"Error extracting company info: {e}")
        # Return defaults with error message
        domain = urlparse(normalized_url).netloc if 'normalized_url' in locals() else "unknown"
        default_company_name = domain.split('.')[0].capitalize() if domain else "Unknown Company"
        return default_company_name, default_logo_url, False, f"Error: {str(e)}"

@router.post("/extract-company-info", response_model=CompanyInfoResponse)
async def get_company_info(request: CompanyInfoRequest) -> CompanyInfoResponse:
    """
    Extract company name and logo from a website.
    
    Uses BeautifulSoup to parse the HTML and extract:
    1. Company name from title or other metadata
    2. Logo URL from various sources (meta tags, OpenGraph, schema.org, common selectors)
    
    Falls back to default values if extraction fails.
    """
    logger.info(f"Extracting company info for URL: {request.url}")
    
    company_name, logo_url, success, message = await extract_company_info(request.url)
    
    return CompanyInfoResponse(
        url=request.url,
        company_name=company_name,
        logo_url=logo_url,
        success=success,
        message=message
    ) 