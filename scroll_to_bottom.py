import asyncio
import re
from playwright.async_api import async_playwright

# Define your strong match terms here
strong_terms_matches = [
    'terms of service', 'terms of use', 'terms and conditions',
    'conditions of use', 'condition of use', 'user agreement',
    'terms', 'tos', 'eula', 'legal terms'
]

async def find_all_links_js(page, context):
    print("Searching for all links using JavaScript...")
    
    # Check for Cloudflare challenge page
    is_cloudflare = await page.evaluate("""() => {
        const html = document.documentElement.innerHTML.toLowerCase();
        return html.includes('cloudflare') && 
               (html.includes('challenge') || 
                html.includes('security check') || 
                html.includes('captcha') || 
                html.includes('verify your browser'));
    }""")
    
    if is_cloudflare:
        print("Detected Cloudflare challenge page, cannot extract reliable links")
        return None, page
    
    # Find all links using JS (this gets everything without scrolling)
    links = await page.evaluate("""() => {
        const allElements = document.querySelectorAll('a');
        const links = [];
        
        // Define exact match priorities (higher = more preferred)
        const exactMatchPriorities = {
            'terms of service': 100,
            'terms of use': 95,
            'terms and conditions': 90,
            'conditions of use': 85,
            'condition of use': 80,
            'user agreement': 75,
            'terms': 70,
            'tos': 65,
            'eula': 60,
            'legal terms': 55,
            'legal': 50,
            'privacy policy': 45,
            'privacy': 40
        };
        
        allElements.forEach(el => {
            const text = (el.textContent || '').toLowerCase().trim();
            const href = el.getAttribute('href') || '';
            
            // Skip Cloudflare challenge links
            if (href.includes('cloudflare.com') && 
                (href.includes('challenge') || href.includes('utm_source=challenge'))) {
                return; // Skip this link
            }
            
            // Skip links with Cloudflare challenge text
            if (text.includes('cloudflare') && 
                (text.includes('challenge') || text.includes('security check'))) {
                return; // Skip this link
            }
            
            // Check if the text or href contains any of our target terms
            if (text.includes('terms') || 
                text.includes('tos') || 
                text.includes('conditions') || 
                text.includes('eula') || 
                text.includes('agreement') ||
                text.includes('conditions of use') ||
                text.includes('condition of use') ||
                href.includes('terms') || 
                href.includes('tos') || 
                href.includes('conditions') || 
                href.includes('eula') || 
                href.includes('agreement') ||
                href.includes('conditions-of-use') ||
                href.includes('condition-of-use') ||
                href.includes('legal') ||
                href.includes('privacy')
            ) {
                // Calculate priority score
                let priority = 0;
                
                // Check for exact matches first (highest priority)
                for (const [exactMatch, score] of Object.entries(exactMatchPriorities)) {
                    if (text === exactMatch) {
                        priority = score;
                        break;
                    }
                }
                
                // If no exact match, check for contains (lower priority)
                if (priority === 0) {
                    if (text.includes('conditions of use')) priority = 45;
                    else if (text.includes('condition of use')) priority = 44;
                    else if (text.includes('terms of service')) priority = 40;
                    else if (text.includes('terms of use')) priority = 38;
                    else if (text.includes('terms')) priority = 35;
                    else if (text.includes('conditions')) priority = 30;
                    else if (text.includes('agreement')) priority = 25;
                    else if (text.includes('tos')) priority = 20;
                    else if (text.includes('eula')) priority = 15;
                    else if (href.includes('terms')) priority = 10;
                    else if (href.includes('legal')) priority = 5;
                }
                
                // Get the absolute URL for relative links
                let absoluteHref = href;
                if (href && !href.startsWith('http') && !href.startsWith('//')) {
                    // Handle relative URLs
                    const base = document.baseURI;
                    // If href starts with /, join with origin
                    if (href.startsWith('/')) {
                        const origin = new URL(base).origin;
                        absoluteHref = origin + href;
                    } else {
                        // Otherwise, resolve against base URI
                        absoluteHref = new URL(href, base).href;
                    }
                }
                
                links.push({
                    text: (el.textContent || '').trim(),
                    href: href, // Original href
                    absoluteHref: absoluteHref, // Absolute href
                    x: el.getBoundingClientRect().left,
                    y: el.getBoundingClientRect().top,
                    width: el.getBoundingClientRect().width,
                    height: el.getBoundingClientRect().height,
                    priority: priority  // Add priority score
                });
            }
        });
        
        // Sort links by priority (highest first)
        links.sort((a, b) => b.priority - a.priority);
        
        return links;
    }""")
    
    print(f"Found {len(links)} potential matching links via JavaScript")
    
    if len(links) > 0:
        for link in links:
            href_display = link['href'] if link['href'] else '(no href)'
            abs_href_display = link['absoluteHref'] if 'absoluteHref' in link and link['absoluteHref'] else '(no absolute href)'
            priority_display = link['priority'] if 'priority' in link else '(no priority)'
            print(f"Potential link: {link['text']} → {href_display} (absolute: {abs_href_display}) [Priority: {priority_display}]")
            
            try:
                # Skip Cloudflare challenge links
                if (('absoluteHref' in link and link['absoluteHref'] and 'cloudflare.com' in link['absoluteHref'] and 
                     ('challenge' in link['absoluteHref'] or 'utm_source=challenge' in link['absoluteHref'])) or
                    (link['href'] and 'cloudflare.com' in link['href'] and 
                     ('challenge' in link['href'] or 'utm_source=challenge' in link['href']))):
                    print(f"Skipping Cloudflare challenge URL: {abs_href_display}")
                    continue
                
                # Try direct navigation for absolute URLs (more reliable than clicking)
                if 'absoluteHref' in link and link['absoluteHref'] and link['absoluteHref'].startswith('http'):
                    try:
                        print(f"Navigating directly to: {link['absoluteHref']}")
                        # Use a shorter timeout for navigation
                        await page.goto(link['absoluteHref'], timeout=3000, wait_until='domcontentloaded')
                        
                        # Check if we landed on a Cloudflare challenge page
                        is_cloudflare_challenge = await page.evaluate("""() => {
                            const html = document.documentElement.innerHTML.toLowerCase();
                            return html.includes('cloudflare') && 
                                   (html.includes('challenge') || 
                                    html.includes('security check') || 
                                    html.includes('captcha') || 
                                    html.includes('verify your browser'));
                        }""")
                        
                        if is_cloudflare_challenge:
                            print("Landed on a Cloudflare challenge page, continuing search")
                            continue
                        
                        print(f"Direct navigation successful to: {page.url}")
                        return page.url, page
                    except Exception as e:
                        print(f"Direct navigation failed: {e}")
                        # Fall back to clicking
                
                # If direct navigation failed or wasn't attempted, try clicking
                element = None
                
                # Try different selectors to find the element
                if link['href']:
                    # Exact href match
                    element = await page.query_selector(f"a[href='{link['href']}']")
                    
                    # If that fails, try contains match
                    if not element and not link['href'].startswith('http'):
                        element = await page.query_selector(f"a[href*='{link['href'].replace('/', '')}']")
                
                # If still not found, try by text
                if not element:
                    # Try exact text match
                    clean_text = link['text'].replace("'", "\\'").strip()
                    element = await page.query_selector(f"text='{clean_text}'")
                    
                    # If that fails, try partial text match
                    if not element:
                        # Get a shorter version of the text for partial matching
                        short_text = clean_text.split(' ')[0] if ' ' in clean_text else clean_text
                        if len(short_text) > 3:  # Only if it's not too short
                            element = await page.query_selector(f"text='{short_text}'")
                
                if element:
                    print(f"Found link: '{link['text']}' — Clicking...")
                    
                    # Store starting URL
                    starting_url = page.url
                    
                    # Set up BOTH event handlers with shorter timeouts
                    page_promise = None
                    nav_promise = None
                    
                    try:
                        # Much shorter timeout for new tab - just 1 second
                        page_promise = context.wait_for_event('page', timeout=1000)
                    except:
                        page_promise = None
                        
                    try:
                        # Set up navigation listener but with shorter timeout
                        nav_promise = page.wait_for_navigation(timeout=2000)
                    except:
                        nav_promise = None
                    
                    # Click the element - use middle of the element
                    try:
                        if 'x' in link and 'y' in link and 'width' in link and 'height' in link:
                            # Click in the middle of the element by coordinates
                            x = link['x'] + link['width']/2
                            y = link['y'] + link['height']/2
                            await page.mouse.click(x, y)
                        else:
                            # Fall back to regular click
                            await element.click()
                    except Exception as click_error:
                        print(f"Click failed, trying alternate methods: {click_error}")
                        try:
                            # Try JavaScript click as fallback
                            await page.evaluate("(element) => element.click()", element)
                        except:
                            # Last resort: try navigation via href
                            if link['absoluteHref']:
                                await page.goto(link['absoluteHref'], timeout=3000)
                    
                    # Check for URL change FIRST (fastest check)
                    await page.wait_for_timeout(300)  # Brief delay
                    if page.url != starting_url:
                        print(f"URL changed to: {page.url}")
                        return page.url, page
                    
                    # Then check for new page/tab
                    try:
                        if page_promise:
                            try:
                                new_page = await page_promise
                                await new_page.wait_for_load_state('domcontentloaded')
                                print(f"New tab opened with URL: {new_page.url}")
                                return new_page.url, new_page
                            except Exception as e:
                                print(f"No new tab opened within timeout: {e}")
                                # Clean up
                                page_promise = None
                    except Exception as e:
                        print(f"Error handling new tab: {e}")
                        page_promise = None
                    
                    # Last check navigation in current page
                    try:
                        if nav_promise:
                            try:
                                await nav_promise
                                if page.url != starting_url:
                                    print(f"Page navigated to: {page.url}")
                                    return page.url, page
                            except Exception as e:
                                print(f"Navigation didn't complete within timeout: {e}")
                                # Clean up
                                nav_promise = None
                    except Exception as e:
                        print(f"Error handling navigation: {e}")
                        nav_promise = None
                    
                    # Final URL change check with slightly longer delay
                    await page.wait_for_timeout(500)
                    if page.url != starting_url:
                        print(f"URL changed to: {page.url}")
                        return page.url, page
                    
                    # As a very last resort, try direct navigation if we have absolute href
                    if 'absoluteHref' in link and link['absoluteHref'] and link['absoluteHref'].startswith('http'):
                        try:
                            print(f"Last resort: Navigating directly to: {link['absoluteHref']}")
                            await page.goto(link['absoluteHref'], timeout=3000)
                            print(f"Direct navigation successful to: {page.url}")
                            return page.url, page
                        except:
                            pass
                    
                    print("No navigation detected after click")
                    
            except Exception as e:
                print(f"Error with link: {e}")
                continue
    
    return None, page

