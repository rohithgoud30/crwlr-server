import warnings
import re
import time
import io
import math
import hashlib
import random
import logging
import asyncio
import requests
import concurrent.futures
from urllib.parse import urlparse
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from fastapi import APIRouter, Response
from functools import lru_cache
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext
import os
import sys
import brotli

# Fixed version with improved resource management and error handling

from app.models.extract import ExtractRequest, ExtractResponse
from app.models.tos import ToSRequest
from app.models.privacy import PrivacyRequest
from app.api.v1.endpoints.tos import find_tos
from app.api.v1.endpoints.privacy import find_privacy_policy

# Suppress XML parsed-as-HTML warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
logger = logging.getLogger(__name__)
router = APIRouter()

# Define consistent user agent
CONSISTENT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

# PlaywrightManager singleton for headful browser reuse


class PlaywrightManager:
    def __init__(self, max_instances: int = 3):
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.semaphore = asyncio.Semaphore(max_instances)
        self.active_pages = set()  # Track active pages to ensure cleanup
        self.last_cleanup = time.time()
        self.cleanup_interval = 300  # Clean unused tabs every 5 minutes
        self.startup_complete = False
        self.startup_failure = None

    async def startup(self):
        """Start the browser with improved error handling for containerized environments"""
        # Don't try to start if we've already failed
        if self.startup_failure:
            logger.warning(f"Not attempting browser startup due to previous failure: {self.startup_failure}")
            raise RuntimeError(f"Browser startup previously failed: {self.startup_failure}")
        
        logger.info("Launching Playwright browser...")
        try:
            # Get Chrome executable path from environment if provided
            chrome_path = os.environ.get("CHROME_PATH", None)
            logger.info(f"Using Chrome executable path: {chrome_path if chrome_path else 'Default'}")
            
            # Start the Playwright process
            logger.info("Attempting to start Playwright process...")
            self.playwright = await async_playwright().start()
            logger.info("Playwright process started successfully")
            
            # Browser launch arguments for containerized environment
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",                        # Required in containerized environments
                "--disable-dev-shm-usage",            # Overcome limited resource in containers
                "--disable-gpu",                       # Disable GPU acceleration
                "--disable-setuid-sandbox",            # Additional sandbox protection
                "--single-process",                    # Use single process (helpful in containers)
                "--ignore-certificate-errors",         # Ignore SSL issues
                "--disable-accelerated-2d-canvas",     # Disable canvas acceleration 
                "--disable-accelerated-video-decode",  # Disable video acceleration
                "--disable-web-security"               # Disable web security for testing
            ]
            
            # Log browser launch details
            logger.info(f"Launching browser with args: {browser_args}")
            
            # ---> ADDED: More logging around browser launch
            logger.info("Attempting to launch browser...")
            # Launch the browser with appropriate arguments
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=browser_args,
                executable_path=chrome_path,
                timeout=60000,  # 60 second timeout for browser launch
            )
            logger.info("Browser launched successfully")
            
            # Create a new browser context
            # ---> ADDED: More logging around context creation
            logger.info("Attempting to create browser context...")
            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
                locale="en-US",
                timezone_id="America/New_York",
                user_agent=CONSISTENT_USER_AGENT
            )
            logger.info("Browser context created successfully")
            
            # Inject minimal stealth script
            # ---> ADDED: More logging around init script
            logger.info("Attempting to add init script...")
            await self.context.add_init_script(
                """
            () => {
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            }
            """
            )
            logger.info("Stealth script injected successfully")
            
            # Create test page to verify everything is working
            # ---> ADDED: More logging around test page
            logger.info("Attempting to create test page...")
            test_page = await self.context.new_page()
            logger.info("Test page created. Attempting to navigate...")
            await test_page.goto("about:blank")
            logger.info("Test page navigation successful. Attempting to close...")
            await test_page.close()
            logger.info("Test page created and closed successfully")

            # --- MODIFICATION START ---
            # Close the initial non-headless browser context and browser after the check
            logger.info("Closing initial non-headless browser after startup check...")
            if self.context:
                await self.context.close()
                self.context = None
            if self.browser:
                await self.browser.close()
                self.browser = None
            logger.info("Initial non-headless browser closed.")

            # Now, re-launch the browser in headless mode for actual use
            logger.info("Re-launching browser in headless mode for operational use...")
            self.browser = await self.playwright.chromium.launch(
                headless=True, # Use headless=True now
                args=browser_args,
                executable_path=chrome_path,
                timeout=60000,
            )
            logger.info("Headless browser launched successfully.")

            # Re-create the browser context for headless operation
            logger.info("Attempting to create headless browser context...")
            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                ignore_https_errors=True,
                locale="en-US",
                timezone_id="America/New_York",
                user_agent=CONSISTENT_USER_AGENT
            )
            logger.info("Headless browser context created successfully.")

            # Re-apply the init script to the new headless context
            logger.info("Attempting to add init script to headless context...")
            await self.context.add_init_script(
                """
            () => {
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            }
            """
            )
            logger.info("Stealth script injected into headless context successfully.")
            # --- MODIFICATION END ---
            
            # Mark startup as complete
            self.startup_complete = True
            logger.info("PlaywrightManager ready and operational (headless)")
            return True
            
        except Exception as e:
            # ---> MODIFIED: Log the full traceback for startup errors
            logger.error(f"Error during browser startup: {str(e)}", exc_info=True) # Add exc_info=True
            self.startup_failure = str(e)
            # Try to clean up any partial initialization
            await self._cleanup_on_failure()
            raise

    async def _cleanup_on_failure(self):
        """Clean up resources after a failed startup"""
        logger.info("Cleaning up after failed browser startup")
        try:
            if self.context:
                await self.context.close()
                self.context = None
                
            if self.browser:
                await self.browser.close()
                self.browser = None
                
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
        except Exception as e:
            logger.error(f"Error during cleanup after failed startup: {str(e)}")

    async def get_page(self):
        """Get a new browser page with semaphore control and fallback mechanism"""
        if not self.startup_complete:
            logger.error("Cannot get page - browser not initialized")
            raise RuntimeError("Browser not initialized")
            
        await self.semaphore.acquire()
        try:
            page = await self.context.new_page()
            self.active_pages.add(page)
            
            # Check if we need to clean up unused tabs
            current_time = time.time()
            if current_time - self.last_cleanup > self.cleanup_interval:
                await self.cleanup_stale_pages()
                
            return page
        except Exception as e:
            # Release semaphore on error
            self.semaphore.release()
            logger.error(f"Error getting browser page: {str(e)}")
            raise

    async def release_page(self, page):
        """Release a page back to the pool"""
        try:
            if page in self.active_pages:
                self.active_pages.remove(page)
            await page.close()
        except Exception as e:
            logger.error(f"Error closing page: {str(e)}")
        finally:
            self.semaphore.release()

    async def cleanup_stale_pages(self):
        """Close any stale pages that might have been left open"""
        try:
            logger.info(
                f"Checking for stale browser pages. Active pages count: {len(self.active_pages)}"
            )
            if self.context:
                pages = self.context.pages
                for page in pages:
                    if page not in self.active_pages:
                        logger.info("Closing stale browser page")
                        try:
                            await page.close()
                        except Exception as e:
                            logger.warning(f"Error closing stale page: {str(e)}")
            self.last_cleanup = time.time()
        except Exception as e:
            logger.error(f"Error during stale page cleanup: {str(e)}")

    async def shutdown(self):
        """Shut down the browser"""
        logger.info("Shutting down Playwright browser...")
        try:
            # Close all active pages first
            for page in list(self.active_pages):
                try:
                    await page.close()
                except Exception as e:
                    logger.warning(f"Error closing page during shutdown: {str(e)}")
            self.active_pages.clear()

            if self.context:
                await self.context.close()
                self.context = None
                
            if self.browser:
                await self.browser.close()
                self.browser = None
                
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
                
            self.startup_complete = False
            logger.info("PlaywrightManager shut down.")
        except Exception as e:
            logger.error(f"Error during browser shutdown: {str(e)}")


