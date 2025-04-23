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

def extract_content_from_soup(soup) -> str:
    """Extract and clean up text content from a BeautifulSoup object."""
    try:
        logger.info(f"Starting content extraction from soup")
        
        # Remove script and style elements
        for script_or_style in soup(['script', 'style', 'header', 'footer', 'nav']):
            script_or_style.extract()
        
        # Try to find the main content
        main_content = None
        
        # Look for common content containers - prioritize the most likely containers
        content_selectors = [
            'article', 'main', 'div[role="main"]', 
            '.content', '.main-content', '#content', '#main',
            '.terms', '.terms-content', '.privacy-policy', '.legal',
            '.legal-text', '#terms', '#tos', '.tos'
        ]
        
        logger.debug(f"Looking for content containers using selectors")
        # Try each selector in order of likelihood
        for selector in content_selectors:
            elements = soup.select(selector)
            if elements:
                # Use the largest matching element
                main_content = max(elements, key=lambda elem: len(elem.get_text(strip=True)))
                text_length = len(main_content.get_text(strip=True))
                logger.info(f"Found content with selector {selector} - length: {text_length}")
                if text_length > 500:  # If it has substantial content
                    break
        
        # If we couldn't find a clear main content, use the body
        if not main_content or len(main_content.get_text(strip=True)) < 500:
            logger.info("No main content container found, falling back to body")
            main_content = soup.body
        
        if not main_content:
            logger.warning("No body element found, using entire soup")
            main_content = soup
        
        # Check if we have any content at all
        if not main_content:
            logger.error("No content found in HTML")
            return ""
            
        # Use optimized strategy for text extraction based on content size
        main_text = main_content.get_text(strip=True)
        logger.info(f"Raw text length before processing: {len(main_text)}")
        
        if len(main_text) < 5000:
            # For smaller content, use direct text extraction
            text = main_content.get_text(separator='\n', strip=True)
            logger.info(f"Used direct text extraction - result length: {len(text)}")
        else:
            # For larger content, use html2text which is more memory efficient
            text = html2text(str(main_content))
            logger.info(f"Used html2text - result length: {len(text)}")
        
        # Post-processing - simplified for performance
        # Remove excessive whitespace 
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        # Remove URLs that appear as plain text
        text = re.sub(r'https?://\S+', '', text)
        
        final_text = text.strip()
        logger.info(f"Final extracted text length: {len(final_text)}")
        
        # Preview the first 100 characters
        if final_text:
            preview = final_text[:100] + "..." if len(final_text) > 100 else final_text
            logger.info(f"Text preview: {preview}")
        
        return final_text
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
            )
            
            # Set timeout for the entire context
            context.set_default_timeout(PLAYWRIGHT_TIMEOUT)
            
            page = await context.new_page()
            
            try:
                # Navigate to the page with improved waiting strategy
                logger.info(f"Navigating to {url}")
                response = await page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT)
                
                if not response:
                    logger.warning(f"Failed to load page: {url}")
                    raise Exception("Page load failed")
                
                logger.info(f"Page loaded with status: {response.status}")
                
                # Close any cookie consent dialogs or popups that might appear
                popup_buttons = [
                    'button:text-is("Accept All")', 
                    'button:text-is("Accept")', 
                    'button:text-is("Agree")', 
                    'button:text-is("I Agree")', 
                    'button:text-is("OK")',
                    'button:text-is("Continue")',
                    '[aria-label="Accept cookies"]',
                    '[aria-label="Accept all cookies"]',
                    '[data-cookiebanner="accept_button"]',
                    '[data-testid="cookie-policy-dialog-accept-button"]'
                ]
                
                logger.info("Looking for cookie/consent popups to dismiss")
                for selector in popup_buttons:
                    try:
                        if await page.locator(selector).count() > 0:
                            logger.info(f"Found popup button matching '{selector}', clicking it")
                            await page.locator(selector).first.click(timeout=3000)
                            await asyncio.sleep(1)  # Wait for popup to close
                    except Exception as e:
                        logger.debug(f"Button click attempt failed for '{selector}': {str(e)}")
                
                # Check for login walls that might be blocking content
                if await page.locator('text="Log In"').count() > 0 or await page.locator('text="Sign Up"').count() > 0:
                    logger.info("Detected a possible login wall, looking for alternative content")
                    
                    # Try to find the main content despite the login wall
                    # Sometimes content is still loaded but obscured by login prompts
                    await page.evaluate("""
                        // Try to remove login overlays
                        document.querySelectorAll('[role="dialog"]').forEach(e => e.remove());
                        document.querySelectorAll('.login-overlay').forEach(e => e.remove());
                        document.querySelectorAll('.signup-overlay').forEach(e => e.remove());
                    """)
                
                # Perform progressive scrolling to ensure dynamic content loads
                logger.info("Performing progressive scrolling")
                for scroll_position in [0.3, 0.6, 1.0]:
                    await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_position})")
                    await asyncio.sleep(0.7)
                
                # Wait for network to be idle
                try:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                    logger.info("Network is idle")
                except Exception as e:
                    logger.warning(f"Network idle timeout: {str(e)}")
                
                # Try different approaches to get the content
                
                # 1. Try to get inner text of specific elements first
                logger.info("Attempting targeted content extraction")
                targeted_content = await page.evaluate("""() => {
                    // Prioritized list of selectors for legal content
                    const selectors = [
                        '#terms-content', '.legal-content', '.terms-content', '.privacy-content',
                        'article', 'main', '.terms', '.legal', '#legal', '#terms',
                        'div[role="main"]', '.content', '.main-content', '#content', '#main'
                    ];
                    
                    for (const selector of selectors) {
                        const element = document.querySelector(selector);
                        if (element && element.innerText && element.innerText.length > 500) {
                            return element.innerText;
                        }
                    }
                    
                    // If we couldn't find a specific container, try the body excluding navigation
                    if (document.body) {
                        // Clone body to avoid modifying the actual page
                        const clone = document.body.cloneNode(true);
                        
                        // Remove navigation, headers, footers from the clone
                        ['nav', 'header', 'footer', 'script', 'style'].forEach(tag => {
                            clone.querySelectorAll(tag).forEach(el => el.remove());
                        });
                        
                        return clone.innerText;
                    }
                    
                    return null;
                }""")
                
                # 2. Also get all visible text from the page as a backup
                logger.info("Attempting full page text extraction")
                full_page_text = await page.evaluate("""() => {
                    function getVisibleText(node) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            return node.textContent;
                        }
                        
                        // Skip invisible elements
                        const style = window.getComputedStyle(node);
                        if (style.display === 'none' || style.visibility === 'hidden' || 
                            style.opacity === '0' || node.offsetHeight === 0) {
                            return '';
                        }
                        
                        // Skip navigation and other non-content elements
                        if (node.tagName === 'NAV' || node.tagName === 'HEADER' || 
                            node.tagName === 'FOOTER' || node.tagName === 'SCRIPT' || 
                            node.tagName === 'STYLE') {
                            return '';
                        }
                        
                        let text = '';
                        for (const child of node.childNodes) {
                            if (child.nodeType === Node.TEXT_NODE) {
                                text += child.textContent.trim() + ' ';
                            } else if (child.nodeType === Node.ELEMENT_NODE) {
                                text += getVisibleText(child);
                            }
                        }
                        return text;
                    }
                    
                    return getVisibleText(document.body);
                }""")
                
                # 3. Get the HTML for BeautifulSoup parsing as another approach
                html_content = await page.content()
                
                # Process the content from the different methods
                logger.info("Processing extracted content")
                
                extracted_text = ""
                
                # Try BeautifulSoup parsing first
                if html_content:
                    logger.info("Parsing HTML with BeautifulSoup")
                    soup = BeautifulSoup(html_content, 'html.parser')
                    extracted_text = extract_content_from_soup(soup)
                
                # If BeautifulSoup didn't yield good results, try the targeted content
                if not extracted_text or len(extracted_text.strip()) < MIN_CONTENT_LENGTH:
                    if targeted_content and len(targeted_content.strip()) > MIN_CONTENT_LENGTH:
                        logger.info(f"Using targeted content (length: {len(targeted_content.strip())})")
                        extracted_text = targeted_content.strip()
                
                # If targeted content didn't work, try the full page text
                if not extracted_text or len(extracted_text.strip()) < MIN_CONTENT_LENGTH:
                    if full_page_text and len(full_page_text.strip()) > MIN_CONTENT_LENGTH:
                        logger.info(f"Using full page text (length: {len(full_page_text.strip())})")
                        extracted_text = full_page_text.strip()
                
                if extracted_text and len(extracted_text.strip()) > MIN_CONTENT_LENGTH:
                    logger.info("Successfully extracted text with Playwright")
                    return ExtractResponse(
                        url=url_to_return,
                        document_type=document_type,
                        text=extracted_text,
                        success=True,
                        message="Successfully extracted text content using JavaScript-enabled browser rendering",
                        method_used="playwright"
                    )
                
                # If still no content, take a screenshot to help debug
                logger.warning("Playwright extraction didn't yield sufficient content")
                raise Exception("Playwright extraction didn't yield sufficient content")
            finally:
                await browser.close()
    except Exception as e:
        logger.warning(f"Playwright extraction failed: {str(e)}")
        raise 