import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import re
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def standard_tos_finder(variations_to_try, headers, session):
    """
    Standard approach to find ToS links using requests and BeautifulSoup.
    """
    for url, url_type in variations_to_try:
        try:
            logger.info(f"Trying URL variation: {url} ({url_type})")
            response = session.get(url, headers=headers, timeout=15)
            
            # Handle redirects - if the URL changed, log it
            if response.url != url:
                logger.info(f"Followed redirect: {url} -> {response.url}")
                url = response.url  # Update the URL to the final one after redirects
            
            # Log the current URL we're processing
            logger.info(f"Fetching content from {url}")
            
            # Check if response is valid
            if response.status_code != 200:
                logger.warning(f"Got status code {response.status_code} for {url} ({url_type})")
                continue
            
            # Parse HTML content
            logger.info(f"Searching for ToS link in {url}")
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find the ToS link
            tos_url = find_tos_link(url, soup)
            
            # If we found a ToS URL, return success
            if tos_url:
                logger.info(f"Found ToS link: {tos_url} in {url} ({url_type})")
                return {
                    "url": url,
                    "tos_url": tos_url,
                    "success": True,
                    "message": f"Found Terms of Service link for {url}",
                    "method_used": "standard"
                }
                
        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({url_type}): {str(e)}")
        except Exception as e:
            logger.error(f"Error processing {url} ({url_type}): {str(e)}")
    
    # If we get here, no ToS link was found with the standard method
    return {
        "url": variations_to_try[0][0] if variations_to_try else "unknown",
        "success": False,
        "message": "No Terms of Service link found using standard method",
        "method_used": "standard_failed"
    }