async def find_matching_link(page, context):
    # Try matching each pattern using a case-insensitive partial match
    for keyword in strong_terms_matches:
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        element = await page.query_selector(f"text=/{pattern.pattern}/i")
        if element:
            print(f"Found link matching: '{keyword}' — Clicking...")
            
            # Store starting URL
            starting_url = page.url
            
            # Set up BOTH event handlers with shorter timeouts
            page_promise = None
            nav_promise = None
            
            try:
                # Much shorter timeout for new tab - just 1 second
                page_promise = context.wait_for_event('page', timeout=1000)
            except:
                page_promise = None
                
            try:
                # Set up navigation listener but with shorter timeout
                nav_promise = page.wait_for_navigation(timeout=2000)
            except:
                nav_promise = None
            
            # Click the element
            await element.click()
            
            # Check for URL change FIRST (fastest check)
            await page.wait_for_timeout(300)  # Brief delay
            if page.url != starting_url:
                print(f"URL changed to: {page.url}")
                return page.url, page
            
            # Then check for new page/tab
            try:
                if page_promise:
                    try:
                        new_page = await page_promise
                        await new_page.wait_for_load_state('domcontentloaded')
                        print(f"New tab opened with URL: {new_page.url}")
                        return new_page.url, new_page
                    except Exception as e:
                        print(f"No new tab opened within timeout: {e}")
                        # Clean up
                        page_promise = None
            except Exception as e:
                print(f"Error handling new tab: {e}")
                page_promise = None
            
            # Last check navigation in current page
            try:
                if nav_promise:
                    try:
                        await nav_promise
                        if page.url != starting_url:
                            print(f"Page navigated to: {page.url}")
                            return page.url, page
                    except Exception as e:
                        print(f"Navigation didn't complete within timeout: {e}")
                        # Clean up
                        nav_promise = None
            except Exception as e:
                print(f"Error handling navigation: {e}")
                nav_promise = None
            
            # Final URL change check with slightly longer delay
            await page.wait_for_timeout(500)
            if page.url != starting_url:
                print(f"URL changed to: {page.url}")
                return page.url, page
            
            print("No navigation detected after click")
                
    return None, page

