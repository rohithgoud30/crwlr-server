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

    async def startup(self):
        logger.info("Launching Playwright browser...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--ignore-certificate-errors',
            ]
        )
        self.context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            locale='en-US',
            timezone_id='America/New_York',
        )
        # Inject minimal stealth script
        await self.context.add_init_script("""
        () => {
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        }
        """)
        logger.info("PlaywrightManager ready.")

    async def get_page(self):
        await self.semaphore.acquire()
        return await self.context.new_page()

    async def release_page(self, page):
        try:
            await page.close()
        finally:
            self.semaphore.release()

    async def shutdown(self):
        logger.info("Shutting down Playwright browser...")
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("PlaywrightManager shut down.")

# Instantiate and plan to call startup/shutdown in app main
auth_manager = PlaywrightManager()

# Cache and settings
CACHE = {}
CACHE_TTL = 3600
MAX_CACHE_SIZE = 500
STANDARD_TIMEOUT = 10
URL_DISCOVERY_TIMEOUT = 8
MIN_CONTENT_LENGTH = 100
MAX_PDF_PAGES = 30
PDF_CHUNK = 5
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# URL sanitization

def sanitize_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or '.' not in parsed.netloc:
        return ''
    return url

# Cache helpers

def get_from_cache(key: str):
    entry = CACHE.get(key)
    if entry and time.time() < entry['expires']:
        return entry['value']
    return None


def add_to_cache(key: str, value: dict):
    if len(CACHE) >= MAX_CACHE_SIZE:
        oldest = sorted(CACHE.items(), key=lambda kv: kv[1]['expires'])[0][0]
        del CACHE[oldest]
    CACHE[key] = {'value': value, 'expires': time.time() + CACHE_TTL}

# PDF detection & extraction

def is_pdf_url(url: str) -> bool:
    return url.lower().endswith('.pdf')

@lru_cache(maxsize=100)
def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = io.BytesIO(pdf_bytes)
    pdf = __import__('PyPDF2').PdfReader(reader)
    pages = min(len(pdf.pages), MAX_PDF_PAGES)
    chunks = []
    for i in range(0, pages, PDF_CHUNK):
        text = []
        for p in range(i, min(i + PDF_CHUNK, pages)):
            text.append(pdf.pages[p].extract_text() or '')
        chunks.append('\n'.join(text))
    return '\n'.join(chunks)

# HTML cleanup

def extract_content_from_soup(soup: BeautifulSoup) -> str:
    for tag in soup(['script','style','nav','header','footer']):
        tag.decompose()
    ps = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True))>10]
    text = '\n\n'.join(ps)
    return text if len(text)>=MIN_CONTENT_LENGTH else soup.get_text(separator='\n', strip=True)

# Standard HTML extraction

async def extract_standard_html(url: str, doc_type: str, ret_url: str) -> ExtractResponse:
    try:
        # Enhanced browser-like headers with random user agent
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
        
        # Log the headers we're using
        logger.info(f"Attempting standard extraction for URL: {url} with User-Agent: {headers['User-Agent']}")
        
        loop = asyncio.get_event_loop()
        
        # Use retry strategy with exponential backoff
        max_retries = 3
        retry_delay = 1.0
        
        for retry in range(max_retries):
            try:
                fut = loop.run_in_executor(
                    None, 
                    lambda: requests.get(url, headers=headers, timeout=STANDARD_TIMEOUT, allow_redirects=True)
                )
                resp = await asyncio.wait_for(fut, timeout=STANDARD_TIMEOUT+1)
                resp.raise_for_status()
                break  # Success, exit retry loop
            except Exception as e:
                if retry == max_retries - 1:  # Last retry
                    raise
                logger.warning(f"Extraction attempt {retry+1} failed: {str(e)}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
                
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Try different content extraction strategies
        text = None
        
        # 1. Try content extraction by paragraphs
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 10]
        if paragraphs:
            text = '\n\n'.join(paragraphs)
            
        # 2. If we didn't get enough content, try specific content sections
        if not text or len(text) < MIN_CONTENT_LENGTH:
            for content_id in ['content', 'main', 'main-content', 'article', 'terms', 'privacy-policy', 'terms-of-service']:
                content_div = soup.find(id=content_id) or soup.find('div', class_=content_id) or soup.find('article', class_=content_id)
                if content_div and len(content_div.get_text(strip=True)) > MIN_CONTENT_LENGTH:
                    text = content_div.get_text(separator='\n', strip=True)
                    break
                    
        # 3. If still no content, get full text
        if not text or len(text) < MIN_CONTENT_LENGTH:
            # First remove script, style and hidden elements
            for element in soup(['script', 'style', 'meta', 'link', 'header', 'footer', 'nav']):
                element.decompose()
            
            text = soup.get_text(separator='\n', strip=True)
        
        if len(text) < MIN_CONTENT_LENGTH:
            raise Exception('Insufficient content')
            
        logger.info(f"Successfully extracted {len(text)} characters from {url} using standard method")
        return ExtractResponse(url=ret_url, document_type=doc_type, text=text, success=True, message='standard', method_used='standard')
    except Exception as e:
        logger.warning(f"Standard extraction failed: {str(e)}")
        raise