# Instantiate and plan to call startup/shutdown in app main
auth_manager = PlaywrightManager()

# Cache and settings
CACHE = {}
CACHE_TTL = 3600
MAX_CACHE_SIZE = 500
STANDARD_TIMEOUT = 15
URL_DISCOVERY_TIMEOUT = 12
MIN_CONTENT_LENGTH = 100
MAX_PDF_PAGES = 30
PDF_CHUNK = 5
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Replace random user agent function with consistent one
def get_user_agent():
    """
    Returns a consistent user agent string.
    """
    return CONSISTENT_USER_AGENT

# URL sanitization


def sanitize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return url


# Cache helpers


def get_from_cache(key: str):
    entry = CACHE.get(key)
    if entry and time.time() < entry["expires"]:
        return entry["value"]
    return None


def add_to_cache(key: str, value: dict):
    if len(CACHE) >= MAX_CACHE_SIZE:
        oldest = sorted(CACHE.items(), key=lambda kv: kv[1]["expires"])[0][0]
        del CACHE[oldest]
    CACHE[key] = {"value": value, "expires": time.time() + CACHE_TTL}


# PDF detection & extraction


def is_pdf_url(url: str) -> bool:
    return url.lower().endswith(".pdf")


@lru_cache(maxsize=100)
def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = io.BytesIO(pdf_bytes)
    pdf = __import__("PyPDF2").PdfReader(reader)
    pages = min(len(pdf.pages), MAX_PDF_PAGES)
    chunks = []
    for i in range(0, pages, PDF_CHUNK):
        text = []
        for p in range(i, min(i + PDF_CHUNK, pages)):
            text.append(pdf.pages[p].extract_text() or "")
        chunks.append("\n".join(text))
    return "\n".join(chunks)


# HTML cleanup


