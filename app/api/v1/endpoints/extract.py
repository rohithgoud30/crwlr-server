from fastapi import APIRouter, Response, HTTPException, Depends
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import re
import asyncio
from playwright.async_api import async_playwright
import logging
from html2text import html2text
import io
import PyPDF2
from typing import Optional, Union, Dict, List, Tuple
import random
import hashlib
import time
from functools import lru_cache
import concurrent.futures
import math
from fake_useragent import UserAgent

from app.models.extract import ExtractRequest, ExtractResponse
from app.models.tos import ToSResponse
from app.models.privacy import PrivacyResponse
from app.api.v1.endpoints.tos import find_tos
from app.api.v1.endpoints.privacy import find_privacy_policy
from app.models.tos import ToSRequest
from app.models.privacy import PrivacyRequest

# Filter out the XML parsed as HTML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize UserAgent for random browser User-Agent strings
ua_generator = UserAgent()

# Enhanced in-memory cache with TTL and size limit
CACHE = {}
CACHE_TTL = 3600  # Cache expiry in seconds (1 hour)
MAX_CACHE_SIZE = 500  # Maximum number of items in cache

# Concurrency control for Playwright instances
PLAYWRIGHT_SEMAPHORE = asyncio.Semaphore(3)  # Limit to 3 concurrent browser instances
PLAYWRIGHT_BACKOFF_TIMES = {}  # Track exponential backoff per domain

# Optimized timeouts
STANDARD_TIMEOUT = 10  # Reduced from 15 seconds
PLAYWRIGHT_TIMEOUT = 15000  # Reduced from 20000 ms
URL_DISCOVERY_TIMEOUT = 8  # Reduced from 10 seconds

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF = 5  # Initial backoff in seconds
MAX_BACKOFF = 300  # Maximum backoff in seconds (5 minutes)

# Extraction constants
MIN_CONTENT_LENGTH = 100  # Minimum characters for valid content
MAX_PDF_PAGES = 30  # Reduced from 50 pages
PDF_EXTRACTION_CHUNK_SIZE = 5  # Number of pages to process in parallel

# ThreadPoolExecutor for CPU-bound tasks
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Function to get a random user agent
def get_random_user_agent():
    """
    Returns a random, realistic user agent string from the fake-useragent library.
    Falls back to a default value if the API fails.
    """
    try:
        return ua_generator.random
    except Exception as e:
        # Fallback user agents in case the API fails
        fallback_user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        ]
        logger.error(f"Error getting random user agent: {e}. Using fallback.")
        return random.choice(fallback_user_agents)

# Optional proxy configuration - Add your proxies here
# Format: ["http://user:pass@host:port", "http://user:pass@host:port", ...]
# Leave empty to disable proxy rotation
PROXY_SERVERS = []
# PROXY_SERVERS = [
#     "http://user:pass@proxy1.example.com:8080",
#     "http://user:pass@proxy2.example.com:8080",
#     "http://user:pass@proxy3.example.com:8080",
# ]

# Track usage of each proxy to balance load
PROXY_USAGE_COUNT = {proxy: 0 for proxy in PROXY_SERVERS}
PROXY_LOCK = asyncio.Lock()  # Lock for synchronizing proxy selection

def get_from_cache(url: str) -> Optional[dict]:
    """Get cached response if it exists and is not expired"""
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cached_data = CACHE.get(cache_key)
    if cached_data and time.time() < cached_data.get("expires_at", 0):
        logger.info(f"Cache hit for URL: {url}")
        return cached_data["data"]
    return None

def add_to_cache(url: str, data: dict):
    """Add response to cache with expiry time and manage cache size"""
    cache_key = hashlib.md5(url.encode()).hexdigest()
    CACHE[cache_key] = {
        "data": data,
        "expires_at": time.time() + CACHE_TTL
    }
    
    # Prune cache if it exceeds maximum size
    if len(CACHE) > MAX_CACHE_SIZE:
        # Remove oldest 20% of entries
        entries = sorted(CACHE.items(), key=lambda x: x[1].get("expires_at", 0))
        for i in range(int(MAX_CACHE_SIZE * 0.2)):
            if i < len(entries):
                del CACHE[entries[i][0]]
    
    logger.info(f"Added to cache: {url}")