async def smooth_scroll_and_click(page, context, step=150, delay=250):
    print("Starting smooth scroll with strong term matching...")
    visited_url = None
    current_page = page
    
    # First check visible links before scrolling
    visited_url, current_page = await find_matching_link(current_page, context)
    if visited_url:
        return visited_url, current_page
    
    # Add a specific check for footer elements that might contain terms links
    try:
        # Common footer selectors
        footer_selectors = ["footer", ".footer", "#footer", "[role='contentinfo']", 
                           ".site-footer", ".page-footer", ".bottom", ".legal"]
        
        for selector in footer_selectors:
            footer = await current_page.query_selector(selector)
            if footer:
                print(f"Found footer with selector: {selector}")
                # Make sure it's in view
                await footer.scroll_into_view_if_needed()
                await current_page.wait_for_timeout(500)  # Give it time to render
                
                # Check for terms links in the footer
                visited_url, current_page = await find_matching_link(current_page, context)
                if visited_url:
                    return visited_url, current_page
                
                # Special check for links that have "terms" in their href but not text
                terms_links = await current_page.evaluate("""(selector) => {
                    const footer = document.querySelector(selector);
                    if (!footer) return [];
                    
                    const links = Array.from(footer.querySelectorAll('a'));
                    return links.filter(link => {
                        const href = link.getAttribute('href') || '';
                        return href.toLowerCase().includes('terms') || 
                               href.toLowerCase().includes('legal') || 
                               href.includes('conditions') ||
                               href.includes('tos');
                    }).map(link => ({
                        text: link.textContent.trim(),
                        href: link.getAttribute('href')
                    }));
                }""", selector)
                
                if terms_links and len(terms_links) > 0:
                    print(f"Found {len(terms_links)} potential terms links in footer")
                    for link in terms_links:
                        print(f"Footer link: {link['text']} → {link['href']}")
                        try:
                            element = await current_page.query_selector(f"a[href='{link['href']}']")
                            if element:
                                print(f"Clicking on footer link: {link['text']}")
                                await element.click()
                                await current_page.wait_for_load_state('domcontentloaded')
                                print(f"Navigated to: {current_page.url}")
                                return current_page.url, current_page
                        except Exception as e:
                            print(f"Error clicking footer link: {e}")
                            # Try direct navigation as fallback
                            try:
                                href = link['href']
                                if not href.startswith('http'):
                                    # Handle relative URLs
                                    if href.startswith('/'):
                                        href = current_page.url.split('/')[0] + '//' + current_page.url.split('/')[2] + href
                                    else:
                                        href = current_page.url.split('?')[0] + '/' + href
                                
                                print(f"Direct navigation to: {href}")
                                await current_page.goto(href, timeout=3000)
                                print(f"Navigated to: {current_page.url}")
                                return current_page.url, current_page
                            except Exception as nav_error:
                                print(f"Navigation error: {nav_error}")
    except Exception as e:
        print(f"Footer check error: {e}")
    
    # Try an extreme measure - check ALL links on the page for terms in the href
    print("Checking all links on the page for terms-related hrefs...")
    try:
        all_term_links = await current_page.evaluate("""() => {
            const allLinks = Array.from(document.querySelectorAll('a'));
            const termLinks = allLinks.filter(link => {
                const href = link.getAttribute('href') || '';
                const text = link.textContent.toLowerCase().trim();
                
                return href.includes('terms') || 
                       href.includes('tos') || 
                       href.includes('conditions') || 
                       href.includes('legal') ||
                       text.includes('terms') ||
                       text.includes('conditions');
            });
            
            return termLinks.map(link => ({
                text: link.textContent.trim(),
                href: link.getAttribute('href')
            }));
        }""")
        
        if all_term_links and len(all_term_links) > 0:
            print(f"Found {len(all_term_links)} potential terms links across the page")
            for link in all_term_links:
                print(f"Potential term link: {link['text']} → {link['href']}")
                try:
                    element = await current_page.query_selector(f"a[href='{link['href']}']")
                    if element:
                        print(f"Clicking on term link: {link['text']}")
                        
                        # Direct navigation is more reliable in many cases
                        href = link['href']
                        if not href.startswith('http'):
                            # Handle relative URLs
                            base_url = current_page.url.split('?')[0]
                            if href.startswith('/'):
                                origin = '/'.join(base_url.split('/')[:3])  # Get http(s)://domain.com
                                href = origin + href
                            else:
                                href = base_url + '/' + href
                        
                        print(f"Direct navigation to: {href}")
                        await current_page.goto(href, timeout=3000)
                        print(f"Navigated to: {current_page.url}")
                        return current_page.url, current_page
                except Exception as e:
                    print(f"Error with term link: {e}")
                    continue
    except Exception as e:
        print(f"All links check error: {e}")
    
    # If still not found, do the regular scroll
    scroll_attempts = 0
    max_scroll_attempts = 20  # Limit the number of scrolls to prevent infinite loop
    
    while scroll_attempts < max_scroll_attempts:
        # Scroll by a small step
        reached_end = await current_page.evaluate(
            """async (step) => {
                const currentScroll = window.scrollY;
                const maxScroll = document.body.scrollHeight - window.innerHeight;
                window.scrollBy(0, step);
                await new Promise(r => setTimeout(r, 100));  // Small pause for rendering
                return currentScroll + step >= maxScroll || window.scrollY >= maxScroll;
            }""", step
        )

        await current_page.wait_for_timeout(delay)
        
        # Try to find links after each scroll
        visited_url, current_page = await find_matching_link(current_page, context)
        if visited_url:
            return visited_url, current_page
        
        scroll_attempts += 1

        if reached_end:
            print("Reached the bottom of the page.")
            
            # One final check with a longer wait time at the bottom
            await current_page.wait_for_timeout(1000)  # Wait longer to ensure everything loaded
            visited_url, current_page = await find_matching_link(current_page, context)
            if visited_url:
                return visited_url, current_page
            break

    # If still not found after scrolling, try a different approach with JavaScript
    if not visited_url:
        print("No link found through scrolling. Trying full-page scan...")
        
        # Execute JavaScript to find all links in the page, including those in the footer
        links = await current_page.evaluate("""() => {
            const allLinks = Array.from(document.querySelectorAll('a'));
            const footerLinks = allLinks.filter(link => {
                // Check if link is in a footer or bottom section
                const isInFooter = link.closest('footer') || 
                                   link.closest('[role="contentinfo"]') ||
                                   link.closest('.footer') || 
                                   link.closest('#footer') ||
                                   link.closest('.bottom') ||
                                   (link.getBoundingClientRect().top > window.innerHeight * 0.7);
                
                if (!isInFooter) return false;
                
                const text = link.textContent.toLowerCase();
                const href = link.getAttribute('href') || '';
                
                return text.includes('terms') || 
                       text.includes('conditions') ||
                       text.includes('tos') ||
                       href.includes('terms') || 
                       href.includes('tos') ||
                       href.includes('legal');
            });
            
            return footerLinks.map(link => ({
                text: link.textContent.trim(),
                href: link.getAttribute('href')
            }));
        }""")
        
        print(f"Found {len(links)} potential terms links in footer/bottom area")
        
        for link in links:
            print(f"Potential footer link: {link['text']} → {link['href']}")
            
            # Try to click on this link
            try:
                element = await current_page.query_selector(f"a[href='{link['href']}']")
                if element:
                    print(f"Clicking on footer link: {link['text']}")
                    await element.click()
                    await current_page.wait_for_load_state('domcontentloaded')
                    print(f"Navigated to: {current_page.url}")
                    return current_page.url, current_page
            except Exception as e:
                print(f"Error clicking footer link: {e}")
    
    # Use content analysis to detect likely areas where terms links might appear
    print("Using semantic content analysis to detect terms areas...")
    try:
        # Look for semantic hints about where terms might be found
        semantic_hints = await current_page.evaluate("""() => {
            // Function to score an element based on terms-related content
            function scoreElement(element) {
                if (!element) return 0;
                
                const text = element.textContent.toLowerCase();
                const tagName = element.tagName.toLowerCase();
                let score = 0;
                
                // Check for terms-related text
                if (text.includes('terms')) score += 10;
                if (text.includes('legal')) score += 8;
                if (text.includes('conditions')) score += 7;
                if (text.includes('use')) score += 5;
                if (text.includes('service')) score += 5;
                if (text.includes('agreement')) score += 6;
                if (text.includes('copyright')) score += 4;
                
                // Heading elements are more significant
                if (tagName === 'h1' || tagName === 'h2' || tagName === 'h3') {
                    score *= 2;
                }
                
                // Density of terms-related words
                const wordCount = text.split(/\\s+/).length;
                if (wordCount > 0) {
                    const termsWords = ['terms', 'legal', 'conditions', 'use', 'service', 'agreement', 'copyright'];
                    let termsWordCount = 0;
                    termsWords.forEach(word => {
                        const regex = new RegExp(word, 'gi');
                        const matches = text.match(regex);
                        if (matches) termsWordCount += matches.length;
                    });
                    
                    const density = termsWordCount / wordCount;
                    score += density * 20; // Boost score based on density
                }
                
                return score;
            }
            
            // Find elements with significant terms-related content
            const allElements = document.querySelectorAll('div, section, article, nav, footer, aside');
            const scoredElements = Array.from(allElements).map(el => {
                return {
                    element: el,
                    score: scoreElement(el),
                    text: el.textContent.trim().substring(0, 100),
                    path: el.tagName + (el.id ? '#' + el.id : '') + 
                          (el.className ? '.' + el.className.replace(/\\s+/g, '.') : '')
                };
            }).filter(item => item.score > 15); // Only keep elements with significant scores
            
            // Sort by score (highest first)
            scoredElements.sort((a, b) => b.score - a.score);
            
            // Return top candidates
            return scoredElements.slice(0, 5);
        }""")
        
        print(f"Found {len(semantic_hints)} potential terms-related content areas")
        
        for hint in semantic_hints:
            print(f"Content area: {hint['path']} (Score: {hint['score']}) - Preview: {hint['text']}")
            
            # Try to find links in these high-scoring areas
            try:
                area_links = await current_page.evaluate("""(path) => {
                    try {
                        const container = document.querySelector(path);
                        if (!container) return [];
                        
                        return Array.from(container.querySelectorAll('a')).map(link => ({
                            text: link.textContent.trim(),
                            href: link.getAttribute('href'),
                            rect: link.getBoundingClientRect()
                        }));
                    } catch (e) {
                        return [];
                    }
                }""", hint['path'])
                
                for link in area_links:
                    if not link.get('href'):
                        continue
                        
                    print(f"Found link in terms area: {link['text']} → {link['href']}")
                    
                    # Try clicking the link
                    try:
                        # Try direct navigation if we have a valid href
                        href = link['href']
                        if href:
                            if not href.startswith('http'):
                                # Handle relative URLs
                                base_url = current_page.url.split('?')[0]
                                if href.startswith('/'):
                                    origin = '/'.join(base_url.split('/')[:3])  # Get http(s)://domain.com
                                    href = origin + href
                                else:
                                    href = base_url + '/' + href
                            
                            print(f"Direct navigation to potential terms page: {href}")
                            response = await current_page.goto(href, timeout=3000)
                            
                            # Verify if this page looks like a terms page
                            is_terms_page = await current_page.evaluate("""() => {
                                const title = document.title.toLowerCase();
                                const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map(h => h.textContent.toLowerCase());
                                const bodyText = document.body.textContent.toLowerCase();
                                
                                // Check title
                                const titleHasTerms = 
                                    title.includes('terms') || 
                                    title.includes('conditions') || 
                                    title.includes('legal');
                                
                                // Check headings
                                const headingsHaveTerms = headings.some(h => 
                                    h.includes('terms') || 
                                    h.includes('conditions') || 
                                    h.includes('legal agreements'));
                                
                                // Check content
                                const contentIsLegal = 
                                    (bodyText.includes('terms') && bodyText.includes('use')) ||
                                    (bodyText.includes('terms') && bodyText.includes('service')) ||
                                    (bodyText.includes('agree') && bodyText.includes('terms'));
                                
                                // Check content length - terms pages are usually lengthy
                                const isLongText = bodyText.length > 2000;
                                
                                // Combine signals
                                return (titleHasTerms || headingsHaveTerms) && contentIsLegal && isLongText;
                            }""")
                            
                            if is_terms_page:
                                print(f"Found terms page: {current_page.url}")
                                return current_page.url, current_page
                            else:
                                print("Not a terms page, continuing search...")
                                # Navigate back for next attempt
                                await current_page.goto(page.url, timeout=3000)
                    except Exception as e:
                        print(f"Error navigating to potential terms page: {e}")
            except Exception as e:
                print(f"Error analyzing content area: {e}")
                
    except Exception as e:
        print(f"Content analysis error: {e}")
    
    # Final attempt: analyze document structure and try browser back functionality
    print("Analyzing document structure to locate terms...")
    
    try:
        # Look for terms-related links in less common locations
        structure_links = await current_page.evaluate("""() => {
            // Score a link based on how likely it is to be a terms link
            function scorePotentialTermsLink(link) {
                if (!link) return 0;
                
                const text = link.textContent.toLowerCase().trim();
                const href = link.getAttribute('href') || '';
                let score = 0;
                
                // Filter out Cloudflare challenge links
                if (href.includes('cloudflare.com') && 
                    (href.includes('challenge') || href.includes('utm_source=challenge'))) {
                    return -100; // Strong negative score to exclude Cloudflare challenge links
                }
                
                // Text content scoring
                if (text === 'terms' || text === 'terms of use' || text === 'terms of service') {
                    score += 100;
                } else if (text.includes('terms of use') || text.includes('terms of service')) {
                    score += 90;
                } else if (text.includes('terms') && text.includes('conditions')) {
                    score += 85;
                } else if (text.includes('terms')) {
                    score += 70;
                } else if (text.includes('legal')) {
                    score += 50;
                } else if (text.includes('conditions')) {
                    score += 40;
                }
                
                // Exclude Cloudflare text matches
                if (text.includes('cloudflare') && 
                    (text.includes('challenge') || text.includes('security') || text.includes('check'))) {
                    return -100; // Exclude Cloudflare security checks
                }
                
                // URL scoring
                if (href.includes('terms-of-use') || href.includes('terms-of-service')) {
                    score += 50;
                } else if (href.includes('terms')) {
                    score += 30;
                } else if (href.includes('legal')) {
                    score += 20;
                } else if (href.includes('conditions')) {
                    score += 15;
                }
                
                // Location scoring
                const position = link.getBoundingClientRect();
                // Links at the very bottom of the page are often terms links
                if (position.top > window.innerHeight * 0.8) {
                    score += 20;
                }
                
                // Parent element scoring
                const parent = link.parentElement;
                if (parent) {
                    const parentText = parent.textContent.toLowerCase();
                    if (parentText.includes('legal') || parentText.includes('copyright') || 
                        parentText.includes('terms') || parentText.includes('© ')) {
                        score += 25;
                    }
                    
                    // Small font sizes often indicate footer/legal links
                    const style = window.getComputedStyle(link);
                    if (parseInt(style.fontSize) < 14) {
                        score += 15;
                    }
                }
                
                return score;
            }
            
            // Find all links on the page
            const allLinks = Array.from(document.querySelectorAll('a[href]'));
            
            // Score each link
            const scoredLinks = allLinks.map(link => {
                return {
                    text: link.textContent.trim(),
                    href: link.getAttribute('href'),
                    score: scorePotentialTermsLink(link)
                };
            }).filter(item => item.score > 30); // Only keep likely candidates
            
            // Sort by score
            scoredLinks.sort((a, b) => b.score - a.score);
            
            return scoredLinks.slice(0, 10); // Return top 10 candidates
        }""")
        
        print(f"Found {len(structure_links)} potential terms links from structural analysis")
        
        for link in structure_links:
            print(f"Structure link: {link['text']} → {link['href']} (Score: {link['score']})")
            
            try:
                # Skip Cloudflare challenge URLs
                if (link['href'] and 'cloudflare.com' in link['href'] and 
                    ('challenge' in link['href'] or 'utm_source=challenge' in link['href'])):
                    print(f"Skipping Cloudflare challenge URL: {link['href']}")
                    continue
                
                # Try direct navigation
                if link['href']:
                    href = link['href']
                    if not href.startswith('http'):
                        # Handle relative URLs
                        base_url = current_page.url.split('?')[0]
                        if href.startswith('/'):
                            origin = '/'.join(base_url.split('/')[:3])
                            href = origin + href
                        else:
                            href = base_url + '/' + href
                    
                    # Check if this is likely a Cloudflare challenge URL before navigating
                    if ('cloudflare.com' in href and 
                        ('challenge' in href or 'utm_source=challenge' in href)):
                        print(f"Skipping Cloudflare challenge URL: {href}")
                        continue
                        
                    print(f"Navigating to potential terms link: {href}")
                    await current_page.goto(href, timeout=3000)
                    
                    # Check if we landed on a Cloudflare challenge page
                    is_challenge = await current_page.evaluate("""() => {
                        const html = document.documentElement.innerHTML.toLowerCase();
                        return html.includes('cloudflare') && 
                               (html.includes('challenge') || 
                                html.includes('security check') || 
                                html.includes('captcha') || 
                                html.includes('verify your browser'));
                    }""")
                    
                    if is_challenge:
                        print("Landed on a Cloudflare challenge page, skipping")
                        # Navigate back for next attempt
                        await current_page.goto(page.url, timeout=3000)
                        continue
                    
                    # Verify if this looks like a terms page
                    is_terms = await current_page.evaluate("""() => {
                        const text = document.body.textContent.toLowerCase();
                        const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map(h => h.textContent.toLowerCase());
                        
                        // Check for terms indicators in the page
                        return {
                            title: document.title,
                            hasTermsInTitle: document.title.toLowerCase().includes('terms'),
                            hasTermsInHeadings: headings.some(h => h.includes('terms')),
                            hasTermsContent: text.includes('terms') && text.includes('use') && text.includes('agree'),
                            textLength: text.length,
                            isCloudflareChallenge: text.includes('cloudflare') && 
                                                   (text.includes('challenge') || text.includes('security check'))
                        };
                    }""")
                    
                    # Log verification details
                    print(f"Page verification: {is_terms}")
                    
                    # Skip if it's a Cloudflare challenge
                    if is_terms.get('isCloudflareChallenge', False):
                        print("Detected Cloudflare challenge page, skipping")
                        # Navigate back for next attempt
                        await current_page.goto(page.url, timeout=3000)
                        continue
                    
                    # If this is likely a terms page, return it
                    if ((is_terms['hasTermsInTitle'] or is_terms['hasTermsInHeadings']) and 
                        is_terms['hasTermsContent'] and is_terms['textLength'] > 2000):
                        print(f"Confirmed terms page: {current_page.url}")
                        return current_page.url, current_page
                    else:
                        print("Not a terms page, continuing search...")
                        # Navigate back for next attempt
                        await current_page.goto(page.url, timeout=3000)
            except Exception as e:
                print(f"Error checking structure link: {e}")
                # Continue to the next link
                try:
                    await current_page.goto(page.url, timeout=3000)
                except:
                    pass
                    
    except Exception as e:
        print(f"Document structure analysis error: {e}")

    print("Exhausted all dynamic methods. No terms link found.")
    return visited_url, current_page