def extract_content_from_soup(soup: BeautifulSoup) -> str:
    """Extract content from BeautifulSoup object with multiple strategies."""
    # First check if this is likely a bot verification page
    is_bot_verification = detect_bot_verification_page(soup)
    if is_bot_verification:
        raise Exception(
            "Bot verification page detected - unable to access actual content"
        )

    # Remove known non-content elements first
    for tag in soup(
        ["script", "style", "nav", "header", "footer", "noscript", "iframe", "aside"]
    ):
        tag.decompose()
        
    # Attempt to remove common sidebars/TOCs specifically (like eBay's "On this page")
    for toc_selector in ['div[class*="on-this-page"]', 'div[id*="toc"]', 'nav[class*="toc"]']: 
        toc = soup.select_one(toc_selector)
        if toc:
            logger.info(f"Removing potential Table of Contents element: {toc_selector}")
            toc.decompose()

    potential_containers = []

    # 1. Prioritize semantic containers: <article>, <main>, role="main"
    for selector in ["article", "main", '[role="main"]']:
        elements = soup.select(selector)
        if elements:
            potential_containers.extend(elements)
            break 

    # 2. If no semantic container, try common content IDs/classes
    if not potential_containers:
        for selector in [
            "#content",
            ".content",
            "#main-content",
            ".main-content",
            "#main",
            ".main",
            ".entry-content", # Common in blogs/CMS
            '[class*="page-content"]',
            # Add TOS/Policy specific selectors as lower priority fallbacks
            '[id*="terms"]',
            '[id*="tos"]',
            '[id*="agreement"]',
            '[id*="legal"]',
            '[id*="policy"]',
            '[class*="terms"]',
            '[class*="tos"]',
            '[class*="agreement"]',
            '[class*="legal"]',
            '[class*="policy"]',
        ]:
            elements = soup.select(selector)
            if elements:
                potential_containers.extend(elements)
                if selector.startswith( ('#', '.') ):
                    break

    # Select the best container or fall back to body
    best_container = None
    if potential_containers:
        # Simplification: Pick the first semantic one found, or the first specific ID/class
        best_container = potential_containers[0]
        container_id = best_container.get('id', '')
        container_class = best_container.get('class', '')
        logger.info(f"Selected container: <{best_container.name}> id='{container_id}' class='{container_class}'")
    else:
        best_container = soup.body
        if not best_container:
             logger.warning("No <body> tag found, falling back to root soup object.")
             best_container = soup # Fallback if no body
        logger.info("No specific container found, using <body> as container.")


    # 3. Extract meaningful text from the selected container
    text_parts = []
    if best_container:
        # Find primarily block-level text elements
        # Avoid generic divs/spans here as they are often UI elements
        relevant_elements = best_container.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'], recursive=True)
        
        min_line_length = 10 # Adjust minimum length slightly
        
        processed_elements = set() # Keep track of elements already processed to avoid duplicates from nesting
        
        for element in relevant_elements:
            # Skip if element or its text content has already been processed via a parent
            if element in processed_elements: 
                continue

            # Skip if element is inside a known non-content area that wasn't fully removed
            # (e.g., if a button or form somehow survived initial decomposition)
            if element.find_parent(['button', 'form', 'select']): 
                continue
            
            # Extract text 
            element_text = element.get_text(separator=' ', strip=True)
             
            if len(element_text) >= min_line_length:
                text_parts.append(element_text)
                
                # Mark this element and all its children as processed
                processed_elements.add(element)
                processed_elements.update(element.find_all()) # Mark children too
                      
    if text_parts:
        # Join parts with single newlines for better readability before final cleaning
        content = "\n".join(text_parts) 
        logger.info(f"Extracted content parts from selected container: {len(content)} characters")
    else:
        # Ultimate fallback: Get all text from the originally selected best container
        logger.warning("No specific text parts found in container, falling back to full container text.")
        if best_container:
            content = best_container.get_text(separator="\n", strip=True)
        else:
             content = "" 
             
    # Final check for missed bot page based on extracted content
    if len(content) < 1000 and is_likely_bot_page(content):
        raise Exception(
            "Bot verification content detected - unable to access actual document"
        )

    # Basic post-cleaning: remove extra whitespace and blank lines
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    cleaned_content = '\n'.join(lines)

    logger.info(f"Final cleaned content length: {len(cleaned_content)} characters")
    return cleaned_content

def fetch_text(url):
    """ Fetch all <p> text from url """
    try:
        res = requests.get(url, headers={
            "User-Agent": CONSISTENT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9"
        })
        soup = BeautifulSoup(res.text, 'html.parser')
        return soup.get_text(separator=' ', strip=True)
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return "Not found"

def detect_bot_verification_page(soup: BeautifulSoup) -> bool:
    """
    Detect if the page is a bot verification or CAPTCHA page.
    Returns True if it appears to be a verification page.
    """
    # Common bot verification indicators in text
    verification_phrases = [
        "verify yourself",
        "please verify",
        "security check",
        "bot check",
        "captcha",
        "prove you're human",
        "are you a robot",
        "not a robot",
        "verification required",
        "security verification",
        "security measure",
        "please confirm you're not a robot",
        "we need to verify",
        "please complete the security check",
    ]

    # Check page text for verification phrases
    page_text = soup.get_text(separator=" ", strip=True).lower()
    if any(phrase in page_text for phrase in verification_phrases):
        matching_phrases = [
            phrase for phrase in verification_phrases if phrase in page_text
        ]
        logger.warning(
            f"Bot verification page detected with phrases: {matching_phrases}"
        )
        return True

    # Check for CAPTCHA elements
    captcha_indicators = soup.select(
        'iframe[src*="captcha"], iframe[src*="recaptcha"], div[class*="captcha"], div[id*="captcha"]'
    )
    if captcha_indicators:
        logger.warning("CAPTCHA elements detected on page")
        return True

    # Check if there are verification images
    verification_images = soup.select(
        'img[alt*="verification"], img[alt*="security"], img[alt*="captcha"]'
    )
    if verification_images:
        logger.warning("Verification images detected on page")
        return True

    return False


def is_likely_bot_page(text: str) -> bool:
    """
    Analyzes text content to determine if it's likely a bot verification page.
    """
    text = text.lower()

    # Common phrases in bot verification pages
    bot_phrases = [
        "verify yourself",
        "verification",
        "security measure",
        "please verify",
        "bot detection",
        "captcha",
        "human verification",
        "not a robot",
        "bot check",
        "security check",
        "confirm you're human",
        "prove you're not a bot",
    ]

    # Check if multiple bot verification phrases are present
    matches = [phrase for phrase in bot_phrases if phrase in text]
    if len(matches) >= 2:
        logger.warning(
            f"Text likely from a bot verification page. Matched phrases: {matches}"
        )
        return True

    # Check for very short content with specific verification keywords
    if len(text.split()) < 150 and any(
        phrase in text
        for phrase in ["verify", "verification", "robot", "bot", "security check"]
    ):
        retry_words = ["try again", "reload", "refresh", "browser"]
        if any(word in text for word in retry_words):
            logger.warning(
                "Short text with verification keywords and retry suggestions detected"
            )
            return True

    return False


