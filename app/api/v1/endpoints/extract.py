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

# Enhanced in-memory cache with TTL and size limit
CACHE = {}
CACHE_TTL = 3600  # Cache expiry in seconds (1 hour)
MAX_CACHE_SIZE = 500  # Maximum number of items in cache

# Optimized timeouts
STANDARD_TIMEOUT = 10  # Reduced from 15 seconds
PLAYWRIGHT_TIMEOUT = 15000  # Reduced from 20000 ms
URL_DISCOVERY_TIMEOUT = 8  # Reduced from 10 seconds

# Extraction constants
MIN_CONTENT_LENGTH = 100  # Minimum characters for valid content
MAX_PDF_PAGES = 30  # Reduced from 50 pages
PDF_EXTRACTION_CHUNK_SIZE = 5  # Number of pages to process in parallel

# ThreadPoolExecutor for CPU-bound tasks
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

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
        
        document_url = ""
        document_type = request.document_type or "tos"
        
        # Check cache before processing
        cache_key = f"{url}:{document_type}"
        cached_result = get_from_cache(cache_key)
        if cached_result:
            return ExtractResponse(**cached_result)
        
        # Find the legal document URL if document_type is provided
        if document_type in ["tos", "pp"]:
            logger.info(f"Finding {document_type} URL for {url}")
            
            document_url, success, error_message = await find_document_url(url, document_type, original_url)
            
            if not success:
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
        else:
            # If no document_type specified, use the provided URL directly
            extraction_url = url
            document_url = url
            # Default to "tos" as document_type since we need to use a valid Literal value
            document_type = "tos"
            url_to_return = url
        
        # Check cache again with the resolved document URL
        doc_cache_key = f"{extraction_url}:{document_type}"
        cached_doc_result = get_from_cache(doc_cache_key)
        if cached_doc_result:
            return ExtractResponse(**cached_doc_result)
        
        # Determine if URL is likely a PDF
        is_pdf = is_pdf_url(extraction_url)
        
        # Concurrent extraction strategy
        # Create tasks for different extraction methods based on URL type
        tasks = []
        
        if is_pdf:
            # For PDFs, only use PDF extraction
            tasks.append(asyncio.create_task(extract_pdf(extraction_url, document_type, url_to_return)))
        else:
            # For HTML content, try both methods concurrently
            tasks.append(asyncio.create_task(extract_standard_html(extraction_url, document_type, url_to_return)))
            tasks.append(asyncio.create_task(extract_with_playwright(extraction_url, document_type, url_to_return)))
        
        # Wait for the first successful extraction or all to fail
        for i, completed_task in enumerate(asyncio.as_completed(tasks)):
            try:
                result = await completed_task
                if result.success and result.text:
                    # Cancel other tasks as we've got a successful result
                    for j, task in enumerate(tasks):
                        if j != i and not task.done():
                            task.cancel()
                    
                    # Cache successful result
                    add_to_cache(doc_cache_key, result.dict())
                    return result
            except Exception as e:
                logger.warning(f"Extraction task failed: {str(e)}")
                continue
        
        # If all strategies failed
        return ExtractResponse(
            url=url_to_return,
            document_type=document_type,
            text=None,
            success=False,
            message="Failed to extract text content after trying all methods",
            method_used="standard"
        )
            
    except Exception as e:
        logger.error(f"Error in extraction: {str(e)}")
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
        # Enhanced browser-like headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
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
        # Enhanced browser-like headers for HTML content
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
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
        extracted_text = extract_content_from_soup(soup, extraction_url)
        
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