def sanitize_url(url: str) -> str:
    """
    Sanitize and validate URLs to ensure they are valid.
    
    If the URL is severely malformed or clearly invalid, returns an empty string
    instead of attempting to fix it.
    """
    if not url:
        logger.error("Empty URL provided")
        return ""
        
    # Trim whitespace and control characters
    url = url.strip().strip('\r\n\t')
    
    try:
        # Fix only the most common minor issues
        # Add protocol if missing
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        
        # Validate the URL structure
        parsed = urlparse(url)
        
        # Check for severely malformed URLs
        if not parsed.netloc or '.' not in parsed.netloc:
            logger.error(f"Invalid domain in URL: {url}")
            return ""
            
        # Check for nonsensical URL patterns that indicate a malformed URL
        if re.match(r'https?://[a-z]+s?://', url):
            # Invalid patterns like https://ttps://
            logger.error(f"Malformed URL with invalid protocol pattern: {url}")
            return ""
            
        # Additional validation to ensure domain has a valid TLD
        domain_parts = parsed.netloc.split('.')
        if len(domain_parts) < 2 or len(domain_parts[-1]) < 2:
            logger.error(f"Domain lacks valid TLD: {url}")
            return ""
            
        return url
    except Exception as e:
        logger.error(f"Error validating URL {url}: {str(e)}")
        return ""

