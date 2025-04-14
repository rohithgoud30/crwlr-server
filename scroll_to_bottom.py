import asyncio
from playwright.async_api import async_playwright

async def scroll_to_bottom(url):
    """
    Scrolls to the bottom of a webpage using Playwright.
    
    Args:
        url: URL of the webpage to scroll
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            print(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded")
            
            # Get initial page height
            initial_height = await page.evaluate("document.body.scrollHeight")
            print(f"Initial page height: {initial_height}px")
            
            print("Starting scroll...")
            
            # Scroll down in steps
            current_position = 0
            last_position = -1
            
            while current_position != last_position:
                last_position = current_position
                
                # Scroll down
                current_position = await page.evaluate("""async () => {
                    const scrollHeight = document.body.scrollHeight;
                    window.scrollTo(0, scrollHeight);
                    await new Promise(r => setTimeout(r, 1000));
                    return window.pageYOffset;
                }""")
                
                print(f"Scrolled to position: {current_position}px")
                
                # Wait for any lazy-loaded content
                await page.wait_for_timeout(1000)
            
            final_height = await page.evaluate("document.body.scrollHeight")
            print(f"Final page height: {final_height}px")
            print("Reached bottom of page")
            
            # Optional: take a screenshot
            await page.screenshot(path="bottom_screenshot.png")
            print("Screenshot saved as bottom_screenshot.png")
            
        except Exception as e:
            print(f"Error: {e}")
        finally:
            await browser.close()

async def main():
    url = "https://example.com"  # Replace with your target URL
    await scroll_to_bottom(url)

if __name__ == "__main__":
    asyncio.run(main()) 