def extract_content_from_soup(soup, url="") -> str:
    """Extract and clean up text content from a BeautifulSoup object."""
    try:
        # Check if the URL is from Facebook
        is_facebook = "facebook.com" in url.lower() if url else False
        
        # Remove script and style elements
        for script_or_style in soup(['script', 'style', 'header', 'footer', 'nav']):
            script_or_style.extract()
        
        # Try to find the main content
        main_content = None
        
        # Look for common content containers - prioritize the most likely containers
        content_selectors = [
            'article', 'main', 'div[role="main"]', 
            '.content', '.main-content', '#content', '#main',
            '.terms', '.terms-content', '.privacy-policy', '.legal'
        ]
        
        # Add Facebook-specific selectors if it's a Facebook URL
        if is_facebook:
            facebook_selectors = [
                'div.xeuugli',
                'div._1xgd',
                'div[data-pagelet="MainFeed"]',
                '#globalContainer',
                'div.x1qjc9v5'
            ]
            content_selectors = facebook_selectors + content_selectors
        
        # Try each selector in order of likelihood
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                # Use the largest matching element
                main_content = max(elements, key=lambda elem: len(elem.get_text(strip=True)))
                if len(main_content.get_text(strip=True)) > 500:  # If it has substantial content
                    break
        
        # If we couldn't find a clear main content, try alternative extraction methods
        if not main_content or len(main_content.get_text(strip=True)) < 500:
            # For Facebook, try extracting from all paragraphs
            if is_facebook:
                paragraphs = soup.find_all('p')
                if paragraphs and len(paragraphs) > 5:  # Ensure there are enough paragraphs
                    # Join all paragraphs with substantial content
                    text = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 15])
                    if len(text) > 500:  # If we extracted enough content
                        return text
                
                # Try extracting from div elements with substantial text
                divs = soup.find_all('div')
                text_divs = [div for div in divs if len(div.get_text(strip=True)) > 100 and len(div.find_all('div')) < 5]
                if text_divs:
                    # Use the largest text div
                    main_content = max(text_divs, key=lambda elem: len(elem.get_text(strip=True)))
                    if len(main_content.get_text(strip=True)) > 500:
                        # Process this main content below
                        pass
                    else:
                        main_content = None
        
        # If we still couldn't find content, use the body
        if not main_content or len(main_content.get_text(strip=True)) < 500:
            main_content = soup.body
        
        if not main_content:
            main_content = soup
            
        # Use optimized strategy for text extraction based on content size
        main_text = main_content.get_text(strip=True)
        
        # For Facebook, use a more aggressive approach to extract text
        if is_facebook and len(main_text) < 1000:
            # Try to get all text from the document instead
            text = soup.get_text(separator='\n', strip=True)
            
            # Filter out short lines that are likely navigation or UI elements
            lines = text.split('\n')
            relevant_lines = [line for line in lines if len(line) > 40 or (len(line) > 15 and line.endswith('.'))]
            
            if relevant_lines:
                text = '\n'.join(relevant_lines)
                if len(text) > 500:
                    # Post-processing
                    # Remove excessive whitespace 
                    text = re.sub(r'\n\s*\n', '\n\n', text)
                    # Remove URLs that appear as plain text
                    text = re.sub(r'https?://\S+', '', text)
                    return text.strip()
        
        # Standard extraction path
        if len(main_text) < 5000:
            # For smaller content, use direct text extraction
            text = main_content.get_text(separator='\n', strip=True)
        else:
            # For larger content, use html2text which is more memory efficient
            text = html2text(str(main_content))
        
        # Post-processing - simplified for performance
        # Remove excessive whitespace 
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        # Remove URLs that appear as plain text
        text = re.sub(r'https?://\S+', '', text)
        
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting content from soup: {str(e)}")
        # Return empty string on failure, let the calling function handle it
        return ""