# Standard HTML extraction


async def extract_standard_html(
    url: str, doc_type: str, ret_url: str
) -> ExtractResponse:
    try:
        # Enhanced browser-like headers with consistent user agent
        headers = {
            "User-Agent": CONSISTENT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

        # Log the headers we're using
        logger.info(
            f"Attempting standard extraction for URL: {url} with User-Agent: {headers['User-Agent']}"
        )

        loop = asyncio.get_event_loop()

        # Use retry strategy with exponential backoff
        max_retries = 3
        retry_delay = 1.0

        for retry in range(max_retries):
            try:
                fut = loop.run_in_executor(
                    None,
                    lambda: requests.get(
                        url,
                        headers=headers,
                        timeout=STANDARD_TIMEOUT,
                        allow_redirects=True,
                    ),
                )
                resp = await asyncio.wait_for(fut, timeout=STANDARD_TIMEOUT + 1)
                resp.raise_for_status()
                break  # Success, exit retry loop
            except Exception as e:
                if retry == max_retries - 1:  # Last retry
                    raise
                logger.warning(
                    f"Extraction attempt {retry+1} failed: {str(e)}. Retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff

        # Log response details AFTER successful request
        logger.info(f"Standard request successful for {url}.")
        logger.debug(f"Response Headers for {url}: {resp.headers}")
        logger.debug(f"Requests detected encoding for {url}: {resp.encoding}")
        
        # IMPROVED ENCODING HANDLING
        # Get content bytes from response
        content_bytes = resp.content
        
        # Handle Brotli-compressed responses explicitly
        if resp.headers.get("Content-Encoding", "").lower() == "br":
            try:
                content_bytes = brotli.decompress(content_bytes)
                logger.info("Decompressed Brotli content successfully")
            except Exception as e:
                logger.warning(f"Failed to decompress Brotli content: {e}")
        
        # Try to detect encoding from headers first
        content_type = resp.headers.get('Content-Type', '')
        encoding_match = re.search(r'charset=([^ ;]+)', content_type)
        detected_encoding = encoding_match.group(1) if encoding_match else None
        
        # If no encoding in headers, use what requests detected, with fallbacks
        if not detected_encoding:
            detected_encoding = resp.encoding if resp.encoding else 'utf-8'
        
        # Log detailed encoding information
        logger.info(f"Detected encoding for {url}: headers={detected_encoding}, requests={resp.encoding}")
        
        # Always try UTF-8 first for best compatibility
        try:
            # First try UTF-8 regardless of detected encoding
            html_content = content_bytes.decode('utf-8', errors='replace')
            logger.info(f"Successfully decoded content using UTF-8")
        except Exception as e:
            logger.warning(f"UTF-8 decoding failed, trying detected encoding: {detected_encoding}")
            try:
                # Try the detected encoding as fallback
                html_content = content_bytes.decode(detected_encoding, errors='replace')
                logger.info(f"Successfully decoded content using {detected_encoding}")
            except Exception as e:
                # Last resort - force utf-8 with replacement for invalid chars
                logger.error(f"All encoding attempts failed, forcing UTF-8 with error replacement")
                html_content = content_bytes.decode('utf-8', errors='replace')
        
        # Additional HTML sanitization to remove any potential binary or control characters
        html_content = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', html_content)
        
        # Verify decode was successful by checking for readable characters
        if not re.search(r'[a-zA-Z0-9 ]', html_content[:1000]):
            logger.warning("Decoded content appears to be binary or corrupted, trying ASCII fallback")
            html_content = content_bytes.decode('ascii', errors='replace')

        # Log a sample of the decoded content for verification
        logger.debug(f"Decoded content sample: {html_content[:500]}")

        soup = BeautifulSoup(html_content, "html.parser")
        
        # Check for bot verification page first
        if detect_bot_verification_page(soup):
            raise Exception("Bot verification page detected - cannot extract content")
        
        # Try multiple extraction methods and use the best one
        
        # Method 1: Maximum content extraction - prioritize getting EVERYTHING 
        max_content_text = extract_maximum_content(soup)
        
        # Method 2: Improved text extraction logic from existing method
        logger.info("Applying enhanced content extraction with BeautifulSoup")
        
        # Remove non-content elements
        for tag in soup.select('script, style, nav, footer, header, noscript, iframe, aside, [class*="cookie"], [class*="banner"], [id*="banner"], [class*="popup"], [id*="popup"]'):
            tag.extract()
        
        # Try to identify the main content area
        main_content = None
        
        # 1. Try semantic elements first
        for selector in ['article', 'main', '[role="main"]', 'section.content', 'div.content', '#content', '.post-content', '.entry-content']:
            content_area = soup.select_one(selector)
            if content_area and len(content_area.get_text(strip=True)) > 200:
                main_content = content_area
                logger.info(f"Found main content area using selector: {selector}")
                break
        
        # 2. For terms/privacy pages specifically (based on document type)
        if not main_content and doc_type in ['tos', 'pp']:
            doc_type_selectors = [
                f'[class*="{doc_type}"]', 
                f'[id*="{doc_type}"]',
                '[class*="terms"]',
                '[id*="terms"]',
                '[class*="privacy"]',
                '[id*="privacy"]',
                '[class*="legal"]',
                '[id*="legal"]'
            ]
            
            for selector in doc_type_selectors:
                content_area = soup.select_one(selector)
                if content_area and len(content_area.get_text(strip=True)) > 200:
                    main_content = content_area
                    logger.info(f"Found {doc_type} specific content area using selector: {selector}")
                    break
        
        # 3. Fallback to body if no specific content area found
        if not main_content:
            main_content = soup.body or soup
            logger.info("No specific content area found, using body element")
        
        # Extract meaningful text from paragraphs and headings
        text_parts = []
        
        # Prioritize these content elements
        for elem in main_content.select('p, h1, h2, h3, h4, h5, h6, li, div > text'):
            # Skip very short elements that are likely UI components
            elem_text = elem.get_text(strip=True)
            if len(elem_text) > 15:  # Minimum length to filter out buttons/labels
                text_parts.append(elem_text)
        
        # If we couldn't find enough paragraph content, fall back to all text
        if len(''.join(text_parts)) < 500:
            logger.warning("Not enough paragraph content found, using all text from content area")
            text_parts = [main_content.get_text(separator=' ', strip=True)]
        
        # Join with newlines between paragraphs for better readability
        structured_text = '\n'.join(text_parts)
        
        # Final cleanup - remove excessive whitespace
        structured_text = re.sub(r'\s+', ' ', structured_text).strip()
        
        # Add reasonable paragraph breaks
        structured_text = re.sub(r'([.!?])\s+', r'\1\n', structured_text)
        
        # Remove any remaining non-printable characters
        structured_text = re.sub(r'[^\x20-\x7E\x0A\x0D\u00A0-\u00FF\u0100-\u017F]', '', structured_text)
        
        # Method 3: Just get all text from the body as a fallback
        all_body_text = ""
        if soup.body:
            all_body_text = soup.body.get_text(separator=' ', strip=True)
            all_body_text = re.sub(r'\s+', ' ', all_body_text).strip()
        
        # Choose the best extraction result (by length)
        content_candidates = [
            (max_content_text, "maximum_content"),
            (structured_text, "structured_content"),
            (all_body_text, "body_text")
        ]
        
        # Sort by content length (descending)
        content_candidates.sort(key=lambda x: len(x[0]) if x[0] else 0, reverse=True)
        
        # Log extraction results
        for text, method in content_candidates:
            logger.info(f"Extraction method {method} produced {len(text) if text else 0} characters")
        
        # Use the longest content that meets minimum requirements
        text = content_candidates[0][0]
        method = content_candidates[0][1]
        
        if len(text) < MIN_CONTENT_LENGTH:
            raise Exception("Insufficient content")

        logger.info(
            f"Successfully extracted {len(text)} characters from {url} using standard method ({method})"
        )
        return ExtractResponse(
            url=ret_url,
            document_type=doc_type,
            text=text,
            success=True,
            message=f"standard_{method}",
            method_used="standard",
        )
    except Exception as e:
        logger.warning(f"Standard extraction failed: {str(e)}")
        raise


# PDF extraction


async def extract_pdf(url: str, doc_type: str, ret_url: str) -> ExtractResponse:
    try:
        # Enhanced browser-like headers with consistent user agent
        headers = {
            "User-Agent": CONSISTENT_USER_AGENT,
            "Accept": "application/pdf,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
        }

        logger.info(
            f"Attempting PDF extraction for: {url} with User-Agent: {headers['User-Agent']}"
        )

        # Use retry strategy with exponential backoff
        max_retries = 3
        retry_delay = 1.0

        for retry in range(max_retries):
            try:
                resp = requests.get(
                    url, headers=headers, timeout=STANDARD_TIMEOUT, allow_redirects=True
                )
                resp.raise_for_status()
                break  # Success, exit retry loop
            except Exception as e:
                if retry == max_retries - 1:  # Last retry
                    raise
                logger.warning(
                    f"PDF download attempt {retry+1} failed: {str(e)}. Retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff

        # Check if content type is PDF
        content_type = resp.headers.get("Content-Type", "").lower()
        if not ("application/pdf" in content_type or is_pdf_url(url)):
            raise Exception(f"Not a PDF document. Content-Type: {content_type}")

        text = extract_text_from_pdf(resp.content)
        if len(text) < MIN_CONTENT_LENGTH:
            raise Exception("PDF content too small")

        logger.info(f"Successfully extracted {len(text)} characters from PDF: {url}")
        return ExtractResponse(
            url=ret_url,
            document_type=doc_type,
            text=text,
            success=True,
            message="pdf",
            method_used="pdf",
        )
    except Exception as e:
        logger.warning(f"PDF extraction failed: {str(e)}")
        raise


# Playwright extraction


async def extract_with_playwright(
    url: str, doc_type: str, ret_url: str
) -> ExtractResponse:
    """Extract content using Playwright with improved waiting and interaction."""
    page = None
    try:
        logger.info(f"Starting Playwright extraction for URL: {url}")
        
        # Ensure the browser is initialized
        if not auth_manager.startup_complete or not auth_manager.context:
            logger.error("Playwright browser not initialized, cannot extract content")
            raise Exception("Playwright browser not available")
        
        # Get a browser page
        page = await auth_manager.get_page()
        
        # Improved navigation options with extended timeout for complex pages
        logger.info(f"Navigating to URL with Playwright: {url}")
        try:
            await page.goto(
                url, 
                wait_until="networkidle", 
                timeout=90000  # 90 seconds timeout for slow-loading pages
            )
        except Exception as nav_err:
            # If networkidle fails, try with domcontentloaded which is less strict
            logger.warning(f"Navigation with networkidle failed: {nav_err}, trying with domcontentloaded")
            await page.goto(
                url, 
                wait_until="domcontentloaded", 
                timeout=45000
            )
        
        # Wait a bit longer for any remaining content to load
        await asyncio.sleep(3)
        
        # Enhanced wait for content to stabilize - wait for core content elements
        try:
            # Wait for common content containers
            for selector in [
                "main", "article", "#content", ".content", "#main", ".main",
                "[class*='terms']", "[class*='privacy']", "[class*='policy']",
                "div > p", "div > h1", "div > h2"
            ]:
                try:
                    # Only wait 1s per selector to avoid long delays
                    await page.wait_for_selector(selector, timeout=1000)
                    logger.info(f"Found content selector: {selector}")
                    break
                except:
                    continue
        except Exception as wait_err:
            logger.warning(f"Wait for content elements timed out: {wait_err}")
        
        # Try to click "Accept" or "I Agree" buttons if present (common on legal pages)
        for selector in [
            'button:has-text("Accept")',
            'button:has-text("I Agree")',
            'button:has-text("Agree")',
            'button:has-text("Continue")',
            'a:has-text("Accept")',
            'a:has-text("I Agree")',
            '[class*="accept"]:visible',
            '[class*="agree"]:visible',
            '[id*="accept"]:visible',
            '[id*="agree"]:visible',
        ]:
            try:
                if await page.locator(selector).count() > 0:
                    logger.info(f"Clicking {selector} button")
                    await page.locator(selector).first.click()
                    await asyncio.sleep(1)  # Wait for any post-click changes
            except Exception as accept_error:
                logger.debug(f"Error clicking {selector}: {str(accept_error)}")
        
        # NEW: Intelligent scrolling to reveal dynamically loaded content
        # This greatly improves extraction for lazy-loaded content
        try:
            logger.info("Performing intelligent scroll to reveal all content")
            # Initial scroll to bottom to trigger any lazy loading
            await page.evaluate("""
                window.scrollTo({
                    top: document.body.scrollHeight,
                    behavior: 'smooth'
                });
            """)
            await asyncio.sleep(1.5)  # Wait for any lazy content to load
            
            # Scroll back to top
            await page.evaluate("window.scrollTo(0, 0);")
            await asyncio.sleep(0.5)
            
            # More thorough scrolling - scroll down in chunks
            height = await page.evaluate("document.body.scrollHeight")
            view_port_height = await page.evaluate("window.innerHeight")
            
            if height > view_port_height:
                steps = min(10, max(3, int(height / view_port_height)))  # At least 3, at most 10 steps
                logger.info(f"Scrolling page in {steps} steps to reveal all content")
                
                for i in range(steps):
                    position = int((i + 1) * height / steps)
                    await page.evaluate(f"window.scrollTo(0, {position})")
                    await asyncio.sleep(0.5)  # Brief pause at each scroll position
            
            # Final pause after scrolling to ensure all content is loaded
            await asyncio.sleep(2)
        except Exception as scroll_err:
            logger.warning(f"Error during intelligent scrolling: {str(scroll_err)}")

        # Get content - enhanced with multiple extraction methods
        html = await page.content()
        logger.debug(f"Playwright raw content start (first 500 chars) for {url}: {html[:500]}")
        soup = BeautifulSoup(html, "html.parser")
        
        # NEW: Enhanced content extraction with multiple methods
        # Try several extraction methods and use the most comprehensive result
        
        # Method 0: New maximum content extraction - prioritize getting EVERYTHING
        extracted_text_0 = extract_maximum_content(soup)
        
        # Method 1: Our standard extract_content_from_soup function 
        extracted_text_1 = extract_content_from_soup(soup)
        
        # Method 2: Specialized content extraction for legal pages
        extracted_text_2 = ""
        try:
            # Find the most likely container for privacy/terms content
            legal_selectors = [
                "[class*='privacy']", "[class*='policy']", "[class*='terms']",
                "[id*='privacy']", "[id*='policy']", "[id*='terms']",
                "main", "article", ".content", "#content", "#main"
            ]
            
            legal_content = None
            for selector in legal_selectors:
                elements = soup.select(selector)
                if elements and len(elements[0].get_text(strip=True)) > 200:
                    legal_content = elements[0]
                    logger.info(f"Found legal content container with selector: {selector}")
                    break
            
            if legal_content:
                # Extract all paragraph and heading text from this container
                text_elements = []
                for elem in legal_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li']):
                    elem_text = elem.get_text(strip=True)
                    if elem_text:
                        text_elements.append(elem_text)
                
                extracted_text_2 = "\n".join(text_elements)
            else:
                logger.warning("No specialized legal content container found")
        except Exception as ext_err:
            logger.warning(f"Error in specialized extraction: {str(ext_err)}")
        
        # Method 3: Direct JavaScript evaluation to get text content
        extracted_text_3 = ""
        try:
            # Get text content using JavaScript's innerText - enhanced to get ALL text
            extracted_text_3 = await page.evaluate("""
                function getVisibleText() {
                    // Don't bother finding a specific container, get ALL content
                    const excludeSelectors = 'script, style, noscript';
                    const excludedElements = document.querySelectorAll(excludeSelectors);
                    
                    // Hide script and style elements to avoid their text
                    for (let el of excludedElements) {
                        el.style.display = 'none';
                    }
                    
                    // Get the entire body text
                    const text = document.body.innerText;
                    
                    // Restore visibility
                    for (let el of excludedElements) {
                        el.style.display = '';
                    }
                    
                    return text;
                }
                return getVisibleText();
            """)
        except Exception as js_err:
            logger.warning(f"Error in JS evaluation extraction: {str(js_err)}")
        
        # Choose the best extraction result (longest content that meets minimum length)
        content_candidates = [
            (extracted_text_0, "maximum_content_extraction"),  # Prioritize this method first
            (extracted_text_3, "javascript_evaluation"),       # Then JS evaluation
            (extracted_text_1, "extract_content_from_soup"),   # Then standard extraction
            (extracted_text_2, "specialized_legal_extraction") # Then specialized extraction
        ]
        
        # Sort by content length (descending)
        content_candidates.sort(key=lambda x: len(x[0]) if x[0] else 0, reverse=True)
        
        # Log extraction results
        for text, method in content_candidates:
            logger.info(f"Extraction method {method} produced {len(text) if text else 0} characters")
        
        # Use the longest content if it meets minimum requirements
        text = content_candidates[0][0] if content_candidates[0][0] else ""
        extraction_method = content_candidates[0][1]
        
        # Double check for sufficient content
        if len(text) >= MIN_CONTENT_LENGTH:
            logger.info(f"Successfully extracted {len(text)} characters using Playwright ({extraction_method})")
            return ExtractResponse(
                url=ret_url,
                document_type=doc_type,
                text=text,
                success=True,
                message="playwright",
                method_used="playwright",
            )

        # As a fallback for very complex pages, try just evaluating body text
        try:
            text = await page.evaluate("document.body.innerText")
            # Basic cleaning
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) >= MIN_CONTENT_LENGTH:
                logger.info(
                    f"Used body.innerText fallback to extract {len(text)} characters"
                )
                return ExtractResponse(
                    url=ret_url,
                    document_type=doc_type,
                    text=text,
                    success=True,
                    message="playwright_innertext",
                    method_used="playwright",
                )
        except Exception as e:
            logger.warning(f"innerText extraction failed: {str(e)}")

        # If we get here, all methods failed
        raise Exception("Playwright extraction yielded insufficient content")
    except Exception as e:
        logger.warning(f"Playwright extraction failed: {str(e)}")
        raise
    finally:
        # Ensure page is always released back to the pool
        if page:
            try:
                await auth_manager.release_page(page)
                logger.debug("Successfully released page back to the pool")
            except Exception as e:
                logger.error(f"Error releasing Playwright page: {str(e)}")


# Simple extraction using fetch_text
async def extract_with_simple_fetch(url: str, doc_type: str, ret_url: str) -> ExtractResponse:
    """Extract content using simple fetch_text method as an intermediate step."""
    try:
        logger.info(f"Attempting simple fetch extraction for: {url}")

        headers = {
            "User-Agent": CONSISTENT_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br", # Ensure 'br' is accepted
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8" # Added Accept header
        }

        # Run requests.get in a thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            executor,
            lambda: requests.get(url, headers=headers, timeout=STANDARD_TIMEOUT, allow_redirects=True)
        )
        resp.raise_for_status() # Check for HTTP errors

        # --- Added Brotli Decompression Logic ---
        content_bytes = resp.content
        if resp.headers.get("Content-Encoding", "").lower() == "br":
            try:
                logger.info("Brotli encoding detected in simple fetch, decompressing...")
                content_bytes = brotli.decompress(content_bytes)
                logger.info("Decompressed Brotli content successfully for simple fetch")
            except Exception as e:
                logger.warning(f"Failed to decompress Brotli content in simple fetch: {e}")
        # --- End Brotli Logic ---

        # Decode content - prioritize UTF-8
        try:
            html_content = content_bytes.decode('utf-8', errors='replace')
        except Exception:
            # Fallback using detected encoding or default
            detected_encoding = resp.encoding if resp.encoding else 'utf-8'
            logger.warning(f"UTF-8 decoding failed in simple fetch, trying {detected_encoding}")
            html_content = content_bytes.decode(detected_encoding, errors='replace')

        # Parse with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)

        # Log the result for debugging
        logger.info(f"Simple fetch result length: {len(text) if text else 0}")

        # Check if we got enough content
        if text and len(text) >= MIN_CONTENT_LENGTH:
            logger.info(
                f"Successfully extracted {len(text)} characters using simple fetch method"
            )
            return ExtractResponse(
                url=ret_url,
                document_type=doc_type,
                text=text,
                success=True,
                message="simple_fetch",
                method_used="simple_fetch",
            )
        else:
            logger.warning(f"Simple fetch returned insufficient content: {len(text) if text else 0} characters")
            raise Exception("Simple fetch extraction yielded insufficient content")
    except Exception as e:
        logger.warning(f"Simple fetch extraction failed: {str(e)}")
        raise


# Main endpoint


@router.post("/extract", response_model=ExtractResponse)
async def extract_text(request: ExtractRequest, response: Response) -> ExtractResponse:
    # ---> ADDED: Log entry point immediately
    logger.info(f"Received extraction request for URL: {request.url}, Type: {request.document_type}")
    logging.getLogger().handlers[0].flush() # Attempt to flush immediately

    orig = request.url
    url = sanitize_url(orig)
    if not url:
        response.status_code = 400
        return ExtractResponse(
            url=orig,
            document_type=request.document_type or "tos",
            text=None,
            success=False,
            message="Invalid URL",
            method_used="standard",
        )

    doc_type = request.document_type or "tos"
    cache_key = f"{url}:{doc_type}"
    cached = get_from_cache(cache_key)
    if cached:
        return ExtractResponse(**cached)

    # Discover ToS/PP
    if doc_type in ["tos", "pp"]:
        path = urlparse(url).path.lower()
        query = urlparse(url).query.lower()
        # Skip URL discovery if URL already appears to be a legal document
        if (
            doc_type == "tos"
            and any(
                pattern in path or pattern in query
                for pattern in [
                    "terms",
                    "tos",
                    "user-agreement",
                    "legal",
                    "service",
                    "eula",
                ]
            )
        ) or (
            doc_type == "pp"
            and any(
                pattern in path or pattern in query
                for pattern in ["privacy", "datapolicy", "data-policy", "privacypolicy"]
            )
        ):
            logger.info(
                f"URL appears to be a {doc_type} URL already, skipping discovery: {url}"
            )
        else:
            req = ToSRequest(url=url) if doc_type == "tos" else PrivacyRequest(url=url)
            finder = find_tos if doc_type == "tos" else find_privacy_policy
            try:
                resp = await asyncio.wait_for(
                    finder(req), timeout=URL_DISCOVERY_TIMEOUT
                )
                doc_url = resp.tos_url if doc_type == "tos" else resp.pp_url
                if doc_url:
                    url = doc_url
                    logger.info(f"Found document URL: {url}")
            except asyncio.TimeoutError:
                logger.warning(
                    f"Document finder timed out after {URL_DISCOVERY_TIMEOUT}s"
                )
            except Exception as e:
                logger.warning(f"Document finder failed: {str(e)}")

    # SEQUENTIAL EXTRACTION APPROACH
    # First, check if it's a PDF and use PDF extractor if it is
    if is_pdf_url(url):
        logger.info(f"Detected PDF URL, attempting PDF extraction for {url}")
        try:
            pdf_result = await extract_pdf(url, doc_type, url)
            if pdf_result.success:
                add_to_cache(cache_key, pdf_result.dict())
                return pdf_result
        except Exception as e:
            logger.warning(f"PDF extraction failed: {str(e)}")
            # Continue to other methods if PDF extraction fails
    
    # Next, try standard HTML extraction with BeautifulSoup first
    logger.info(f"Attempting standard HTML extraction first for {url}")
    try:
        standard_result = await extract_standard_html(url, doc_type, url)
        if standard_result.success:
            add_to_cache(cache_key, standard_result.dict())
            return standard_result
    except Exception as e:
        logger.warning(f"Standard HTML extraction failed: {str(e)}")
        # If standard extraction fails, we'll try simple fetch method next
    
    # Try simple fetch extraction before Playwright
    logger.info(f"Standard extraction failed, attempting simple fetch extraction for {url}")
    try:
        simple_fetch_result = await extract_with_simple_fetch(url, doc_type, url)
        if simple_fetch_result.success:
            add_to_cache(cache_key, simple_fetch_result.dict())
            return simple_fetch_result
    except Exception as e:
        logger.warning(f"Simple fetch extraction failed: {str(e)}")
        # If simple fetch extraction fails, we'll try Playwright
    
    # Only try Playwright if both standard and simple fetch extraction failed
    logger.info(f"Simple fetch extraction failed, attempting Playwright extraction for {url}")
    if auth_manager.startup_complete and auth_manager.context:
        try:
            playwright_result = await extract_with_playwright(url, doc_type, url)
            if playwright_result.success:
                add_to_cache(cache_key, playwright_result.dict())
                return playwright_result
        except Exception as e:
            logger.warning(f"Playwright extraction failed: {str(e)}")
    else:
        logger.warning(f"Skipping Playwright extraction - browser not initialized. Startup complete: {auth_manager.startup_complete}")
        if auth_manager.startup_failure:
            logger.error(f"Startup failure reason: {auth_manager.startup_failure}")
    
    # If we got here, all methods failed
    logger.error(f"All extraction methods failed for {url}")
    logging.getLogger().handlers[0].flush()
    
    return ExtractResponse(
        url=url,
        document_type=doc_type,
        text=None,
        success=False,
        message="Extraction failed - all methods exhausted",
        method_used="standard",
    )

async def extract_content(url: str, document_type: str = None) -> tuple:
    """
    Extract content from a URL.
    
    Args:
        url: The URL to extract content from
        document_type: Optional document type (tos or pp)
        
    Returns:
        Tuple of (extracted_text, retrieved_url)
    """
    logger.info(f"Extracting content from URL: {url}, document_type: {document_type}")
    
    # Ensure document_type is valid (required by ExtractRequest model)
    valid_doc_type = document_type if document_type in ['tos', 'pp'] else 'tos'
    
    # Create an ExtractRequest
    request = ExtractRequest(url=url, document_type=valid_doc_type)
    
    # Create a Response object to pass to extract_text
    fastapi_response = Response()
    
    # Call extract_text with both request and response parameters
    response = await extract_text(request, fastapi_response)
    
    # Extract the text and URL from the response
    if response and response.success:
        return response.text, response.url
    else:
        # If extraction failed, return empty text but still return the URL to avoid errors
        logger.error(f"Failed to extract content from {url}")
        return "", url

def extract_maximum_content(soup: BeautifulSoup) -> str:
    """
    Extract maximum possible content from a page, ignoring HTML structure.
    This approach prioritizes quantity over quality of content.
    """
    logger.info("Using maximum content extraction approach")
    
    # Remove only the most problematic elements
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    
    # Get all text from the entire page, preserving whitespace
    all_text = soup.get_text(separator=' ', strip=True)
    
    # Basic cleanup - normalize whitespace while preserving paragraphs
    all_text = re.sub(r'\s+', ' ', all_text)
    
    # Add paragraph breaks at sentence endings for readability
    all_text = re.sub(r'([.!?])\s', r'\1\n', all_text)
    
    logger.info(f"Maximum content extraction yielded {len(all_text)} characters")
    return all_text