async def find_document_url(url: str, document_type: str, original_url: str) -> Tuple[str, bool, str]:
    """
    Find appropriate legal document URL with timeout handling.
    Returns a tuple of (document_url, success, error_message)
    """
    try:
        # Create a timeout for URL discovery to prevent long waits
        discovery_timeout = asyncio.create_task(asyncio.sleep(URL_DISCOVERY_TIMEOUT))
        
        if document_type == "tos":
            # Find ToS URL
            tos_request = ToSRequest(url=url)
            find_task = asyncio.create_task(find_tos(tos_request))
            
            done, pending = await asyncio.wait(
                {find_task, discovery_timeout},
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in pending:
                task.cancel()
            
            if discovery_timeout in done:
                return "", False, "Timeout while finding Terms of Service URL"
            
            tos_response = find_task.result()
            
            if not tos_response.success or not tos_response.tos_url:
                return "", False, f"Failed to find Terms of Service URL: {tos_response.message}"
            
            return tos_response.tos_url, True, ""
            
        else:  # privacy policy
            # Find Privacy Policy URL
            pp_request = PrivacyRequest(url=url)
            find_task = asyncio.create_task(find_privacy_policy(pp_request))
            
            done, pending = await asyncio.wait(
                {find_task, discovery_timeout},
                return_when=asyncio.FIRST_COMPLETED
            )
            
            for task in pending:
                task.cancel()
            
            if discovery_timeout in done:
                return "", False, "Timeout while finding Privacy Policy URL"
            
            pp_response = find_task.result()
            
            if not pp_response.success or not pp_response.pp_url:
                return "", False, f"Failed to find Privacy Policy URL: {pp_response.message}"
            
            return pp_response.pp_url, True, ""
        
    except Exception as e:
        logger.error(f"Error finding document URL: {str(e)}")
        return "", False, f"Error finding document URL: {str(e)}"

@router.post("/extract", response_model=ExtractResponse)
async def extract_text(
    request: ExtractRequest, 
    response: Response
) -> ExtractResponse:
    """
    Takes a URL and extracts the text content.
    
    If document_type is provided (tos/privacy), it will:
    1. Find the appropriate legal document URL
    2. Extract the text content from the URL
    3. Return both the document URL and the extracted text
    
    Otherwise, it extracts content directly from the provided URL.
    
    Supports PDF files, standard HTML pages, and JavaScript-heavy sites.
    """
    try:
        logger.info(f"Extraction request received for URL: {request.url}")
        original_url = request.url
        url = sanitize_url(original_url)
        
        if not url:
            response.status_code = 400
            return ExtractResponse(
                url=original_url,
                document_type=request.document_type or "tos",
                text=None,
                success=False,
                message="Invalid URL format",
                method_used="standard"
            )
        
        logger.info(f"Sanitized URL: {url}")
        
        # Check domain backoff status
        domain = urlparse(url).netloc
        current_time = time.time()
        skip_playwright = False
        
        if domain in PLAYWRIGHT_BACKOFF_TIMES and PLAYWRIGHT_BACKOFF_TIMES[domain] > current_time:
            wait_time = int(PLAYWRIGHT_BACKOFF_TIMES[domain] - current_time)
            if wait_time > 60:  # If backoff is significant, try standard extraction first
                logger.info(f"Domain {domain} is in backoff for {wait_time}s. Trying standard extraction only.")
                # Skip playwright extraction for this domain temporarily
                skip_playwright = True
        
        document_url = ""
        document_type = request.document_type or "tos"
        
        # Check cache before processing
        cache_key = f"{url}:{document_type}"
        cached_result = get_from_cache(cache_key)
        if cached_result:
            logger.info(f"Cache hit for {cache_key}")
            return ExtractResponse(**cached_result)
        
        logger.info(f"Cache miss for {cache_key}")
        
        # If document_type is None, skip finding the legal document URL
        if request.document_type is None:
            logger.info(f"No document_type specified, using URL directly: {url}")
            extraction_url = url
            document_url = url
            document_type = "tos"  # Default for pydantic validation
            url_to_return = url
        # Find the legal document URL if document_type is provided
        elif document_type in ["tos", "pp"]:
            logger.info(f"Finding {document_type} URL for {url}")
            
            document_url, success, error_message = await find_document_url(url, document_type, original_url)
            
            if not success:
                logger.error(f"Failed to find document URL: {error_message}")
                return ExtractResponse(
                    url=original_url,
                    document_type=document_type,
                    text=None,
                    success=False,
                    message=error_message,
                    method_used="standard"
                )
                
            # Set the URL to the document URL since it was found
            extraction_url = document_url
            url_to_return = document_url
            logger.info(f"Found document URL: {extraction_url}")
        else:
            # If document_type is not supported, use the provided URL directly
            extraction_url = url
            document_url = url
            document_type = "tos"  # Default to "tos" as document_type since we need to use a valid Literal value
            url_to_return = url
            logger.info(f"Using URL directly for extraction: {extraction_url}")
        
        # Check cache again with the resolved document URL
        doc_cache_key = f"{extraction_url}:{document_type}"
        cached_doc_result = get_from_cache(doc_cache_key)
        if cached_doc_result:
            logger.info(f"Cache hit for resolved document URL: {doc_cache_key}")
            return ExtractResponse(**cached_doc_result)
        
        logger.info(f"Cache miss for resolved document URL: {doc_cache_key}")
        
        # Determine if URL is likely a PDF
        is_pdf = is_pdf_url(extraction_url)
        
        # Concurrent extraction strategy
        # Create tasks for different extraction methods based on URL type
        tasks = []
        
        if is_pdf:
            # For PDFs, only use PDF extraction
            logger.info(f"URL is PDF, using PDF extraction: {extraction_url}")
            tasks.append(asyncio.create_task(extract_pdf(extraction_url, document_type, url_to_return)))
        else:
            # For HTML content, try both methods concurrently
            logger.info(f"URL is HTML, using standard HTML extraction: {extraction_url}")
            tasks.append(asyncio.create_task(extract_standard_html(extraction_url, document_type, url_to_return)))
            
            # Only add playwright if not skipped
            if not skip_playwright:
                logger.info(f"Adding Playwright extraction: {extraction_url}")
                tasks.append(asyncio.create_task(extract_with_playwright(extraction_url, document_type, url_to_return)))
            else:
                logger.info(f"Skipping Playwright extraction due to backoff: {extraction_url}")
        
        # Wait for each extraction task to complete
        for i, task in enumerate(tasks):
            try:
                logger.info(f"Waiting for extraction task {i+1}/{len(tasks)} to complete")
                result = await task
                
                if result and hasattr(result, 'success') and result.success and result.text:
                    # This task succeeded - cancel other pending tasks
                    logger.info(f"Extraction task {i+1} succeeded, cancelling other tasks")
                    for j, other_task in enumerate(tasks):
                        if j != i and not other_task.done():
                            other_task.cancel()
                    
                    # Cache successful result
                    logger.info(f"Adding successful result to cache: {doc_cache_key}")
                    add_to_cache(doc_cache_key, result.dict())
                    return result
                else:
                    logger.warning(f"Extraction task {i+1} failed or returned empty content")
            except Exception as e:
                logger.warning(f"Extraction task {i+1} threw exception: {str(e)}")
        
        # If all strategies failed
        logger.error(f"All extraction strategies failed for URL: {extraction_url}")
        return ExtractResponse(
            url=url_to_return,
            document_type=document_type,
            text=None,
            success=False,
            message="Failed to extract text content after trying all methods",
            method_used="standard"
        )
            
    except Exception as e:
        logger.error(f"Error in extraction: {str(e)}", exc_info=True)
        return ExtractResponse(
            url=original_url,
            document_type=request.document_type or "tos",
            text=None,
            success=False,
            message=f"Error in extraction: {str(e)}",
            method_used="standard"
        )

async def extract_pdf(extraction_url: str, document_type: str, url_to_return: str) -> ExtractResponse:
    """Extract text from PDF URL"""
    logger.info(f"Attempting PDF extraction for: {extraction_url}")
    try:
        # Enhanced browser-like headers with random user agent
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'application/pdf,*/*',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        # Download the PDF with reduced timeout
        pdf_response = requests.get(extraction_url, headers=headers, timeout=STANDARD_TIMEOUT)
        pdf_response.raise_for_status()
        
        # Check if content type is PDF
        content_type = pdf_response.headers.get('Content-Type', '').lower()
        if 'application/pdf' in content_type or is_pdf_url(extraction_url):
            # Extract text from PDF
            pdf_text = extract_text_from_pdf(pdf_response.content)
            
            if pdf_text and len(pdf_text.strip()) > MIN_CONTENT_LENGTH:  # Ensure we got meaningful content
                return ExtractResponse(
                    url=url_to_return,
                    document_type=document_type,
                    text=pdf_text,
                    success=True,
                    message="Successfully extracted text from PDF document",
                    method_used="pdf"
                )
        
        raise Exception("Not a valid PDF or content extraction failed")
    except Exception as e:
        logger.warning(f"PDF extraction failed: {str(e)}")
        raise

async def extract_standard_html(extraction_url: str, document_type: str, url_to_return: str) -> ExtractResponse:
    """Extract text using standard requests and BeautifulSoup"""
    logger.info(f"Attempting standard extraction for: {extraction_url}")
    try:
        # Enhanced browser-like headers for HTML content with random user agent
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        # Use asyncio to make the request with a timeout
        loop = asyncio.get_event_loop()
        response_future = loop.run_in_executor(
            None,
            lambda: requests.get(extraction_url, headers=headers, timeout=STANDARD_TIMEOUT)
        )
        
        # Wait for the request with a timeout
        response_data = await asyncio.wait_for(response_future, timeout=STANDARD_TIMEOUT+1)
        response_data.raise_for_status()
        
        # Parse the HTML
        soup = BeautifulSoup(response_data.text, 'html.parser')
        
        # Extract text using optimized method
        extracted_text = extract_content_from_soup(soup)
        
        if extracted_text and len(extracted_text.strip()) > MIN_CONTENT_LENGTH:  # Ensure we got meaningful content
            return ExtractResponse(
                url=url_to_return,
                document_type=document_type,
                text=extracted_text,
                success=True,
                message="Successfully extracted text content using standard method",
                method_used="standard"
            )
        
        raise Exception("Standard extraction didn't yield sufficient content")
    except Exception as e:
        logger.warning(f"Standard extraction failed: {str(e)}")
        raise

def is_pdf_url(url: str) -> bool:
    """Check if URL likely points to a PDF file based on extension."""
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    return path.endswith('.pdf')

def process_pdf_page_chunk(reader, start_page, end_page):
    """Process a chunk of PDF pages in parallel"""
    text = []
    for page_num in range(start_page, min(end_page, len(reader.pages))):
        page = reader.pages[page_num]
        text.append(page.extract_text())
    return "\n\n".join(text)

@lru_cache(maxsize=100)
def extract_text_from_pdf(pdf_content: bytes) -> str:
    """Extract text from PDF content with caching and parallel processing."""
    try:
        pdf_file = io.BytesIO(pdf_content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        # Limit to fewer pages for performance if PDF is very large
        max_pages = min(len(pdf_reader.pages), MAX_PDF_PAGES)
        
        if max_pages <= 10:
            # For small PDFs, extract sequentially
            text = []
            for page_num in range(max_pages):
                page = pdf_reader.pages[page_num]
                text.append(page.extract_text())
            return "\n\n".join(text)
        else:
            # For larger PDFs, use parallel processing
            chunks = []
            chunk_size = PDF_EXTRACTION_CHUNK_SIZE
            
            # Submit parallel tasks for each chunk
            futures = []
            with concurrent.futures.ThreadPoolExecutor() as exec:
                for i in range(0, max_pages, chunk_size):
                    end = min(i + chunk_size, max_pages)
                    futures.append(exec.submit(process_pdf_page_chunk, pdf_reader, i, end))
            
            # Collect results
            chunks = [future.result() for future in futures]
            return "\n\n".join(chunks)
    except Exception as e:
        logger.error(f"Error extracting text from PDF: {str(e)}")
        raise

def get_random_user_agent():
    """Return a random realistic user agent string"""
    return random.choice(REALISTIC_USER_AGENTS)

async def human_like_scroll(page):
    """Scroll the page in a human-like manner with pauses and random speeds"""
    logger.info("Performing human-like scrolling")
    
    try:
        # Get page height
        page_height = await page.evaluate("() => document.body.scrollHeight")
        viewport_height = await page.evaluate("() => window.innerHeight")
        
        # No need to scroll for tiny pages
        if page_height <= viewport_height:
            logger.info("Page is shorter than viewport, no scrolling needed")
            return
        
        # Get random scroll steps (between 5-12)
        num_steps = random.randint(5, 12)
        
        # Calculate scroll positions with some jitter
        scroll_positions = []
        for i in range(1, num_steps + 1):
            # Add some randomness to each step
            target_position = int((i / num_steps) * page_height)
            jitter = random.randint(-30, 30)  # Add jitter
            position = max(0, min(page_height, target_position + jitter))
            scroll_positions.append(position)
        
        # Add a slight scroll up sometimes (humans do this)
        if random.random() < 0.3 and len(scroll_positions) > 2:
            idx = random.randint(1, len(scroll_positions) - 1)
            scroll_positions[idx] = max(0, scroll_positions[idx] - random.randint(50, 150))
        
        # Ensure the last scroll position reaches the bottom
        if scroll_positions and scroll_positions[-1] < page_height - 20:
            scroll_positions.append(page_height)
        
        # Execute the scrolling with random speeds
        for position in scroll_positions:
            # Random scroll speed (pixels per frame)
            scroll_speed = random.uniform(5, 20)
            
            # Get current scroll position
            current_position = await page.evaluate("() => window.scrollY")
            
            # Calculate number of frames needed for this scroll
            distance = abs(position - current_position)
            frames = max(1, int(distance / scroll_speed))
            
            # Scroll smoothly across frames
            for frame in range(1, frames + 1):
                # Easing function (start slow, speed up, then slow down)
                progress = frame / frames
                easing = 0.5 - 0.5 * math.cos(math.pi * progress)
                
                # Apply easing to get intermediate position
                if position > current_position:
                    # Scrolling down
                    intermediate = current_position + (distance * easing)
                else:
                    # Scrolling up
                    intermediate = current_position - (distance * easing)
                
                # Execute scroll
                await page.evaluate(f"window.scrollTo(0, {intermediate})")
                
                # Random tiny pause between frames
                await asyncio.sleep(random.uniform(0.005, 0.015))
            
            # Pause slightly at each major position
            await asyncio.sleep(random.uniform(0.3, 1.2))
            
            # 30% chance to have a longer pause (like reading)
            if random.random() < 0.3:
                await asyncio.sleep(random.uniform(1.0, 2.5))
                
        logger.info("Human-like scrolling completed")
        
    except Exception as e:
        logger.warning(f"Error during human-like scrolling: {str(e)}")
        # Fallback to basic scrolling
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.0)
        except Exception:
            logger.warning("Fallback scrolling also failed")

def extract_content_from_soup(soup):
    """Extract clean content from BeautifulSoup object"""
    # Remove script, style and hidden elements
    for element in soup(['script', 'style', 'meta', 'link', 'header', 'footer', 'nav']):
        element.decompose()
        
    # First try specific content areas
    content_ids = ['content', 'main', 'main-content', 'article', 'terms', 'privacy-policy', 'terms-of-service']
    for id in content_ids:
        content_div = soup.find(id=id) or soup.find('div', class_=id) or soup.find('article', class_=id)
        if content_div and len(content_div.get_text(strip=True)) > MIN_CONTENT_LENGTH:
            return content_div.get_text(separator='\n', strip=True)
    
    # If no specific content area found, get all paragraphs
    paragraphs = soup.find_all('p')
    if paragraphs:
        text = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 10])
        if len(text) > MIN_CONTENT_LENGTH:
            return text
    
    # If still no content, get all text
    body_text = soup.body.get_text(separator='\n', strip=True) if soup.body else ""
    
    return body_text

