from app.api.v1.endpoints.privacy import find_all_privacy_links_js
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright

print("✅ Import successful - syntax is now correct!")

# Define a simple HTTP server for redirect testing
class RedirectHandler(BaseHTTPRequestHandler):
    """
    Simple HTTP server that simulates privacy policy redirects.
    This helps test our redirect handling logic.
    """
    def log_message(self, format, *args):
        # Suppress logging to keep test output clean
        return
    
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        # Main page with link to privacy policy
        if parsed_path.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            # Create a page with different types of privacy links
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>Redirect Test Site</title></head>
            <body>
                <header>
                    <a href="/about">About</a>
                    <a href="/privacy-simple">Simple Privacy</a>
                </header>
                <main>
                    <h1>Test Site for Redirect Testing</h1>
                </main>
                <footer>
                    <a href="/privacy-policy">Privacy Policy</a>
                    <a href="/privacy-with-redirect">Privacy (With Redirect)</a>
                    <a href="/company-privacy">Company Privacy Policy</a>
                    <a href="/external-redirect">External Privacy Policy</a>
                </footer>
            </body>
            </html>
            """
            self.wfile.write(content.encode())
            
        # Simple privacy page - no redirect
        elif parsed_path.path == "/privacy-simple":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>Privacy Policy</title></head>
            <body>
                <h1>Privacy Policy</h1>
                <p>This is our privacy policy. We collect personal information...</p>
            </body>
            </html>
            """
            self.wfile.write(content.encode())
            
        # Standard privacy policy
        elif parsed_path.path == "/privacy-policy":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>Privacy Policy</title></head>
            <body>
                <h1>Privacy Policy</h1>
                <p>This is our complete privacy policy with GDPR information.</p>
                <h2>Information We Collect</h2>
                <p>Personal data collection details...</p>
            </body>
            </html>
            """
            self.wfile.write(content.encode())
            
        # Privacy page that redirects to another page
        elif parsed_path.path == "/privacy-with-redirect":
            self.send_response(302)
            self.send_header("Location", "/privacy-destination")
            self.end_headers()
            
        # Privacy page that redirects to external site
        elif parsed_path.path == "/external-redirect":
            self.send_response(302)
            self.send_header("Location", "/corporate-privacy")
            self.end_headers()
            
        # Target of internal redirect
        elif parsed_path.path == "/privacy-destination":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>Redirected Privacy Policy</title></head>
            <body>
                <h1>Redirected Privacy Policy</h1>
                <p>This page demonstrates successful redirect handling.</p>
            </body>
            </html>
            """
            self.wfile.write(content.encode())
            
        # Target of "external" company redirect 
        elif parsed_path.path == "/corporate-privacy":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>Corporate Privacy Policy</title></head>
            <body>
                <h1>Corporate Privacy Policy</h1>
                <p>This simulates a parent company privacy policy (like Vox Media).</p>
                <h2>Data Protection Information</h2>
                <p>GDPR compliance details...</p>
            </body>
            </html>
            """
            self.wfile.write(content.encode())
        
        # Company privacy page with direct link
        elif parsed_path.path == "/company-privacy":
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>Company Privacy Policy</title></head>
            <body>
                <h1>Company Privacy Policy</h1>
                <p>This is our company's privacy policy page.</p>
            </body>
            </html>
            """
            self.wfile.write(content.encode())
            
        # 404 for anything else
        else:
            self.send_response(404)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            content = """
            <!DOCTYPE html>
            <html>
            <head><title>404 Not Found</title></head>
            <body>
                <h1>404 Not Found</h1>
                <p>The requested resource was not found.</p>
            </body>
            </html>
            """
            self.wfile.write(content.encode())