# PDF extraction

async def extract_pdf(url: str, doc_type: str, ret_url: str) -> ExtractResponse:
    try:
        # Enhanced browser-like headers with random user agent
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'application/pdf,*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'max-age=0'
        }
        
        logger.info(f"Attempting PDF extraction for: {url} with User-Agent: {headers['User-Agent']}")
        
        # Use retry strategy with exponential backoff
        max_retries = 3
        retry_delay = 1.0
        
        for retry in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=STANDARD_TIMEOUT, allow_redirects=True)
                resp.raise_for_status()
                break  # Success, exit retry loop
            except Exception as e:
                if retry == max_retries - 1:  # Last retry
                    raise
                logger.warning(f"PDF download attempt {retry+1} failed: {str(e)}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
        
        # Check if content type is PDF
        content_type = resp.headers.get('Content-Type', '').lower()
        if not ('application/pdf' in content_type or is_pdf_url(url)):
            raise Exception(f"Not a PDF document. Content-Type: {content_type}")
        
        text = extract_text_from_pdf(resp.content)
        if len(text) < MIN_CONTENT_LENGTH:
            raise Exception('PDF content too small')
            
        logger.info(f"Successfully extracted {len(text)} characters from PDF: {url}")
        return ExtractResponse(url=ret_url, document_type=doc_type, text=text, success=True, message='pdf', method_used='pdf')
    except Exception as e:
        logger.warning(f"PDF extraction failed: {str(e)}")
        raise

# Playwright extraction

async def extract_with_playwright(url: str, doc_type: str, ret_url: str) -> ExtractResponse:
    page = await auth_manager.get_page()
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=STANDARD_TIMEOUT*1000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)
        html = await page.content()
        soup = BeautifulSoup(html, 'html.parser')
        text = extract_content_from_soup(soup)
        if len(text)>=MIN_CONTENT_LENGTH:
            return ExtractResponse(url=ret_url, document_type=doc_type, text=text, success=True, message='playwright', method_used='playwright')
        raise Exception('Playwright no content')
    finally:
        await auth_manager.release_page(page)

# Main endpoint

@router.post('/extract', response_model=ExtractResponse)
async def extract_text(request: ExtractRequest, response: Response) -> ExtractResponse:
    orig = request.url
    url = sanitize_url(orig)
    if not url:
        response.status_code = 400
        return ExtractResponse(url=orig, document_type=request.document_type or 'tos', text=None, success=False, message='Invalid URL', method_used='standard')

    doc_type = request.document_type or 'tos'
    cache_key = f"{url}:{doc_type}"
    cached = get_from_cache(cache_key)
    if cached:
        return ExtractResponse(**cached)

    # Discover ToS/PP
    if doc_type in ['tos','pp']:
        path = urlparse(url).path.lower()
        # Skip URL discovery if URL already appears to be a legal document
        if ((doc_type=='tos' and any(pattern in path for pattern in ['terms', 'tos', 'user-agreement', 'legal', 'service', 'eula'])) or 
            (doc_type=='pp' and any(pattern in path for pattern in ['privacy', 'datapolicy', 'data-policy', 'privacypolicy']))):
            logger.info(f"URL appears to be a {doc_type} URL already, skipping discovery: {url}")
        else:
            req = ToSRequest(url=url) if doc_type=='tos' else PrivacyRequest(url=url)
            finder = find_tos if doc_type=='tos' else find_privacy_policy
            try:
                resp = await asyncio.wait_for(finder(req), timeout=URL_DISCOVERY_TIMEOUT)
                doc_url = resp.tos_url if doc_type=='tos' else resp.pp_url
                if doc_url:
                    url = doc_url
                    logger.info(f"Found document URL: {url}")
            except Exception as e:
                logger.warning(f"Document finder failed: {str(e)}")

    # Extraction tasks - STANDARD first, then Playwright
    tasks = []
    if is_pdf_url(url):
        tasks.append(asyncio.create_task(extract_pdf(url, doc_type, url)))
    else:
        # Standard extraction first
        tasks.append(asyncio.create_task(extract_standard_html(url, doc_type, url)))
        # Then try playwright
        tasks.append(asyncio.create_task(extract_with_playwright(url, doc_type, url)))

    for task in tasks:
        try:
            res = await task
            if res.success:
                add_to_cache(cache_key, res.dict())
                for t in tasks:
                    if t is not task and not t.done(): t.cancel()
                return res
        except Exception as e:
            logger.warning(f"Extraction task failed: {str(e)}")
            continue

    return ExtractResponse(
        url=url, 
        document_type=doc_type, 
        text=None, 
        success=False,
        message='Extraction failed - all methods exhausted', 
        method_used='standard'
    )