async def setup_stealth_browser(user_agent=None):
    """
    Set up a Playwright browser with stealth mode to avoid detection.
    Returns playwright instance, browser instance, and browser context.
    """
    if not user_agent:
        user_agent = get_random_user_agent()
    
    logger.info(f"Setting up stealth browser with UA: {user_agent}")
    
    try:
        # Initialize playwright
        playwright = await async_playwright().start()
        
        # Launch browser with stealth configurations
        browser = await playwright.chromium.launch(
            headless=False,  # Non-headless is better for avoiding detection
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials',
                '--disable-web-security',
                '--disable-dev-shm-usage',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--ignore-certificate-errors',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-service-autorun',
                '--password-store=basic',
                '--use-mock-keychain',
                '--disable-gpu',
                f'--user-agent={user_agent}'
            ]
        )
        
        # Create context with additional stealth settings
        context_options = {
            "viewport": {'width': 1920, 'height': 1080},
            "user_agent": user_agent,
            "ignore_https_errors": True,
            "locale": 'en-US',
            "timezone_id": 'America/New_York',
            "geolocation": {'latitude': 40.730610, 'longitude': -73.935242},  # NYC
            "permissions": ['geolocation'],
            "java_script_enabled": True,
            "accept_downloads": True,
            "has_touch": random.choice([True, False]),  # Randomize touch capability
        }
            
        context = await browser.new_context(**context_options)
        
        # Add stealth scripts
        await context.add_init_script("""
        () => {
            // Navigator overrides
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false
            });
            
            // Prevent fingerprinting
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    return [
                        {
                            0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},
                            description: "Portable Document Format",
                            filename: "internal-pdf-viewer",
                            length: 1,
                            name: "Chrome PDF Plugin"
                        },
                        {
                            0: {type: "application/pdf", suffixes: "pdf", description: "Portable Document Format"},
                            description: "Portable Document Format",
                            filename: "internal-pdf-viewer",
                            length: 1,
                            name: "Chrome PDF Viewer"
                        }
                    ];
                }
            });
            
            // Hide automation
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
            );
        }
        """)
        
        # Set extra HTTP headers that real browsers typically use
        await context.set_extra_http_headers({
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
        })
        
        return playwright, browser, context
        
    except Exception as e:
        logger.error(f"Error setting up stealth browser: {str(e)}")
        # Ensure cleanup in case of errors
        if 'playwright' in locals():
            await playwright.stop()
        raise

