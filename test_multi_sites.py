import asyncio
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
from app.api.v1.endpoints.privacy import find_privacy_link
from playwright.async_api import async_playwright
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Comprehensive list of sites to test
sites = [
    "https://www.theverge.com",
    "https://nodejs.org",
    "https://www.facebook.com",
    "https://www.amazon.com",
    "https://www.google.com",
    "https://www.instagram.com",
    "https://github.com",
    "https://www.pornhub.com",
    "https://www.youporn.com",
    "https://meta.ai",
    "https://unsplash.com"
]

async def test_with_playwright(url):
    """Test finding privacy policy with Playwright for JavaScript-heavy sites"""
    try:
        logger.info(f"Testing with Playwright: {url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            # Navigate to the site
            await page.goto(url, wait_until="networkidle", timeout=30000)
            
            # Get the content
            content = await page.content()
            
            # Parse with BeautifulSoup
            soup = BeautifulSoup(content, 'html.parser')
            
            # Find privacy link using the enhanced function
            privacy_link = find_privacy_link(url, soup)
            
            # If found, verify by visiting the link
            if privacy_link:
                try:
                    privacy_page = await context.new_page()
                    await privacy_page.goto(privacy_link, wait_until="domcontentloaded", timeout=20000)
                    final_url = privacy_page.url
                    
                    # Check title for verification
                    title = await privacy_page.title()
                    content = await privacy_page.content()
                    privacy_soup = BeautifulSoup(content, 'html.parser')
                    
                    # Simple verification of content
                    body_text = privacy_soup.get_text().lower()
                    has_privacy_terms = any(term in body_text for term in ['privacy', 'personal data', 'information we collect'])
                    
                    await privacy_page.close()
                    
                    return {
                        'url': url,
                        'privacy_link': privacy_link,
                        'final_url': final_url,
                        'title': title,
                        'verified': has_privacy_terms
                    }
                except Exception as e:
                    logger.error(f"Error verifying privacy link for {url}: {str(e)}")
                    return {
                        'url': url,
                        'privacy_link': privacy_link,
                        'verified': False,
                        'error': str(e)
                    }
            else:
                return {
                    'url': url,
                    'privacy_link': None,
                    'verified': False
                }
            
            await browser.close()
    except Exception as e:
        logger.error(f"Error testing {url} with Playwright: {str(e)}")
        return {
            'url': url,
            'privacy_link': None,
            'verified': False,
            'error': str(e)
        }

def test_with_requests(url):
    """Test finding privacy policy with standard requests+BS4 approach"""
    try:
        logger.info(f"Testing with requests: {url}")
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        
        # Make the request
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find privacy link using the enhanced function
        privacy_link = find_privacy_link(url, soup)
        
        return {
            'url': url,
            'privacy_link': privacy_link,
            'verified': privacy_link is not None
        }
    except Exception as e:
        logger.error(f"Error testing {url} with requests: {str(e)}")
        return {
            'url': url,
            'privacy_link': None,
            'verified': False,
            'error': str(e)
        }

async def main():
    results = []
    
    # First try with standard requests for all sites
    standard_results = []
    for url in sites:
        try:
            result = test_with_requests(url)
            standard_results.append(result)
            # If successful, add to results
            if result['privacy_link']:
                results.append(result)
                logger.info(f"Found privacy link for {url} with standard method: {result['privacy_link']}")
            else:
                # If not found, try with Playwright
                logger.info(f"No privacy link found for {url} with standard method, trying Playwright...")
                playwright_result = await test_with_playwright(url)
                results.append(playwright_result)
                if playwright_result['privacy_link']:
                    logger.info(f"Found privacy link for {url} with Playwright: {playwright_result['privacy_link']}")
                else:
                    logger.warning(f"No privacy link found for {url} with any method")
        except Exception as e:
            logger.error(f"Error processing {url}: {str(e)}")
    
    # Print final results
    print("\n===== FINAL RESULTS =====")
    success_count = 0
    for i, result in enumerate(results):
        url = result['url']
        privacy_link = result['privacy_link']
        verified = result.get('verified', False)
        
        status = "✅ SUCCESS" if privacy_link else "❌ FAILED"
        if privacy_link and verified:
            status = "✅ VERIFIED"
            success_count += 1
        elif privacy_link:
            status = "⚠️ FOUND BUT NOT VERIFIED"
            success_count += 1
        
        print(f"{i+1}. {url}: {status}")
        if privacy_link:
            print(f"   Privacy Link: {privacy_link}")
        if 'error' in result:
            print(f"   Error: {result['error']}")
        print()
    
    # Summary
    print(f"Summary: {success_count}/{len(sites)} sites successfully found privacy links")
    success_rate = (success_count / len(sites)) * 100
    print(f"Success rate: {success_rate:.2f}%")

if __name__ == "__main__":
    asyncio.run(main()) 