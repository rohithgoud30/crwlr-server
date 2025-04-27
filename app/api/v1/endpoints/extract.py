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
from fake_useragent import UserAgent
import os
import sys

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
                user_agent=get_random_user_agent()
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
                user_agent=get_random_user_agent()
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

# Initialize UserAgent for random browser User-Agent strings
ua_generator = UserAgent()

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
        # Enhanced browser-like headers with random user agent
        headers = {
            "User-Agent": get_random_user_agent(),
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
        logger.debug(f"Raw content start (first 500 bytes) for {url}: {resp.content[:500]}")


        # Explicitly handle encoding
        content_bytes = resp.content
        encoding = resp.encoding if resp.encoding else 'utf-8'
        # Fallback if requests guesses poorly (e.g., ISO-8859-1 is often a fallback for failed UTF-8 detection)
        if encoding.lower() == 'iso-8859-1':
            logger.warning(f"Requests detected ISO-8859-1 encoding for {url}, falling back to UTF-8.")
            encoding = 'utf-8'

        try:
            # Decode using determined encoding, ignoring errors
            html_content = content_bytes.decode(encoding, errors='ignore')
            logger.info(f"Decoded content from {url} using encoding: {encoding}")
            logger.debug(f"Decoded content start (first 500 chars) for {url}: {html_content[:500]}") # Log decoded content start
        except Exception as decode_error:
             logger.error(f"Failed to decode content from {url} with encoding {encoding}: {decode_error}. Falling back to UTF-8.")
             # Final fallback decoding attempt
             html_content = content_bytes.decode('utf-8', errors='ignore')
             logger.debug(f"Decoded content start (UTF-8 fallback) for {url}: {html_content[:500]}") # Log decoded content start


        soup = BeautifulSoup(html_content, "html.parser")
        text = extract_content_from_soup(soup)

        if len(text) < MIN_CONTENT_LENGTH:
            raise Exception("Insufficient content")

        logger.info(
            f"Successfully extracted {len(text)} characters from {url} using standard method"
        )
        return ExtractResponse(
            url=ret_url,
            document_type=doc_type,
            text=text,
            success=True,
            message="standard",
            method_used="standard",
        )
    except Exception as e:
        logger.warning(f"Standard extraction failed: {str(e)}")
        raise


# PDF extraction


async def extract_pdf(url: str, doc_type: str, ret_url: str) -> ExtractResponse:
    try:
        # Enhanced browser-like headers with random user agent
        headers = {
            "User-Agent": get_random_user_agent(),
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
    # ---> ADDED: Log entry into this function
    logger.info(f"Entering extract_with_playwright for {url}")
    logging.getLogger().handlers[0].flush()

    if not auth_manager.startup_complete: # Check if startup finished
        logger.error(
            "Playwright startup never completed or failed. Cannot extract."
        )
        # Log the specific startup failure if it exists
        if auth_manager.startup_failure:
             logger.error(f"Startup failure reason: {auth_manager.startup_failure}")
        raise Exception("Playwright browser not initialized or startup failed")
    
    if not auth_manager.context:
        logger.warning(
            "Playwright context not initialized - browser might not be started"
        )
        raise Exception("Playwright browser not initialized")

    page = None
    try:
        page = await auth_manager.get_page()
        logger.info(f"Navigating to {url} with Playwright")

        # Use a longer timeout for initial page load
        await page.goto(
            url, wait_until="domcontentloaded", timeout=STANDARD_TIMEOUT * 1000
        )

        # Wait for network to be idle (helps with JS-loaded content)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
            logger.info("Network is idle")
        except Exception as e:
            logger.warning(f"Network idle timeout: {str(e)}")

        # Scroll to ensure lazy-loaded content appears
        await page.evaluate(
            """
        () => {
                const scrollToBottom = () => {
                    window.scrollTo(0, document.body.scrollHeight);
                };
                
                const scrollToTop = () => {
                    window.scrollTo(0, 0);
                };
                
                // Scroll down in increments
                const totalScrolls = 5;
                for(let i = 0; i < totalScrolls; i++) {
                    setTimeout(() => {
                        const scrollPos = (document.body.scrollHeight / totalScrolls) * i;
                        window.scrollTo(0, scrollPos);
                    }, i * 300);
                }
                
                // Final scroll to bottom and back to top
                setTimeout(() => {
                    scrollToBottom();
                    setTimeout(scrollToTop, 300);
                }, totalScrolls * 300);
        }
        """
        )

        # Wait for any animations to finish
        await asyncio.sleep(2)

        # Try to click "Accept" or "I Agree" buttons if present (common on legal pages)
        for selector in [
            'button:has-text("Accept")',
            'button:has-text("I Agree")',
            'button:has-text("Agree")',
            'button:has-text("Continue")',
            'a:has-text("Accept")',
            'a:has-text("I Agree")',
        ]:
            try:
                if await page.locator(selector).count() > 0:
                    logger.info(f"Clicking {selector} button")
                    await page.locator(selector).first.click()
                    await asyncio.sleep(1)  # Wait for any post-click changes
            except Exception as accept_error:
                logger.debug(f"Error clicking {selector}: {str(accept_error)}")

        # Get content
        html = await page.content()
        logger.debug(f"Playwright raw content start (first 500 chars) for {url}: {html[:500]}") # Log Playwright content start
        soup = BeautifulSoup(html, "html.parser")
        text = extract_content_from_soup(soup)

        if len(text) >= MIN_CONTENT_LENGTH:
            logger.info(
                f"Successfully extracted {len(text)} characters using Playwright"
            )
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

    # Extraction tasks
    # For certain websites, we want to try the Playwright method first as it often works better for JavaScript-heavy sites
    use_playwright_first = any(
        domain in url.lower()
        for domain in [
            "ebay.com",
            "amazon.com",
            "facebook.com",
            "twitter.com",
            "instagram.com",
            "tiktok.com",
            "netflix.com",
            "airbnb.com",
            "booking.com",
            "expedia.com",
        ]
    )

    tasks = []
    if is_pdf_url(url):
        logger.info(f"Detected PDF URL, creating PDF extraction task for {url}")
        tasks.append(asyncio.create_task(extract_pdf(url, doc_type, url)))
    else:
        if use_playwright_first:
            # Try Playwright first for JS-heavy sites
            logger.info(f"Using Playwright as primary extraction method for {url}")
            # ---> ADDED: Log before potentially creating Playwright task
            if auth_manager.startup_complete and auth_manager.context:  # Check if browser seems ready
                logger.info(f"Playwright context seems ready, creating task for {url}")
                tasks.append(
                    asyncio.create_task(extract_with_playwright(url, doc_type, url))
                )
            else:
                 logger.warning(f"Playwright context not ready, skipping Playwright task creation for {url}. Startup complete: {auth_manager.startup_complete}")
                 # Log the specific startup failure if it exists
                 if auth_manager.startup_failure:
                      logger.error(f"Startup failure reason: {auth_manager.startup_failure}")


            # ---> ADDED: Log before creating standard task
            logger.info(f"Creating standard HTML task (secondary) for {url}")
            tasks.append(asyncio.create_task(extract_standard_html(url, doc_type, url)))
        else:
            # Standard extraction first for most sites
            # ---> ADDED: Log before creating standard task
            logger.info(f"Using standard HTML as primary extraction method, creating task for {url}")
            tasks.append(asyncio.create_task(extract_standard_html(url, doc_type, url)))

            # ---> ADDED: Log before potentially creating Playwright task
            if auth_manager.startup_complete and auth_manager.context: # Check if browser seems ready
                logger.info(f"Playwright context seems ready, creating task (secondary) for {url}")
                tasks.append(
                    asyncio.create_task(extract_with_playwright(url, doc_type, url))
                )
            else:
                 logger.warning(f"Playwright context not ready, skipping Playwright task creation (secondary) for {url}. Startup complete: {auth_manager.startup_complete}")
                  # Log the specific startup failure if it exists
                 if auth_manager.startup_failure:
                      logger.error(f"Startup failure reason: {auth_manager.startup_failure}")


    # Run tasks with better failure handling
    all_errors = []

    for task in tasks:
        try:
            res = await task
            if res.success:
                add_to_cache(cache_key, res.dict())
                # Cancel remaining tasks
                for t in tasks:
                    if t is not task and not t.done():
                        t.cancel()
                return res
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"Extraction task failed: {error_msg}")
            all_errors.append(error_msg)
            continue

    # If we get here, all methods failed
    error_summary = (
        "; ".join(all_errors) if all_errors else "Unknown extraction failure"
    )
    logger.error(f"All extraction methods failed for {url}: {error_summary}")
    # ---> ADDED: Explicitly flush logs on final failure
    logging.getLogger().handlers[0].flush()

    return ExtractResponse(
        url=url,
        document_type=doc_type,
        text=None,
        success=False,
        message=f"Extraction failed - all methods exhausted: {error_summary[:200]}",
        method_used="standard",
    )