async def extract_with_playwright(url, document_type: str, url_to_return: str, retry_count=0, max_retries=MAX_RETRIES):
    """
    Extract content from JavaScript-heavy sites using Playwright with anti-bot evasion
    techniques. Special handling for Facebook URLs. Uses concurrency control and exponential backoff.
    """
    playwright = None
    browser = None
    context = None
    page = None
    
    # Extract domain for backoff tracking
    domain = urlparse(url).netloc
    is_facebook = 'facebook.com' in url.lower() or 'fb.com' in url.lower()
    
    # Check if domain is in backoff
    current_time = time.time()
    if domain in PLAYWRIGHT_BACKOFF_TIMES and PLAYWRIGHT_BACKOFF_TIMES[domain] > current_time:
        backoff_remaining = int(PLAYWRIGHT_BACKOFF_TIMES[domain] - current_time)
        logger.warning(f"Domain {domain} is in backoff period. {backoff_remaining}s remaining.")
        # Sleep a short time then raise exception to be handled by caller
        await asyncio.sleep(random.uniform(1.0, 3.0))
        raise Exception(f"Domain {domain} is currently in backoff period ({backoff_remaining}s remaining)")
    
    try:
        # Use semaphore to limit concurrent Playwright instances
        async with PLAYWRIGHT_SEMAPHORE:
            logger.info(f"Acquiring Playwright instance (currently using {3 - PLAYWRIGHT_SEMAPHORE._value} of 3 slots)")
            
            # Get a random user agent on retries
            user_agent = get_random_user_agent() if retry_count > 0 else None
            
            # Set up stealth browser
            playwright, browser, context = await setup_stealth_browser(user_agent)
            
            # Create new page
            page = await context.new_page()
            
            # Set referrer based on URL
            if is_facebook:
                await page.set_extra_http_headers({'Referer': 'https://www.google.com/'})
            else:
                await page.set_extra_http_headers({'Referer': 'https://www.bing.com/search?q=' + url.split('/')[2]})
            
            # Special handling for Facebook
            if is_facebook:
                logger.info(f"Handling Facebook URL: {url}")
                
                # First visit Facebook homepage
                await page.goto('https://www.facebook.com/', wait_until='networkidle')
                await asyncio.sleep(random.uniform(2.0, 4.0))
                
                # Visit some legitimate pages first (randomize to avoid patterns)
                legitimate_pages = [
                    'https://www.facebook.com/about/',
                    'https://www.facebook.com/policies/',
                    'https://www.facebook.com/privacy/policy/',
                    'https://www.facebook.com/help/'
                ]
                
                for _ in range(random.randint(1, 2)):
                    legit_page = random.choice(legitimate_pages)
                    await page.goto(legit_page, wait_until='networkidle')
                    await asyncio.sleep(random.uniform(1.5, 3.0))
                    await human_like_scroll(page)
                
                # Now go to the actual target URL
                logger.info(f"Navigating to target Facebook URL after prep: {url}")
                response = await page.goto(url, wait_until='domcontentloaded')
                
                # Handle potential login prompt
                login_selectors = [
                    'a[href*="login"]', 
                    'button:has-text("Log In")', 
                    'div[role="button"]:has-text("Not Now")',
                    '[data-testid="cookie-policy-manage-dialog-accept-button"]'
                ]
                
                for selector in login_selectors:
                    try:
                        if await page.is_visible(selector, timeout=3000):
                            logger.info(f"Found login/cookie prompt element: {selector}")
                            await page.click(selector)
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                    except Exception as e:
                        logger.debug(f"No {selector} found: {str(e)}")
                
                # Wait for content to load
                await asyncio.sleep(random.uniform(3.0, 5.0))
                
            else:
                # Non-Facebook sites
                logger.info(f"Navigating to URL: {url}")
                
                # Add random delay before navigation (bots don't wait)
                await asyncio.sleep(random.uniform(1.0, 3.5))
                
                # Navigate to the page
                try:
                    response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    
                    # Check response status
                    if not response or response.status >= 400:
                        logger.warning(f"Failed to load page, status: {response.status if response else 'no response'}")
                        # Set backoff for this domain on error
                        apply_exponential_backoff(domain, retry_count)
                        if retry_count < max_retries:
                            logger.info(f"Retrying ({retry_count + 1}/{max_retries})...")
                            # Calculate wait time based on retry count (exponential backoff)
                            wait_time = min(MAX_BACKOFF, INITIAL_BACKOFF * (2 ** retry_count)) * random.uniform(0.75, 1.25)
                            logger.info(f"Waiting {wait_time:.2f}s before retry")
                            await asyncio.sleep(wait_time)
                            await browser.close()
                            await playwright.stop()
                            return await extract_with_playwright(url, document_type, url_to_return, retry_count + 1, max_retries)
                except Exception as e:
                    logger.warning(f"Navigation error: {str(e)}")
                    # Set backoff for this domain on error
                    apply_exponential_backoff(domain, retry_count)
                    raise
            
            # Handle cookie consent (common in EU sites)
            cookie_button_texts = ["accept", "agree", "got it", "ok", "consent", "allow", "accept all"]
            for text in cookie_button_texts:
                try:
                    cookie_button = await page.get_by_role("button", name=re.compile(f"{text}", re.IGNORECASE)).first
                    if cookie_button:
                        await cookie_button.click()
                        logger.info(f"Clicked cookie consent button containing '{text}'")
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        break
                except Exception:
                    continue
            
            # Perform human-like scrolling
            await human_like_scroll(page)
            
            # Try to bypass login/paywall overlays
            await page.evaluate("""() => {
                // Remove common overlay selectors
                const overlaySelectors = [
                    '.modal', '.overlay', '.paywall', '.login-modal', '.signup-modal',
                    '[class*="modal"]', '[class*="overlay"]', '[class*="paywall"]',
                    '[id*="modal"]', '[id*="overlay"]', '[id*="paywall"]',
                    '[style*="z-index: 9999"]', '[style*="position: fixed"]'
                ];
                
                overlaySelectors.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => {
                        if (el.style.position === 'fixed' || 
                            el.style.zIndex > 100 || 
                            window.getComputedStyle(el).position === 'fixed') {
                            el.style.display = 'none';
                        }
                    });
                });
                
                // Also remove body fixed positioning and overflow
                if (document.body.style.overflow === 'hidden') {
                    document.body.style.overflow = 'auto';
                }
                if (document.body.style.position === 'fixed') {
                    document.body.style.position = 'static';
                }
                }""")
                
            # Wait a bit for any dynamic content
            await asyncio.sleep(random.uniform(1.0, 3.0))
            
            # Try to extract content using JS
            logger.info("Attempting to extract content via JavaScript")
            
            # Try multiple extraction methods
            extraction_methods = [
                # 1. Try specific content areas
                """() => {
                    const contentSelectors = [
                        'article', 
                        '#content', 
                        '#main', 
                        '#main-content', 
                        '.content', 
                        '.main', 
                        '.article', 
                        '[role="main"]',
                        '.terms-of-service',
                        '.terms',
                        '.privacy-policy',
                        '.tos',
                        '.legal'
                    ];
                    
                    for (const selector of contentSelectors) {
                        const element = document.querySelector(selector);
                        if (element && element.textContent.trim().length > 1000) {
                            return element.innerText;
                        }
                    }
                    return null;
                }""",
                
                # 2. Try to extract main content area using heuristics
                """() => {
                    // Find all divs and other content containers
                    const containers = Array.from(document.querySelectorAll('div, section, main, article'));
                    
                    // Sort by content length
                    const sortedContainers = containers
                        .map(container => ({
                            element: container,
                            textLength: container.innerText.trim().length,
                            childCount: container.children.length
                        }))
                        .filter(item => item.textLength > 1000) // Must have substantial text
                        .sort((a, b) => b.textLength - a.textLength); // Sort by most text
                    
                    if (sortedContainers.length > 0) {
                        // Take the container with the most text
                        return sortedContainers[0].element.innerText;
                    }
                    
                    return null;
                }""",
                
                # 3. Extract all body text as fallback
                """() => {
                    // Remove script, style, nav, header, footer elements which usually don't contain main content
                    const elementsToRemove = ['script', 'style', 'nav', 'header', 'footer', 'iframe'];
                    for (const tag of elementsToRemove) {
                        const elements = document.querySelectorAll(tag);
                        for (const el of elements) {
                            if (el.parentNode) {
                                el.parentNode.removeChild(el);
                            }
                        }
                    }
                    
                    // Get body content
                    if (document.body) {
                        return document.body.innerText;
                    }
                    
                    return null;
                }"""
            ]
            
            js_content = None
            for i, method in enumerate(extraction_methods):
                try:
                    result = await page.evaluate(method)
                    if result and len(result.strip()) > MIN_CONTENT_LENGTH:
                        js_content = result
                        logger.info(f"Successfully extracted content using JavaScript method {i+1} (length: {len(js_content)})")
                        break
                except Exception as e:
                    logger.warning(f"JS extraction method {i+1} failed: {str(e)}")
            
            # If we got content via JavaScript, return it
            if js_content:
                # Return the successfully extracted content
                return ExtractResponse(
                    url=url_to_return,
                    document_type=document_type,
                    text=js_content,
                    success=True,
                    message="Successfully extracted text using JavaScript",
                    method_used="playwright_js"
                )
                
            # No content found via JavaScript methods
            logger.warning("JavaScript extraction methods failed to find content")
            
            # Fallback to HTML extraction
            try:
                html_content = await page.content()
                if html_content:
                    soup = BeautifulSoup(html_content, 'html.parser')
                    
                    # Clean up the HTML
                    for script in soup(["script", "style", "nav", "header", "footer"]):
                        script.extract()
                    
                    # Extract text
                    text = soup.get_text(separator="\n", strip=True)
                    if text and len(text.strip()) > MIN_CONTENT_LENGTH:
                        logger.info(f"Extracted content using HTML fallback (length: {len(text)})")
                        return ExtractResponse(
                            url=url_to_return,
                            document_type=document_type,
                            text=text,
                            success=True,
                            message="Successfully extracted text using HTML fallback",
                            method_used="playwright_html"
                        )
            except Exception as e:
                logger.warning(f"HTML fallback extraction failed: {str(e)}")
                
            # Failed to extract any meaningful content
            return ExtractResponse(
                url=url_to_return,
                document_type=document_type,
                text=None,
                success=False,
                message="Failed to extract meaningful content using Playwright",
                method_used="playwright_failed"
            )
                
    except Exception as e:
        logger.error(f"Error extracting content with Playwright: {str(e)}")
        # Apply backoff on error
        apply_exponential_backoff(domain, retry_count)
        
        if retry_count < max_retries:
            logger.info(f"Retrying ({retry_count + 1}/{max_retries})...")
            # Cleanup before retry
            if page:
                await page.close()
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()
            
            # Calculate wait time based on retry count (exponential backoff)
            wait_time = min(MAX_BACKOFF, INITIAL_BACKOFF * (2 ** retry_count)) * random.uniform(0.75, 1.25)
            logger.info(f"Waiting {wait_time:.2f}s before retry")
            await asyncio.sleep(wait_time)
            
            # Retry with incremented count
            return await extract_with_playwright(url, document_type, url_to_return, retry_count + 1, max_retries)
        
        # Return a failed response after all retries
        return ExtractResponse(
            url=url_to_return,
            document_type=document_type,
            text="",
            success=False,
            message=f"Failed to extract content after {max_retries} retries: {str(e)}",
            method_used="playwright"
        )
        
    finally:
        # Ensure cleanup
        if page:
            await page.close()
        if browser:
                await browser.close()
        if playwright:
            await playwright.stop()