async def extract_with_playwright(url: str, document_type: str, url_to_return: str) -> ExtractResponse:
    """Extract text using Playwright for JavaScript-heavy sites"""
    logger.info(f"Attempting Playwright extraction for: {url}")
    try:
        # Use Playwright only when necessary - with optimized settings
        async with async_playwright() as p:
            # Launch browser with minimal settings
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--disable-extensions']
            )
            
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
                javascript_enabled=True,
            )
            
            # Set timeout for the entire context
            context.set_default_timeout(PLAYWRIGHT_TIMEOUT)
            
            page = await context.new_page()
            
            try:
                # Extend timeout for complex pages
                longer_timeout = PLAYWRIGHT_TIMEOUT * 1.5
                await page.goto(url, wait_until="networkidle", timeout=longer_timeout)
                
                # Detect if this is a Facebook page
                is_facebook = "facebook.com" in url.lower()
                
                # Special handling for Facebook
                if is_facebook:
                    # Wait for specific Facebook content to load
                    logger.info("Applying Facebook-specific extraction strategy")
                    
                    # Wait longer for Facebook's dynamic content to load
                    await asyncio.sleep(2)
                    
                    # Check if this is the AI Terms page
                    is_ai_terms = "ai-terms" in url.lower() or "ai/terms" in url.lower()
                    if is_ai_terms:
                        logger.info("Detected Facebook AI Terms page - applying specialized extraction")
                        # Special handling for AI Terms page
                        await page.evaluate("""
                            // Attempt to expand any collapsed sections
                            document.querySelectorAll('button, [role="button"]').forEach(btn => {
                                if (btn.textContent.includes('See more') || 
                                    btn.textContent.includes('Read more') ||
                                    btn.textContent.includes('Expand')) {
                                    btn.click();
                                }
                            });
                        """)
                        await asyncio.sleep(1)  # Wait for expansions
                    
                    # Full page scroll with multiple pauses to ensure all content loads
                    for scroll_pos in [0.3, 0.6, 1.0]:
                        await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_pos})")
                        await asyncio.sleep(0.5)
                    
                    # Try to locate and interact with content containers specific to Facebook
                    for selector in [
                        "div[role='main']", 
                        "article", 
                        ".xeuugli", # Facebook legal content class
                        "._1xgd", # Another Facebook content class
                        "#content",
                        ".legal-content"
                    ]:
                        try:
                            elements = await page.query_selector_all(selector)
                            if elements and len(elements) > 0:
                                logger.info(f"Found Facebook content container: {selector}")
                                break
                        except Exception:
                            continue
                else:
                    # Quick scroll to load lazy content for non-Facebook sites
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    await asyncio.sleep(0.5)  # Reduced wait time
                
                # Get the page content
                content = await page.content()
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # For Facebook, try additional content extraction strategies
                if is_facebook:
                    extracted_text = ""
                    
                    # First try specific Facebook content containers
                    for selector in [
                        "div.xeuugli", # Facebook legal content class
                        "div._1xgd", # Another Facebook content class
                        "div[role='main']",
                        "article", 
                        "#content", 
                        ".legal-content"
                    ]:
                        elements = soup.select(selector)
                        if elements:
                            # Use the largest matching element
                            main_content = max(elements, key=lambda elem: len(elem.get_text(strip=True)))
                            potential_text = main_content.get_text(separator='\n', strip=True)
                            if len(potential_text) > MIN_CONTENT_LENGTH:
                                extracted_text = potential_text
                                logger.info(f"Extracted Facebook content using selector: {selector}")
                                break
                    
                    # If specific selectors didn't work, try paragraph extraction
                    if not extracted_text or len(extracted_text) < MIN_CONTENT_LENGTH:
                        paragraphs = soup.find_all('p')
                        if paragraphs:
                            extracted_text = '\n\n'.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 15])
                            logger.info("Extracted Facebook content using paragraph tags")
                            
                    # If paragraph extraction didn't work, try all text extraction
                    if not extracted_text or len(extracted_text) < MIN_CONTENT_LENGTH:
                        # Fall back to standard extraction
                        extracted_text = extract_content_from_soup(soup, url)
                else:
                    # Standard extraction for non-Facebook sites
                    extracted_text = extract_content_from_soup(soup, url)
                
                if extracted_text and len(extracted_text.strip()) > MIN_CONTENT_LENGTH:
                    return ExtractResponse(
                        url=url_to_return,
                        document_type=document_type,
                        text=extracted_text,
                        success=True,
                        message="Successfully extracted text content using JavaScript-enabled browser rendering",
                        method_used="playwright"
                    )
                    
                raise Exception("Playwright extraction didn't yield sufficient content")
            finally:
                await browser.close()
    except Exception as e:
        logger.warning(f"Playwright extraction failed: {str(e)}")
        raise 