async def check_for_better_terms_link(page, context):
    """
    Check if the terms page has any additional terms links that might be more specific.
    Returns the new URL and page if found, otherwise returns None and the original page.
    """
    print("Checking for more specific terms links on the terms page...")
    
    # Store the starting URL before navigation
    starting_url = page.url
    
    # Use the same JavaScript function to find any additional terms links
    new_url, new_page = await find_all_links_js(page, context)
    
    if new_url and new_url != starting_url:
        print(f"Found better terms link: {new_url}")
        return new_url, new_page
    else:
        print("No better terms link found, keeping current page")
        return None, page

async def bs4_fallback_link_finder(page, context):
    """
    Use robust HTML parsing as a fallback method to find terms links
    """
    print("Using robust HTML parsing to find terms links...")
    
    # Get the page HTML
    html_content = await page.content()
    
    # Check for Cloudflare challenge page
    is_cloudflare_challenge = await page.evaluate("""(html) => {
        // Check for common Cloudflare indicators
        return (html.includes('cloudflare') && 
                (html.includes('challenge') || 
                 html.includes('security check') || 
                 html.includes('captcha') || 
                 html.includes('verify your browser')));
    }""", html_content)
    
    if is_cloudflare_challenge:
        print("Detected Cloudflare challenge page, cannot extract reliable links")
        return None, page
    
    # Use more robust attribute-aware parsing to find links even with custom attributes
    terms_links = await page.evaluate("""(html) => {
        // Custom HTML parser to handle complex link structures with various attributes
        function extractLinks(html) {
            const links = [];
            
            // Regular expression to find all anchor tags with their contents
            // This works even with custom data attributes
            const anchorRegex = /<a\\s+([^>]*)>(.*?)<\\/a>/gi;
            let match;
            
            while ((match = anchorRegex.exec(html)) !== null) {
                const attributes = match[1];
                const linkText = match[2].replace(/<[^>]*>/g, '').trim(); // Remove any HTML inside the link text
                
                // Extract href from attributes
                const hrefMatch = attributes.match(/href=["']([^"']*)["']/i);
                const href = hrefMatch ? hrefMatch[1] : '';
                
                // Store other potentially useful attributes
                const classMatch = attributes.match(/class=["']([^"']*)["']/i);
                const idMatch = attributes.match(/id=["']([^"']*)["']/i);
                const dataAttributes = {};
                
                // Extract any data-* attributes
                const dataAttrMatches = attributes.matchAll(/data-([\\w-]+)=["']([^"']*)["']/gi);
                for (const dataMatch of dataAttrMatches) {
                    dataAttributes[dataMatch[1]] = dataMatch[2];
                }
                
                links.push({
                    text: linkText,
                    href: href,
                    className: classMatch ? classMatch[1] : '',
                    id: idMatch ? idMatch[1] : '',
                    dataAttributes: dataAttributes,
                    rawAttributes: attributes
                });
            }
            
            return links;
        }
        
        // Extract all links from the HTML
        const allLinks = extractLinks(html);
        
        // Filter to terms-related links with a more comprehensive approach
        const termsLinks = allLinks.filter(link => {
            const href = link.href.toLowerCase();
            const text = link.text.toLowerCase();
            
            // Strong text indicators
            const textIndicators = [
                'terms of service', 'terms of use', 'terms and conditions',
                'terms & conditions', 'terms', 'tos', 'legal terms', 'conditions of use',
                'user agreement', 'legal', 'legal notices'
            ];
            
            // Strong href indicators
            const hrefIndicators = [
                '/terms', '/tos', '/terms-of-service', '/terms-of-use',
                '/legal/terms', '/terms-and-conditions', '/conditions',
                '/legal', '/legal-terms', '/eula'
            ];
            
            // Check for exact matches in text (highest priority)
            const hasExactTextMatch = textIndicators.some(indicator => 
                text === indicator || text.replace(/\\s+/g, '') === indicator.replace(/\\s+/g, '')
            );
            
            // Check for terms contained in text
            const hasTermsInText = text.includes('term') || text.includes('condition') || 
                                  text.includes('legal') || text.includes('tos');
            
            // Check for terms in href
            const hasTermsInHref = hrefIndicators.some(indicator => href.includes(indicator)) || 
                                  href.includes('term') || href.includes('condition') || 
                                  href.includes('legal');
            
            // Give priority score
            let score = 0;
            if (hasExactTextMatch) score += 100;
            if (hasTermsInText) score += 50;
            if (hasTermsInHref) score += 75;
            
            // Add the score to the link object
            link.score = score;
            
            // Accept if any condition is met and score is above threshold
            return score > 0;
        });
        
        // Sort by score (highest first)
        termsLinks.sort((a, b) => b.score - a.score);
        
        return termsLinks;
    }""", html_content)
    
    print(f"Found {len(terms_links)} potential terms links with robust HTML parsing")
    
    # Process the found links (highest score first)
    for link in terms_links:
        score_display = link['score'] if 'score' in link else 0
        print(f"Link found: '{link['text']}' → {link['href']} [Score: {score_display}]")
        
        try:
            # Handle relative URLs
            href = link['href']
            if href and not href.startswith('http'):
                # Convert to absolute URL
                if href.startswith('/'):
                    base_url = '/'.join(page.url.split('/')[:3])  # Get http(s)://domain.com
                    href = base_url + href
                else:
                    base_url = page.url.split('?')[0].split('#')[0]  # Remove query/fragment
                    if base_url.endswith('/'):
                        href = base_url + href
                    else:
                        href = base_url + '/' + href
            
            # Skip empty or javascript: links
            if not href or href.startswith('javascript:'):
                continue
                
            # Try direct navigation to this link
            if href:
                print(f"Navigating directly to: {href}")
                try:
                    await page.goto(href, timeout=3000)
                    await page.wait_for_load_state('domcontentloaded')
                    
                    # Check if this looks like a terms page
                    terms_content = await page.evaluate("""() => {
                        const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
                        const paragraphs = Array.from(document.querySelectorAll('p'));
                        
                        // Check headings for terms-related content
                        const termsHeading = headings.some(h => {
                            const text = h.textContent.toLowerCase();
                            return text.includes('terms') || 
                                   text.includes('condition') || 
                                   text.includes('legal agreement') ||
                                   text.includes('service agreement');
                        });
                        
                        // Check page title
                        const title = document.title.toLowerCase();
                        const termsInTitle = title.includes('terms') || 
                                            title.includes('tos') || 
                                            title.includes('conditions');
                        
                        // Check first few paragraphs for legal content indicators
                        const legalContent = paragraphs.slice(0, 5).some(p => {
                            const text = p.textContent.toLowerCase();
                            return text.includes('agree') ||
                                   text.includes('terms') ||
                                   text.includes('conditions') ||
                                   text.includes('legal') ||
                                   text.includes('copyright') ||
                                   text.includes('intellectual property');
                        });
                        
                        return termsHeading || termsInTitle || legalContent;
                    }""")
                    
                    if terms_content:
                        print(f"Found terms content at: {page.url}")
                        return page.url, page
                    else:
                        print("Page doesn't appear to contain terms content")
                except Exception as e:
                    print(f"Navigation error: {e}")
        except Exception as e:
            print(f"Error processing link: {e}")
    
    return None, page