def apply_exponential_backoff(domain: str, retry_count: int):
    """Apply exponential backoff to a domain based on retry count"""
    # Calculate backoff time: initial * 2^retry_count with some randomness
    backoff_time = min(MAX_BACKOFF, INITIAL_BACKOFF * (2 ** retry_count)) * random.uniform(0.75, 1.25)
    
    # Only increase backoff if it would be longer than current
    current_backoff = PLAYWRIGHT_BACKOFF_TIMES.get(domain, 0) - time.time()
    if current_backoff < backoff_time:
        PLAYWRIGHT_BACKOFF_TIMES[domain] = time.time() + backoff_time
        logger.warning(f"Domain {domain} backoff set for {backoff_time:.2f}s")
    
    # Clean up old entries periodically
    if random.random() < 0.1:  # 10% chance to clean up on each call
        clean_backoff_times()

def clean_backoff_times():
    """Remove expired backoff times"""
    current_time = time.time()
    expired = [domain for domain, expire_time in PLAYWRIGHT_BACKOFF_TIMES.items() 
               if expire_time <= current_time]
    
    for domain in expired:
        del PLAYWRIGHT_BACKOFF_TIMES[domain]
    
    if expired:
        logger.info(f"Cleaned up {len(expired)} expired domain backoffs") 