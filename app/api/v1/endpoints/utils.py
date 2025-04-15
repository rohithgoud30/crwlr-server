from urllib.parse import urlparse

def normalize_url(url: str) -> str:
    """
    Normalize a URL by ensuring it has a scheme and handling www prefix consistently.
    
    Args:
        url: The URL to normalize
        
    Returns:
        str: Normalized URL
    """
    # Add scheme if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Parse the URL
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    
    # Handle www prefix consistently
    if domain.startswith('www.'):
        domain = domain[4:]  # Remove www.
        
    # Rebuild the URL
    scheme = parsed_url.scheme
    path = parsed_url.path
    query = parsed_url.query
    fragment = parsed_url.fragment
    
    # Reconstruct the URL
    normalized_url = f"{scheme}://{domain}"
    
    if path:
        normalized_url += path
    
    if query:
        normalized_url += f"?{query}"
    
    if fragment:
        normalized_url += f"#{fragment}"
    
    return normalized_url 