async def standard_terms_finder(url: str, headers: dict = None) -> tuple[str, None]:
    """
    Advanced dynamic approach to find Terms of Service links without hardcoded patterns.
    Uses site structure analysis, content evaluation, and semantic understanding to
    discover terms pages regardless of site architecture.
    
    Args:
        url: The URL to scan
        headers: Optional request headers
        
    Returns:
        Tuple of (terms_url, None) or (None, None) if not found
    """
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse, urljoin
    import re
    
    print("Analyzing site structure...")
    
    # Create a session to maintain cookies across requests
    session = requests.Session()
    
    # Default headers if none provided
    if not headers:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    
    # Parse the target URL
    parsed = urlparse(url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # Extract domain name for analysis
    domain_parts = parsed.netloc.split('.')
    main_domain = domain_parts[-2] if len(domain_parts) >= 2 else domain_parts[0]
    
    # Function to detect Cloudflare challenge page
    def is_cloudflare_challenge(soup, url):
        # Check for common Cloudflare challenge indicators
        cloudflare_indicators = [
            # Text patterns
            lambda s: "cloudflare" in s.get_text().lower() and any(x in s.get_text().lower() for x in ["challenge", "security check", "captcha", "verify"]),
            # URL patterns
            lambda s: "cloudflare.com" in url and "challenge" in url,
            # Meta tags
            lambda s: any("cloudflare" in (m.get("content", "").lower() + m.get("name", "").lower()) for m in s.find_all("meta")),
            # Scripts
            lambda s: any("cloudflare" in script.get("src", "").lower() for script in s.find_all("script", src=True))
        ]
        
        return any(indicator(soup) for indicator in cloudflare_indicators)
    
    # Create common URL variations for discovery
    variations_to_try = []
    
    # Add the original URL
    variations_to_try.append((url, "original url"))
    
    # Add base domain if different
    if base_domain != url:
        variations_to_try.append((base_domain, "base domain"))
    
    print(f"Starting discovery with {len(variations_to_try)} URL variations")
    
    # Collection to store all potential terms links with their scores
    candidates = []
    
    # 1. SITE STRUCTURE ANALYSIS
    # Analyze each URL variation to discover link patterns and site architecture
    for target_url, variation_desc in variations_to_try:
        try:
            print(f"Analyzing site structure at {target_url}...")
            
            # Get the main page content
            response = session.get(target_url, headers=headers, timeout=15)
            if response.status_code != 200:
                print(f"Failed to access {target_url}: Status {response.status_code}")
                continue
                
            # Use the final URL after any redirects
            current_url = response.url
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Check if this is a Cloudflare challenge page
            if is_cloudflare_challenge(soup, current_url):
                print(f"Detected Cloudflare challenge page at {current_url}, skipping this variation")
                continue
                
            # 1.1 SITE METADATA ANALYSIS
            # Extract site metadata for clues about the platform and structure
            generator = None
            for meta in soup.find_all('meta'):
                if meta.get('name', '').lower() == 'generator':
                    generator = meta.get('content', '').lower()
                    print(f"Detected site generator: {generator}")
                    break
            
            # 1.2 NAVIGATION STRUCTURE ANALYSIS
            # Find all navigation elements (headers, footers, menus)
            nav_elements = []
            
            # Look for footers (where terms are commonly found)
            footer_sections = soup.select('footer, .footer, #footer, [class*="footer"], [id*="footer"], .legal, .bottom, [class*="bottom"]')
            nav_elements.extend(footer_sections)
            
            # Look for navigation menus
            nav_sections = soup.select('nav, .nav, #nav, [class*="nav"], .menu, #menu, [class*="menu"]')
            nav_elements.extend(nav_sections)
            
            # Look for legal sections
            legal_sections = soup.select('.legal, #legal, [class*="legal"], .policies, [class*="policy"]')
            nav_elements.extend(legal_sections)
            
            print(f"Found {len(nav_elements)} navigation elements to analyze")
            
            # 1.3 NAVIGATION LINK ANALYSIS
            # Extract and analyze all links from navigation elements
            nav_links = []
            for nav in nav_elements:
                for link in nav.find_all('a', href=True):
                    href = link.get('href')
                    text = link.get_text().strip()
                    
                    # Skip non-web links
                    if not href or href.startswith(('javascript:', 'mailto:', 'tel:')):
                        continue
                    
                    # Create absolute URL
                    abs_url = href if href.startswith(('http://', 'https://')) else urljoin(current_url, href)
                    
                    # Add to navigation links collection
                    nav_links.append({
                        'url': abs_url,
                        'text': text,
                        'element_type': str(nav.name),
                        'classes': ' '.join(nav.get('class', [])),
                    })
            
            # 1.4 LINK TEXT SEMANTIC ANALYSIS
            # Analyze link text to identify terms-related terminology patterns
            common_terms = {}
            for link in nav_links:
                text = link.get('text', '').lower()
                
                # Skip empty or very short text
                if not text or len(text) < 3:
                    continue
                    
                # Count occurrences of potentially legal-related words
                words = text.split()
                for word in words:
                    if len(word) > 2:  # Skip very short words
                        if word not in common_terms:
                            common_terms[word] = 0
                        common_terms[word] += 1
            
            # Find the most common terms
            sorted_terms = sorted(common_terms.items(), key=lambda x: x[1], reverse=True)
            
            # Extract the top terms for analysis
            top_terms = [term[0] for term in sorted_terms[:10]]
            print(f"Top terms in navigation links: {', '.join(top_terms)}")
            
            # 1.5 CANDIDATE SCORING
            # Score each link based on how likely it is to be a terms page
            for link in nav_links:
                url = link.get('url')
                text = link.get('text', '').lower()
                element_type = link.get('element_type', '')
                classes = link.get('classes', '').lower()
                
                # Skip links without URL or text
                if not url or not text:
                    continue
                
                score = 0
                text_signals = []
                url_signals = []
                
                # TEXT ANALYSIS - Score based on link text
                term_indicators = [
                    'terms', 'conditions', 'legal', 'use', 
                    'service', 'tos', 'policy'
                ]
                
                # Check if link text contains terms indicators
                for indicator in term_indicators:
                    if indicator in text:
                        score += 10
                        text_signals.append(indicator)
                
                # Exact match bonus for common terms formatting
                if re.search(r'terms\s+of\s+(use|service)', text, re.IGNORECASE):
                    score += 50
                    text_signals.append('exact_match')
                elif re.search(r'terms(\s+and|\s+&)?\s+conditions', text, re.IGNORECASE):
                    score += 40
                    text_signals.append('exact_match')
                elif text == 'terms' or text == 'tos':
                    score += 35
                    text_signals.append('exact_match')
                
                # Exclude likely non-terms links
                if 'privacy' in text or 'cookie' in text:
                    score -= 40
                    text_signals.append('privacy_penalty')
                    
                # URL ANALYSIS - Score based on URL structure
                parsed_url = urlparse(url)
                path = parsed_url.path.lower()
                
                # Extract path components for pattern matching
                path_parts = [p for p in path.split('/') if p]
                
                # Check for terms-related path components
                for part in path_parts:
                    if 'term' in part:
                        score += 20
                        url_signals.append('term_in_path')
                    if 'condition' in part:
                        score += 15
                        url_signals.append('condition_in_path')
                    if 'legal' in part:
                        score += 10
                        url_signals.append('legal_in_path')
                    if 'use' in part and len(part) < 10:  # Avoid matching longer words
                        score += 5
                        url_signals.append('use_in_path')
                
                # POSITION ANALYSIS - Score based on position in document
                if element_type == 'footer' or 'footer' in classes:
                    score += 15  # Terms links often in footer
                    text_signals.append('footer_location')
                if 'legal' in classes or 'policy' in classes:
                    score += 10  # Terms links often in legal sections
                    text_signals.append('legal_section')
                
                # Add candidate with score and signals
                candidates.append({
                    'url': url,
                    'text': text,
                    'score': score,
                    'text_signals': text_signals,
                    'url_signals': url_signals,
                    'source': f"{variation_desc}_{element_type}"
                })
        
        except Exception as e:
            print(f"Error analyzing {target_url}: {str(e)}")
            continue
    
    # 2. ALL-PAGE ANALYSIS
    # If we don't have good candidates from navigation, look at all links
    if not candidates or max(c['score'] for c in candidates) < 30:
        try:
            print("No strong candidates found in navigation. Analyzing all page links...")
            
            # Get the homepage
            response = session.get(base_domain, headers=headers, timeout=15)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Find all links on the page
                for link in soup.find_all('a', href=True):
                    href = link.get('href')
                    text = link.get_text().strip()
                    
                    # Skip non-web links
                    if not href or href.startswith(('javascript:', 'mailto:', 'tel:')):
                        continue
                    
                    # Create absolute URL
                    abs_url = href if href.startswith(('http://', 'https://')) else urljoin(response.url, href)
                    
                    # Skip if already in candidates
                    if any(c['url'] == abs_url for c in candidates):
                        continue
                    
                    # Basic scoring
                    score = 0
                    text_signals = []
                    url_signals = []
                    
                    # Text analysis
                    text_lower = text.lower()
                    if 'terms' in text_lower:
                        score += 20
                        text_signals.append('terms_in_text')
                    if 'condition' in text_lower:
                        score += 15
                        text_signals.append('condition_in_text')
                    if 'legal' in text_lower:
                        score += 10
                        text_signals.append('legal_in_text')
                    
                    # URL analysis
                    url_lower = abs_url.lower()
                    if 'terms' in url_lower:
                        score += 15
                        url_signals.append('terms_in_url')
                    if 'legal' in url_lower:
                        score += 10
                        url_signals.append('legal_in_url')
                    
                    # Only add if score is above threshold
                    if score >= 20:
                        candidates.append({
                            'url': abs_url,
                            'text': text,
                            'score': score,
                            'text_signals': text_signals,
                            'url_signals': url_signals,
                            'source': 'all_page_links'
                        })
        except Exception as e:
            print(f"Error analyzing all page links: {str(e)}")
    
    # 3. ROBOTS.TXT ANALYSIS
    # Look for paths in robots.txt that might lead to terms pages
    try:
        print("Analyzing robots.txt for potential terms pages...")
        robots_url = f"{base_domain}/robots.txt"
        response = session.get(robots_url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            # Parse robots.txt content
            robots_content = response.text.lower()
            
            # Look for paths that might be terms-related
            potential_paths = []
            
            # Extract paths from Disallow and Allow statements
            for line in robots_content.split('\n'):
                line = line.strip()
                if line.startswith(('disallow:', 'allow:')):
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        path = parts[1].strip()
                        if path and path != '/':
                            # Check if this path looks like it might be terms-related
                            if any(term in path for term in ['term', 'legal', 'condition', 'tos']):
                                potential_paths.append(path)
            
            # Check each potential path
            for path in potential_paths:
                test_url = urljoin(base_domain, path)
                
                # Skip if already in candidates
                if any(c['url'] == test_url for c in candidates):
                    continue
                
                # Basic scoring
                score = 25  # Base score for being in robots.txt
                url_signals = ['found_in_robots']
                
                # URL analysis
                path_lower = path.lower()
                if 'term' in path_lower:
                    score += 15
                    url_signals.append('term_in_path')
                if 'legal' in path_lower:
                    score += 10
                    url_signals.append('legal_in_path')
                
                # Add to candidates
                candidates.append({
                    'url': test_url,
                    'text': f"Path from robots.txt: {path}",
                    'score': score,
                    'text_signals': [],
                    'url_signals': url_signals,
                    'source': 'robots_txt'
                })
    except Exception as e:
        print(f"Error analyzing robots.txt: {str(e)}")
    
    # 4. SITEMAP ANALYSIS
    # Look for terms pages in sitemap
    try:
        print("Analyzing sitemap for potential terms pages...")
        # Common sitemap locations
        sitemap_locations = [
            f"{base_domain}/sitemap.xml",
            f"{base_domain}/sitemap_index.xml",
            f"{base_domain}/sitemap.php",
            f"{base_domain}/sitemaps/sitemap.xml"
        ]
        
        sitemap_urls = []
        
        # Try to find and parse sitemap
        for sitemap_url in sitemap_locations:
            try:
                response = session.get(sitemap_url, headers=headers, timeout=10)
                if response.status_code == 200 and '<url>' in response.text.lower():
                    # Extract URLs using regex for simplicity
                    urls = re.findall(r'<loc>(.*?)</loc>', response.text, re.IGNORECASE)
                    sitemap_urls.extend(urls)
                    print(f"Found {len(urls)} URLs in sitemap at {sitemap_url}")
            except:
                continue
        
        # Filter for potential terms URLs
        for url in sitemap_urls:
            # Skip if already in candidates
            if any(c['url'] == url for c in candidates):
                continue
                
            # Check if URL contains terms-related patterns
            url_lower = url.lower()
            if 'term' in url_lower or 'legal' in url_lower or 'condition' in url_lower:
                # Basic scoring
                score = 20  # Base score for being in sitemap
                signals = []
                
                if 'term' in url_lower:
                    score += 15
                    signals.append('term_in_url')
                if 'legal' in url_lower:
                    score += 10
                    signals.append('legal_in_url')
                
                # Add to candidates
                candidates.append({
                    'url': url,
                    'text': f"URL from sitemap",
                    'score': score,
                    'text_signals': [],
                    'url_signals': signals,
                    'source': 'sitemap'
                })
    except Exception as e:
        print(f"Error analyzing sitemap: {str(e)}")
    
    # 5. CONTENT VERIFICATION
    # Sort candidates by score and verify content
    print(f"Found {len(candidates)} potential terms page candidates")
    
    # Sort by score (highest first)
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    # Display top candidates
    print("Top candidates:")
    for i, candidate in enumerate(candidates[:5]):
        print(f"  {i+1}. {candidate['text']} → {candidate['url']} (Score: {candidate['score']})")
        print(f"     Signals: {', '.join(candidate['text_signals'] + candidate['url_signals'])}")
    
    # Verify top candidates
    for candidate in candidates[:10]:  # Check top 10 candidates
        try:
            print(f"Verifying: {candidate['url']}")
            
            # Get page content
            response = session.get(candidate['url'], headers=headers, timeout=15)
            if response.status_code != 200:
                print(f"  Failed: Status code {response.status_code}")
                continue
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract text content
            content = soup.get_text().lower()
            title = soup.title.string.lower() if soup.title else ""
            
            # Look for headings
            headings = []
            for h in soup.find_all(['h1', 'h2', 'h3']):
                heading_text = h.get_text().strip().lower()
                if heading_text:
                    headings.append(heading_text)
            
            # Verify this is a terms page
            content_score = 0
            verification_signals = []
            
            # Title verification
            if 'terms' in title:
                content_score += 20
                verification_signals.append('terms_in_title')
            if 'condition' in title:
                content_score += 15
                verification_signals.append('conditions_in_title')
            if 'use' in title and 'terms' in title:
                content_score += 25
                verification_signals.append('terms_of_use_in_title')
            if 'service' in title and 'terms' in title:
                content_score += 25
                verification_signals.append('terms_of_service_in_title')
            
            # Heading verification
            for heading in headings[:3]:  # Check first few headings
                if 'terms' in heading:
                    content_score += 20
                    verification_signals.append('terms_in_heading')
                    break
            
            # Content verification
            # Check for legal terminology typical in terms pages
            legal_terms = [
                'agree', 'agreement', 'accept', 'terms', 'conditions', 
                'provision', 'clause', 'license', 'copyright', 'intellectual property',
                'liability', 'limitation', 'warranty', 'disclaimer', 'jurisdiction'
            ]
            
            found_legal_terms = []
            for term in legal_terms:
                if term in content:
                    found_legal_terms.append(term)
            
            # If we found multiple legal terms, this is likely a terms page
            if len(found_legal_terms) >= 5:
                content_score += len(found_legal_terms) * 2
                verification_signals.append(f"found_{len(found_legal_terms)}_legal_terms")
            
            # Check content length - terms pages are usually lengthy
            if len(content) > 3000:
                content_score += 15
                verification_signals.append('long_content')
            
            # Common phrases in terms pages
            terms_phrases = [
                'terms of use', 'terms of service', 'terms and conditions',
                'by using', 'you agree to', 'by accessing', 'please read',
                'your use of', 'these terms', 'without limitation'
            ]
            
            found_phrases = []
            for phrase in terms_phrases:
                if phrase in content:
                    found_phrases.append(phrase)
            
            if found_phrases:
                content_score += len(found_phrases) * 3
                verification_signals.append(f"found_{len(found_phrases)}_terms_phrases")
            
            # Combine original score with content verification score
            total_score = candidate['score'] + content_score
            
            print(f"  Verification score: {content_score} (Total: {total_score})")
            print(f"  Verification signals: {', '.join(verification_signals)}")
            
            # If this is very likely a terms page, return it
            if total_score >= 100 or (content_score >= 50 and candidate['score'] >= 30):
                print(f"Found terms page: {response.url}")
                return response.url, None
            
            # If it's a promising candidate but not entirely sure, keep it as a backup
            if total_score >= 70:
                print(f"Found promising terms page candidate: {response.url}")
                
                # If this is the highest scoring candidate so far, return it
                if candidate == candidates[0]:
                    print(f"Using highest scoring candidate as terms page: {response.url}")
                    return response.url, None
        
        except Exception as e:
            print(f"  Error verifying {candidate['url']}: {str(e)}")
    
    # If we found any reasonable candidates, return the best one
    if candidates and candidates[0]['score'] >= 40:
        best_url = candidates[0]['url']
        print(f"Using best available candidate as terms page: {best_url}")
        return best_url, None
    
    # No good candidates found
    print("No suitable terms page found with dynamic discovery")
    return None, None

# Add a new function to verify if the current link is the final destination
async def verify_final_link(page, context):
    """
    Check if the current page contains links to more specific terms pages.
    This handles cases where the initial terms page is a hub/index that links to the actual terms.
    """
    print("Verifying if this is the final Terms of Service destination...")
    
    # Look for links with strong indicators that they lead to more specific terms
    final_links = await page.evaluate("""() => {
        // Find all links on the current page
        const links = Array.from(document.querySelectorAll('a[href]'));
        
        // Strong indicators that a link points to more specific terms
        const strongIndicators = [
            { text: 'terms of service', score: 100 },
            { text: 'terms of use', score: 95 },
            { text: 'user agreement', score: 90 },
            { text: 'terms and conditions', score: 85 },
            { text: 'service agreement', score: 80 }
        ];
        
        // Score each link
        const scoredLinks = links.map(link => {
            const text = link.textContent.toLowerCase().trim();
            const href = link.getAttribute('href');
            
            // Skip empty links or current page links
            if (!href || href === '#' || href === window.location.href) {
                return { score: -1 };
            }
            
            let score = 0;
            let matchReason = [];
            
            // Check for exact match with strong indicators
            for (const indicator of strongIndicators) {
                if (text === indicator.text) {
                    score += indicator.score;
                    matchReason.push(`exact_match: ${indicator.text}`);
                }
            }
            
            // Check for partial match with strong indicators
            if (score === 0) {
                for (const indicator of strongIndicators) {
                    if (text.includes(indicator.text)) {
                        score += indicator.score * 0.7;  // 70% of the full score
                        matchReason.push(`contains: ${indicator.text}`);
                    }
                }
            }
            
            // Check if URL contains strong indicators
            if (href.includes('terms-of-service') || href.includes('terms_of_service') || href.includes('legal/terms')) {
                score += 40;
                matchReason.push('terms_in_url');
            }
            
            return {
                text: text,
                href: href,
                score: score,
                matchReason: matchReason
            };
        }).filter(item => item.score > 30);  // Only keep significant matches
        
        // Sort by score
        scoredLinks.sort((a, b) => b.score - a.score);
        
        return scoredLinks.slice(0, 3);  // Return top 3 candidates
    }""")
    
    if not final_links:
        print("Current page appears to be the final destination")
        return None
    
    print(f"Found {len(final_links)} potential deeper terms links")
    
    # Check the top scoring links
    for link in final_links:
        if link['score'] > 50:
            print(f"Found promising deeper link: '{link['text']}' → {link['href']} (Score: {link['score']})")
            
            try:
                # Resolve URL if relative
                href = link['href']
                if not href.startswith('http'):
                    # Convert to absolute URL
                    if href.startswith('/'):
                        base_url = '/'.join(page.url.split('/')[:3])  # Get http(s)://domain.com
                        href = base_url + href
                    else:
                        base_url = page.url.split('?')[0].split('#')[0]  # Remove query/fragment
                        if base_url.endswith('/'):
                            href = base_url + href
                        else:
                            href = base_url + '/' + href
                
                # Skip if it's the same as current URL
                if href == page.url:
                    print("Link points to current page, skipping")
                    continue
                
                # Navigate to check if it's a better terms page
                print(f"Checking potential final terms link: {href}")
                try:
                    await page.goto(href, timeout=5000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(2000)  # Give it time to load
                    
                    # Check if this looks like a terms page
                    is_terms = await page.evaluate("""() => {
                        const text = document.body.textContent.toLowerCase();
                        const headings = Array.from(document.querySelectorAll('h1, h2, h3')).map(h => h.textContent.toLowerCase());
                        
                        return {
                            title: document.title,
                            hasTermsInTitle: document.title.toLowerCase().includes('terms'),
                            hasTermsInHeadings: headings.some(h => h.includes('terms')),
                            hasTermsContent: text.includes('terms') && text.includes('use') && text.includes('agree'),
                            textLength: text.length
                        };
                    }""")
                    
                    # If this is likely a terms page, return it
                    if ((is_terms['hasTermsInTitle'] or is_terms['hasTermsInHeadings']) and 
                        is_terms['hasTermsContent'] and is_terms['textLength'] > 2000):
                        print(f"Confirmed terms page: {page.url}")
                        return page.url
                    
                except Exception as nav_error:
                    print(f"Navigation error: {nav_error}")
                    continue
                    
            except Exception as e:
                print(f"Error checking link: {e}")
                continue
    
    return None

async def main():
    """Main execution function"""
    try:
        # Initialize playwright
        async with async_playwright() as p:
            # Launch browser with longer timeout and different user agent
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-web-security', '--no-sandbox']
            )
            
            # Create context with custom settings
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            
            # Create page
            page = await context.new_page()
            
            # Get URL from command line arguments or use a default
            import sys
            url = sys.argv[1] if len(sys.argv) > 1 else "https://facebook.com"
            
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
            
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(3000)  # Longer initial wait
            
            result = None
            method_used = ""
            final_page = None
            
            # Try each method in sequence
            methods = [
                ('javascript', find_all_links_js),
                ('scrolling', smooth_scroll_and_click),
                ('html_parsing', bs4_fallback_link_finder)
            ]
            
            for method_name, method_func in methods:
                if not result:
                    print(f"Trying {method_name} method...")
                    visited_url, current_page = await method_func(page, context)
                    if visited_url:
                        result = visited_url
                        method_used = method_name
                        final_page = current_page
                        break
            
            # Standard finder as last resort
            if not result:
                print("Trying standard HTML scraping method...")
                terms_url, _ = await standard_terms_finder(url, headers=None)
                if terms_url:
                    result = terms_url
                    method_used = "standard_finder"
            
            # Final verification and deep link checking
            if result and final_page:
                print(f"Found initial terms link: {result}")
                
                # Check for better/more specific terms links
                if method_used != "standard_finder":
                    final_url = await verify_final_link(final_page, context)
                    if final_url:
                        print(f"Found final terms page: {final_url}")
                        result = final_url
                        method_used += "_with_final_link"
            
            # Close browser
            await browser.close()
            
            return result, method_used
            
    except Exception as e:
        print(f"Error: {e}")
        return None, "error"

# Run the script
if __name__ == "__main__":
    import asyncio
    result, method = asyncio.run(main())
    if result:
        print(f"\nFinal Terms URL: {result}")
        print(f"Method used: {method}")
    else:
        print("\nNo terms page found")