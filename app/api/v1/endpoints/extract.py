from fastapi import APIRouter, Response, HTTPException
from pydantic import BaseModel, field_validator, HttpUrl
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import re
from typing import Optional, Any, List, Dict
import asyncio
from playwright.async_api import async_playwright
import logging
from html2text import html2text

# Filter out the XML parsed as HTML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


class ExtractRequest(BaseModel):
    url: str  # URL to extract text from
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Basic URL validation."""
        if not v:
            raise ValueError("URL cannot be empty")
        
        # Add scheme if missing
        if not v.startswith(('http://', 'https://')):
            v = 'https://' + v
        
        return v


class ExtractResponse(BaseModel):
    url: str  # Original URL that was processed
    success: bool  # Whether the extraction was successful
    text: Optional[str] = None  # Extracted text content
    message: str  # Status message
    method_used: str  # Method used for extraction (standard or playwright)


@router.post("/extract", response_model=ExtractResponse)
async def extract_text(request: ExtractRequest, response: Response) -> ExtractResponse:
    """
    Takes a URL (such as a Terms of Service or Privacy Policy URL) and extracts 
    the text content from the webpage.
    
    This endpoint uses multiple methods to attempt to extract text:
    1. Standard requests + BeautifulSoup
    2. Headless browser rendering with Playwright for JavaScript-heavy sites
    """
    url = request.url
    logger.info(f"Processing text extraction request for URL: {url}")
    
    # Try the standard method first
    try:
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
        }
        
        logger.info(f"Attempting standard extraction from: {url}")
        response_data = requests.get(url, headers=headers, timeout=30)
        response_data.raise_for_status()
        
        # Parse the HTML
        soup = BeautifulSoup(response_data.text, 'html.parser')
        
        # Extract text using standard method
        extracted_text = extract_content_from_soup(soup)
        
        if extracted_text:
            return ExtractResponse(
                url=url,
                success=True,
                text=extracted_text,
                message="Successfully extracted text content using standard method",
                method_used="standard"
            )
        
        # If standard extraction returns empty content, try Playwright
        logger.info("Standard extraction returned no content, trying Playwright")
    
    except Exception as e:
        logger.error(f"Standard extraction failed: {str(e)}")
        # If standard extraction fails, we'll fall through to the Playwright method
    
    # Try extracting with Playwright
    try:
        logger.info(f"Attempting extraction with Playwright from: {url}")
        playwright_text = await extract_with_playwright(url)
        
        if playwright_text:
            return ExtractResponse(
                url=url,
                success=True,
                text=playwright_text,
                message="Successfully extracted text content using JavaScript-enabled browser rendering",
                method_used="playwright"
            )
        else:
            # Both methods failed
            response.status_code = 404
            return ExtractResponse(
                url=url,
                success=False,
                text=None,
                message="Failed to extract meaningful text content from the URL",
                method_used="both_failed"
            )
            
    except Exception as e:
        logger.error(f"Playwright extraction failed: {str(e)}")
        response.status_code = 500
        return ExtractResponse(
            url=url,
            success=False,
            text=None,
            message=f"Error extracting text: {str(e)}",
            method_used="both_failed_with_error"
        )


def extract_content_from_soup(soup) -> str:
    """Extract and clean up text content from a BeautifulSoup object."""
    # Remove script and style elements
    for script_or_style in soup(['script', 'style', 'header', 'footer', 'nav']):
        script_or_style.extract()
    
    # Try to find the main content
    main_content = None
    
    # Look for common content containers
    content_candidates = []
    
    # Try to find article or main content areas
    for tag in ['article', 'main', 'div[role="main"]', '.content', '.main-content', '#content', '#main']:
        elements = soup.select(tag)
        content_candidates.extend(elements)
    
    # If we found potential content containers, use the largest one
    if content_candidates:
        main_content = max(content_candidates, key=lambda elem: len(elem.get_text(strip=True)))
    
    # If we couldn't find a clear main content, use the body
    if not main_content or len(main_content.get_text(strip=True)) < 100:
        main_content = soup.body
    
    if not main_content:
        main_content = soup
    
    # Convert HTML to clean text
    text = html2text(str(main_content))
    
    # Post-processing
    # Remove excessive whitespace and line breaks
    text = re.sub(r'\n\s*\n', '\n\n', text)
    
    # Remove URLs that appear as plain text
    text = re.sub(r'https?://\S+', '', text)
    
    return text.strip()


async def extract_with_playwright(url) -> str:
    """Extract text from a webpage using Playwright for JavaScript rendering."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        try:
            # Navigate to the URL with a longer timeout
            await page.goto(url, wait_until="networkidle", timeout=60000)
            
            # Wait a moment for any delayed JS rendering
            await page.wait_for_timeout(2000)
            
            # Scroll down to load any lazy-loaded content
            await page.evaluate("""
                window.scrollTo(0, document.body.scrollHeight * 0.2);
                setTimeout(() => { window.scrollTo(0, document.body.scrollHeight * 0.4); }, 500);
                setTimeout(() => { window.scrollTo(0, document.body.scrollHeight * 0.6); }, 1000);
                setTimeout(() => { window.scrollTo(0, document.body.scrollHeight * 0.8); }, 1500);
                setTimeout(() => { window.scrollTo(0, document.body.scrollHeight); }, 2000);
            """)
            
            # Wait for scrolling and any triggered content to load
            await page.wait_for_timeout(3000)
            
            # Try to find and click "Accept cookies" buttons to reveal content
            for selector in [
                'button:text-matches("accept", "i")', 
                'button:text-matches("agree", "i")',
                'button:text-matches("cookie", "i")',
                'button:text-matches("consent", "i")'
            ]:
                try:
                    buttons = await page.query_selector_all(selector)
                    for button in buttons:
                        await button.click()
                        await page.wait_for_timeout(1000)
                except:
                    continue
            
            # Get the page content after all processing
            content = await page.content()
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            
            # Extract text
            return extract_content_from_soup(soup)
            
        finally:
            await browser.close() 