def find_tos_link(url, soup):
    """
    Find and return the Terms of Service link from a webpage.
    
    Added special handling for sites with common terms layout like t3.chat.
    """
    logger.info("DETAILED DEBUG: Scanning elements in HTML...")
    
    # Keywords that might indicate a ToS link
    tos_keywords = [
        'terms of service', 'terms of use', 'terms and conditions', 
        'user agreement', 'terms', 'legal terms', 'eula',
        'end user license agreement', 'conditions', 'legal'
    ]
    
    # Normalize the base URL for converting relative links
    base_url = url
    
    # Debug - print all links with text and href
    links = soup.find_all('a')
    for i, link in enumerate(links):
        href = link.get('href')
        if not href:
            continue
        
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
        
        link_text = link.get_text().strip()
        logger.info(f"Link {i}: Text='{link_text}', Href='{href}'")
        
        # Special check for terms-related paths like /terms-of-service
        if (href.lower().endswith('/terms-of-service') or 
            href.lower().endswith('/terms') or 
            'terms-of-service' in href.lower() or
            'termsofservice' in href.lower() or
            'tos' in href.lower()):
            
            logger.info(f"FOUND TERMS LINK BY PATH: {href}")
            # Convert relative URL to absolute
            if not href.startswith(('http://', 'https://')):
                href = urljoin(base_url, href)
            return href
            
    # Priority 1: Look for links with exact ToS terms
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        # Get the link text and normalize it
        link_text = link.get_text().lower().strip()
        
        # Check for exact matches of priority terms
        if link_text in ['terms of service', 'terms of use', 'terms and conditions']:
            # Convert relative URL to absolute
            if not href.startswith(('http://', 'https://')):
                href = urljoin(base_url, href)
            
            # Skip common platform terms pages (like Google, Apple, etc.)
            if any(skip in href.lower() for skip in [
                'google.com/terms', 'apple.com/legal', 'microsoft.com/terms',
                'twitter.com/tos', 'facebook.com/terms'
            ]) and skip not in url.lower():
                continue
                
            return href
    
    # Priority 2: Look for links containing 'terms' as part of text
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        link_text = link.get_text().lower().strip()
        
        # Check for terms mentioned in text
        for keyword in tos_keywords[:5]:  # Use higher priority keywords first
            if keyword in link_text:
                # Skip common platform terms
                if any(skip in href.lower() for skip in [
                    'google.com/terms', 'apple.com/legal', 'microsoft.com/terms',
                    'twitter.com/tos', 'facebook.com/terms'
                ]) and skip not in url.lower():
                    continue
                
                # Convert relative URL to absolute
                if not href.startswith(('http://', 'https://')):
                    href = urljoin(base_url, href)
                return href
    
    # Priority 3: Check URLs for terms indications
    for link in links:
        href = link.get('href')
        if not href:
            continue
            
        # Skip empty links, anchors, javascript, and mailto
        if href == '#' or href.startswith(('javascript:', 'mailto:')):
            continue
            
        # Check if the URL contains terms indicators
        href_lower = href.lower()
        if any(term in href_lower for term in ['terms', 'tos', 'termsofservice', 'terms-of-service']):
            # Skip common platform terms
            if any(skip in href_lower for skip in [
                'google.com/terms', 'apple.com/legal', 'microsoft.com/terms',
                'twitter.com/tos', 'facebook.com/terms'
            ]) and skip not in url.lower():
                continue
            
            # Convert relative URL to absolute
            if not href.startswith(('http://', 'https://')):
                href = urljoin(base_url, href)
            return href
    
    # Priority 4: Look for links in the page footer
    footer_elem = soup.find('footer')
    if not footer_elem:
        # If no <footer> tag, look for elements with footer in class or id
        footer_elem = soup.find(lambda tag: tag.name and tag.get('class') and 
                            any('footer' in cls.lower() for cls in tag.get('class')))
        if not footer_elem:
            footer_elem = soup.find(lambda tag: tag.name and tag.get('id') and 'footer' in tag.get('id').lower())
    
    if footer_elem:
        footer_links = footer_elem.find_all('a')
        for link in footer_links:
            href = link.get('href')
            if not href:
                continue
                
            # Skip empty links, anchors, javascript, and mailto
            if href == '#' or href.startswith(('javascript:', 'mailto:')):
                continue
                
            link_text = link.get_text().lower().strip()
            
            # Check for ToS-related text
            if any(keyword in link_text for keyword in tos_keywords):
                # Skip common platform terms
                if any(skip in href.lower() for skip in [
                    'google.com/terms', 'apple.com/legal', 'microsoft.com/terms',
                    'twitter.com/tos', 'facebook.com/terms'
                ]) and skip not in url.lower():
                    continue
                
                # Convert relative URL to absolute
                if not href.startswith(('http://', 'https://')):
                    href = urljoin(base_url, href)
                return href
    
    # If we get here, no ToS link was found
    return None

async def test_t3_chat():
    url = "https://t3.chat"
    
    # Enhanced browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Sec-CH-UA': '"Chromium";v="123", "Google Chrome";v="123"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"macOS"',
        'Cache-Control': 'max-age=0',
        'DNT': '1',
    }

    # Create a session to maintain cookies across requests
    session = requests.Session()
    
    # Continue with regular processing for non-App Store URLs
    variations_to_try = []
    
    # 1. First prioritize the exact URL provided by the user
    variations_to_try.append((url, "original exact url"))
    
    # 2. Then add base domain variations for fallback
    parsed = urlparse(url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # Only add base domain if it's different from the original URL
    if base_domain != url:
        variations_to_try.append((base_domain, "base domain"))
        
        # Also add www/non-www variations of the base domain
        domain = parsed.netloc.lower()
        if domain.startswith('www.'):
            domain_without_www = domain[4:]
            new_url = base_domain.replace(domain, domain_without_www)
            variations_to_try.append((new_url, "base domain without www"))
        else:
            domain_with_www = f"www.{domain}"
            new_url = base_domain.replace(domain, domain_with_www)
            variations_to_try.append((new_url, "base domain with www"))
    
    logger.info(f"URL variations to try: {variations_to_try}")
    
    # Try the standard method
    standard_result = await standard_tos_finder(variations_to_try, headers, session)
    print("Result:", standard_result)

# Run the test
if __name__ == "__main__":
    asyncio.run(test_t3_chat()) 