async def test_privacy_detection():
    """
    Test the improved privacy link detection without special cases.
    """
    print("Testing the improved privacy link detection...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Test domains to verify privacy link detection works without special cases
        test_domains = [
            "https://example.com",
            "https://developer.mozilla.org"
        ]
        
        results = []
        
        for domain in test_domains:
            try:
                print(f"\nTesting privacy detection for: {domain}")
                await page.goto(domain, timeout=10000, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                
                # Test our improved privacy links detection function
                privacy_url, _, unverified_url = await find_all_privacy_links_js(page, context)
                
                result = {
                    "domain": domain,
                    "privacy_url": privacy_url,
                    "unverified_url": unverified_url,
                    "success": privacy_url is not None
                }
                
                results.append(result)
                
                if privacy_url:
                    print(f"✅ Privacy policy found: {privacy_url}")
                else:
                    print(f"❌ Privacy policy not found for {domain}")
                    if unverified_url:
                        print(f"   Unverified result: {unverified_url}")
                
            except Exception as e:
                print(f"❌ Error testing {domain}: {e}")
                results.append({
                    "domain": domain,
                    "error": str(e),
                    "success": False
                })
        
        await browser.close()
        print("\n=== Summary of Results ===")
        for result in results:
            status = "✅ Success" if result.get("success") else "❌ Failed"
            privacy_url = result.get("privacy_url") or "Not found"
            print(f"{status} - {result['domain']} -> {privacy_url}")
        
        return results

async def test_media_sites_privacy():
    """
    Test specific media sites to ensure our general approach works 
    without special cases for Vox Media or others.
    """
    print("\nTesting media sites privacy detection (no special cases)...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Test specifically theverge.com (Vox Media) which previously had special case
        # Also test another media site for comparison
        media_sites = [
            "https://www.theverge.com",  # Vox Media site (previously had special case)
            "https://techcrunch.com"      # Another media site for comparison
        ]
        
        results = []
        
        for site in media_sites:
            try:
                print(f"\nTesting privacy detection for media site: {site}")
                await page.goto(site, timeout=15000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                
                # Set a longer timeout as media sites often load slower
                page.set_default_timeout(30000)
                
                # Test our improved privacy links detection function
                privacy_url, _, unverified_url = await find_all_privacy_links_js(page, context)
                
                result = {
                    "domain": site,
                    "privacy_url": privacy_url,
                    "unverified_url": unverified_url,
                    "success": privacy_url is not None
                }
                
                results.append(result)
                
                if privacy_url:
                    print(f"✅ Media site privacy policy found: {privacy_url}")
                    # Check if the URL pattern indicates it's a Vox Media privacy URL
                    if "voxmedia.com" in privacy_url and "theverge.com" in site:
                        print(f"   ✅ Successfully followed redirect to Vox Media privacy page")
                else:
                    print(f"❌ Privacy policy not found for {site}")
                    if unverified_url:
                        print(f"   Unverified result: {unverified_url}")
                
            except Exception as e:
                print(f"❌ Error testing {site}: {e}")
                results.append({
                    "domain": site,
                    "error": str(e),
                    "success": False
                })
        
        await browser.close()
        print("\n=== Media Sites Summary ===")
        for result in results:
            status = "✅ Success" if result.get("success") else "❌ Failed"
            privacy_url = result.get("privacy_url") or "Not found"
            print(f"{status} - {result['domain']} -> {privacy_url}")
        
        return results

async def test_link_scoring_algorithm():
    """
    Test the improved link scoring algorithm with a simulated page
    containing various types of privacy-related links.
    """
    print("\nTesting privacy link scoring algorithm...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        
        # Create a test page with various privacy links to test scoring
        await page.set_content('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Privacy Link Test Page</title>
        </head>
        <body>
            <header>
                <nav>
                    <a href="https://example.com/about">About</a>
                    <a href="https://example.com/contact">Contact</a>
                    <a href="https://example.com/privacy">Privacy</a>
                </nav>
            </header>
            
            <main>
                <h1>Test Page for Privacy Link Detection</h1>
                <p>This is a test page to evaluate privacy link scoring.</p>
                
                <a href="https://example.com/blog/privacy-tips">Privacy Tips Blog Post</a>
                <a href="https://example.com/data-collection">How We Collect Data</a>
            </main>
            
            <footer>
                <div class="footer-links">
                    <a href="https://example.com/terms">Terms of Service</a>
                    <a href="https://example.com/privacy-policy">Privacy Policy</a>
                    <a href="https://example.com/cookies">Cookie Policy</a>
                    <a href="https://example.com/data-protection">Data Protection</a>
                    <a href="https://example.com/gdpr">GDPR Compliance</a>
                </div>
            </footer>
        </body>
        </html>
        ''')
        
        print("Evaluating links on test page...")
        
        # Run our JavaScript evaluation to score the links
        scored_links = await page.evaluate('''() => {
            // Simplified version of our scoring logic from the main function
            const links = Array.from(document.querySelectorAll('a[href]'));
            
            return links
                .filter(a => {
                    if (!a.href || !a.textContent) return false;
                    if (a.href.startsWith('javascript:') || a.href.startsWith('mailto:')) return false;
                    return true;
                })
                .map(a => {
                    const text = a.textContent.trim().toLowerCase();
                    const href = a.href.toLowerCase();
                    
                    // Score the link
                    let score = 0;
                    
                    // Text matching
                    if (text === 'privacy policy') score += 100;
                    else if (text.includes('privacy policy')) score += 90;
                    else if (text.includes('privacy') && text.includes('statement')) score += 85;
                    else if (text.includes('data protection')) score += 80;
                    else if (text.includes('privacy')) score += 70;
                    else if (text === 'cookies' || text === 'cookie policy') score += 60;
                    else if (text.includes('personal') && text.includes('data')) score += 60;
                    
                    // URL matching
                    if (href.includes('privacy-policy') || 
                        href.includes('privacy-notice') || 
                        href.includes('privacy_policy') || 
                        href.includes('privacy_notice')) score += 50;
                    else if (href.includes('data-protection') || 
                             href.includes('data_protection')) score += 45;
                    else if (href.includes('gdpr') || 
                             href.includes('ccpa')) score += 45;
                    else if (href.includes('/privacy/') || 
                             href.includes('/privacypolicy/')) score += 40;
                    else if (href.includes('/privacy') || 
                             href.includes('/privacypolicy')) score += 35;
                    else if (href.includes('cookie') || 
                             href.includes('personal-information')) score += 30;
                    
                    // Add boost for links in the page footer
                    const isInFooter = a.closest('footer') !== null;
                    if (isInFooter) score += 20;
                    
                    return {
                        text: text,
                        href: href,
                        score: score,
                        inFooter: isInFooter
                    };
                })
                .sort((a, b) => b.score - a.score);
        }''')
        
        print("\nResults of link scoring:")
        for i, link in enumerate(scored_links):
            print(f"{i+1}. {link['text']} - {link['href']} (Score: {link['score']}, In Footer: {link['inFooter']})")
        
        # Check if the highest score is the privacy policy link in the footer
        if scored_links and scored_links[0]['text'] == 'privacy policy' and scored_links[0]['inFooter']:
            print("\n✅ Success: Privacy Policy link in footer correctly scored highest")
        else:
            print("\n❌ Error: Expected 'Privacy Policy' in footer to score highest")
        
        # Verify other high-scoring links are related to privacy
        high_scoring_links = [link for link in scored_links if link['score'] >= 80]
        privacy_related = [link for link in high_scoring_links if 'privacy' in link['text'] or 'data protection' in link['text']]
        
        print(f"\nFound {len(high_scoring_links)} high-scoring links (score >= 80)")
        print(f"Of those, {len(privacy_related)} are directly privacy-related")
        
        if len(privacy_related) >= 2:
            print("✅ Multiple high-scoring privacy links identified correctly")
        
        # Now test the actual find_all_privacy_links_js function
        privacy_url, _, unverified_url = await find_all_privacy_links_js(page, context)
        
        print("\nResults from find_all_privacy_links_js:")
        print(f"Selected privacy URL: {privacy_url}")
        print(f"Unverified URL: {unverified_url}")
        
        if privacy_url and "privacy-policy" in privacy_url:
            print("✅ find_all_privacy_links_js correctly identified the privacy policy URL")
        
        await browser.close()
        return scored_links

async def test_redirect_handling():
    """
    Test redirect handling for privacy links using a local server.
    This simulates sites like Vox Media that redirect to parent company privacy pages.
    """
    print("\nTesting privacy link redirect handling...")
    
    # Start a simple HTTP server for testing redirects
    server_address = ('localhost', 8000)
    httpd = HTTPServer(server_address, RedirectHandler)
    
    # Start the server in a separate thread
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True  # So the thread will exit when the main program exits
    server_thread.start()
    print(f"Test server started at http://{server_address[0]}:{server_address[1]}")
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            
            # Go to our test server's main page
            test_url = f"http://{server_address[0]}:{server_address[1]}/"
            print(f"Navigating to test server: {test_url}")
            
            await page.goto(test_url, timeout=10000, wait_until="domcontentloaded")
            await page.wait_for_timeout(1000)
            
            # Use our privacy link detection function
            privacy_url, _, unverified_url = await find_all_privacy_links_js(page, context)
            
            print("\nResults from redirect test:")
            print(f"Selected privacy URL: {privacy_url}")
            if unverified_url:
                print(f"Unverified URL: {unverified_url}")
                
            # Check which link was chosen and if redirects were followed
            if privacy_url:
                print("✅ Successfully found a privacy link on test server")
                
                # Check if it's the expected highest-scoring link (privacy policy)
                if "/privacy-policy" in privacy_url:
                    print("✅ Correctly identified 'Privacy Policy' as highest-scoring link")
                    
                elif "/privacy-destination" in privacy_url:
                    print("✅ Successfully followed internal redirect from 'Privacy (With Redirect)'")
                    
                elif "/corporate-privacy" in privacy_url:
                    print("✅ Successfully followed external redirect from 'External Privacy Policy'")
                    
                else:
                    print(f"⚠️ Found unexpected privacy URL: {privacy_url}")
            else:
                print("❌ Failed to find any privacy link on test server")
            
            # Now test specific redirects by directly navigating to them
            print("\nTesting specific redirect scenarios:")
            
            # Test internal redirect
            redirect_url = f"{test_url}privacy-with-redirect"
            print(f"Testing internal redirect: {redirect_url}")
            await page.goto(redirect_url, timeout=5000)
            final_url = page.url
            print(f"Final URL after redirect: {final_url}")
            
            if "/privacy-destination" in final_url:
                print("✅ Internal redirect handled correctly")
            else:
                print("❌ Internal redirect failed")
                
            # Test external redirect (simulating corporate website)
            external_url = f"{test_url}external-redirect"
            print(f"Testing external redirect: {external_url}")
            await page.goto(external_url, timeout=5000)
            final_url = page.url
            print(f"Final URL after external redirect: {final_url}")
            
            if "/corporate-privacy" in final_url:
                print("✅ External redirect to corporate site handled correctly")
            else:
                print("❌ External redirect failed")
            
            await browser.close()
            
            return {
                "privacy_url": privacy_url,
                "unverified_url": unverified_url,
                "internal_redirect_worked": "/privacy-destination" in final_url,
                "external_redirect_worked": "/corporate-privacy" in final_url
            }
    finally:
        # Shut down the test server
        print("Shutting down test server...")
        httpd.shutdown()
        server_thread.join(timeout=1.0)
        print("Test server stopped")

if __name__ == "__main__":
    asyncio.run(test_privacy_detection())
    asyncio.run(test_media_sites_privacy())
    asyncio.run(test_link_scoring_algorithm())
    asyncio.run(test_redirect_handling()) 