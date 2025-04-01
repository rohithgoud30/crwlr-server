from fastapi import APIRouter, Response, HTTPException
from pydantic import BaseModel, field_validator
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings
import re
from typing import Optional, Any, List, Tuple
import asyncio
from playwright.async_api import async_playwright
import logging
from .utils import normalize_url, prepare_url_variations
from playwright.sync_api import sync_playwright

# Filter out the XML parsed as HTML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()


class TosRequest(BaseModel):
    url: str  # Changed from HttpUrl to str to allow any input

    @field_validator('url')
    @classmethod
    def validate_and_transform_url(cls, v: str) -> str:
        """
        Basic URL validation and normalization using utils.
        """
        return normalize_url(v)


class TosResponse(BaseModel):
    url: str
    tos_url: str | None = None
    success: bool
    message: str
    method_used: str = "standard"  # Indicates which method was used to find the ToS


@router.post("/tos", response_model=TosResponse, responses={
    200: {"description": "Terms of Service found successfully"},
    404: {"description": "Terms of Service not found", "model": TosResponse}
})
async def find_tos(request: TosRequest, response: Response) -> TosResponse:
    """
    Takes a base URL and returns the Terms of Service page URL.
    This endpoint accepts partial URLs like 'google.com' and will
    automatically add the 'https://' protocol prefix if needed.
    
    Features a fallback to Playwright for JavaScript-heavy sites.
    """
    original_url = request.url  # The exact URL provided by the user, already normalized
    
    logger.info(f"Processing ToS request for URL: {original_url}")
    
    # Check if this is an App Store app URL
    is_app_store_app = False
    app_id = None
    
    # Parse the URL to check if it's an App Store app URL
    parsed_url = urlparse(original_url)
    if ('apps.apple.com' in parsed_url.netloc or 'itunes.apple.com' in parsed_url.netloc):
        # Check if the path contains an app ID
        app_id_match = re.search(r'/id(\d+)', parsed_url.path)
        if app_id_match:
            is_app_store_app = True
            app_id = app_id_match.group(1)
            logger.info(f"Detected App Store app URL with ID: {app_id}")
    
    # Check if this is a Google Play Store app URL
    is_play_store_app = False
    play_app_id = None
    
    if 'play.google.com/store/apps' in original_url:
        # Extract the app ID from the URL
        play_app_id_match = re.search(r'[?&]id=([^&]+)', original_url)
        if play_app_id_match:
            is_play_store_app = True
            play_app_id = play_app_id_match.group(1)
            logger.info(f"Detected Google Play Store app URL with ID: {play_app_id}")
    
    # If this is an App Store app URL, handle it specially
    if is_app_store_app and app_id:
        result = await handle_app_store_tos(original_url, app_id)
        # Set status code to 404 if ToS not found
        if not result.success:
            response.status_code = 404
        return result
    
    # If this is a Google Play Store app URL, handle it specially
    if is_play_store_app and play_app_id:
        result = await handle_play_store_tos(original_url, play_app_id)
        # Set status code to 404 if ToS not found
        if not result.success:
            response.status_code = 404
        return result
    
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
        'Cache-Control': 'max-age=0',
        'DNT': '1',
    }

    # Create a session to maintain cookies across requests
    session = requests.Session()
    
    # Get URL variations to try from our utility function
    variations_with_types = []
    
    # First prioritize the exact URL provided by the user
    variations_with_types.append((original_url, "original exact url"))
    
    # Then get base domain variations for fallback
    variations = prepare_url_variations(original_url)
    for idx, var_url in enumerate(variations[1:], 1):  # Skip the first one as it's the original
        variations_with_types.append((var_url, f"variation_{idx}"))
    
    logger.info(f"URL variations to try: {variations_with_types}")
    
    # Try the standard method first (requests + BeautifulSoup)
    standard_result = await standard_tos_finder(variations_with_types, headers, session)
    if standard_result.success:
        logger.info(f"Found ToS link with standard method: {standard_result.tos_url}")
        return standard_result
    
    # If standard method fails, try with Playwright
    logger.info(f"Standard method failed for {original_url}, trying with Playwright")
    
    # First try the specific URL with Playwright
    playwright_result = await playwright_tos_finder(original_url)
    
    # Get the base domain
    parsed = urlparse(original_url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # If that fails and the original URL is different from the base domain, 
    # also try the base domain with Playwright
    if not playwright_result.success and original_url != base_domain:
        logger.info(f"Playwright failed on exact URL, trying base domain: {base_domain}")
        base_playwright_result = await playwright_tos_finder(base_domain)
        if base_playwright_result.success:
            logger.info(f"Found ToS link with Playwright on base domain: {base_playwright_result.tos_url}")
            return base_playwright_result
    
    # Return the Playwright result if it found something, otherwise return the standard result
    if playwright_result.success:
        logger.info(f"Found ToS link with Playwright: {playwright_result.tos_url}")
        return playwright_result
    
    # If both methods failed, include a message about what was tried
    logger.info(f"No ToS link found for {original_url} with any method")
    
    # Set status code to 404 since no ToS was found
    response.status_code = 404
    
    # Check if we have any specific error information from Playwright
    if hasattr(playwright_result, 'method_used') and 'timeout' in playwright_result.method_used:
        # Special handling for timeouts
        return TosResponse(
            url=original_url,
            success=False,
            message=f"No Terms of Service link found. The site may be slow or blocking automated access.",
            method_used="both_failed_timeout"
        )
    elif hasattr(playwright_result, 'method_used') and 'navigation_failed' in playwright_result.method_used:
        # Special handling for navigation failures
        return TosResponse(
            url=original_url,
            success=False,
            message=f"No Terms of Service link found. The site may be unavailable or blocking automated access.",
            method_used="both_failed_navigation"
        )
    else:
        # Generic failure message
        return TosResponse(
            url=original_url,
            success=False,
            message=f"No Terms of Service link found. Tried both standard scraping and JavaScript-enabled browser rendering on both the exact URL and base domain.",
            method_used="both_failed"
        )


async def standard_tos_finder(variations_to_try, headers, session) -> TosResponse:
    """Standard approach using requests and BeautifulSoup to find Terms of Service links."""
    
    # For each variation, try to follow redirects to the final destination
    for url, variation_type in variations_to_try:
        try:
            logger.info(f"Trying URL variation: {url} ({variation_type})")
            
            # Check for known sites that might need longer timeouts
            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()
            is_slow_site = any(slow_domain in domain for slow_domain in ['theverge.com', 'vox.com', 'washingtonpost.com'])
            
            # First do a HEAD request to check for redirects
            # Use longer timeout for known slow sites
            timeout = 30 if is_slow_site else 10
            logger.info(f"Using timeout of {timeout}s for HEAD request to {url}")
            
            head_response = session.head(url, headers=headers, timeout=timeout, allow_redirects=True)
            head_response.raise_for_status()
            
            # Get the final URL after redirects
            final_url = head_response.url
            if final_url != url:
                logger.info(f"Followed redirect: {url} -> {final_url}")
            
            # Now get the content of the final URL
            logger.info(f"Fetching content from {final_url}")
            
            # Longer timeout for content fetch on slow sites
            content_timeout = 30 if is_slow_site else 15
            logger.info(f"Using timeout of {content_timeout}s for GET request to {final_url}")
            
            response = session.get(final_url, headers=headers, timeout=content_timeout)
            response.raise_for_status()
            
            # Parse the HTML content
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find the Terms of Service link from the page
            logger.info(f"Searching for ToS link in {final_url}")
            tos_link = find_tos_link(final_url, soup)
            
            # For The Verge or other Vox Media sites, make an extra effort to find the link before falling back
            if 'theverge.com' in final_url and not tos_link:
                logger.info("Extra processing for The Verge to find ToS")
                # Look specifically for footer elements and search for Vox Media links
                footers = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower()))
                for footer in footers:
                    for link in footer.find_all('a', href=True):
                        href = link.get('href')
                        if "voxmedia.com" in href and any(term in href.lower() for term in ['/legal', '/terms', '/tos']):
                            tos_link = href
                            logger.info(f"Found Vox Media ToS link in footer: {tos_link}")
                            break
                    if tos_link:
                        break
            
            if tos_link:
                logger.info(f"Found ToS link: {tos_link} in {final_url} ({variation_type})")
                return TosResponse(
                    url=final_url,  # Return the actual URL after redirects
                    tos_url=tos_link,
                    success=True,
                    message=f"Terms of Service link found on final destination page: {final_url}" + 
                            (f" (Found at {variation_type})" if variation_type != "original exact url" else ""),
                    method_used="standard"
                )
            else:
                logger.info(f"No ToS link found in {final_url} ({variation_type})")
                    
        except requests.RequestException as e:
            logger.error(f"RequestException for {url} ({variation_type}): {str(e)}")
            # If this variation fails, try the next one
            continue
        except Exception as e:
            logger.error(f"Exception for {url} ({variation_type}): {str(e)}")
            # For non-request exceptions, try the next one
            continue
    
    # If we get here, we tried all variations but didn't find a Terms of Service link
    try:
        # Try one more time with the original URL for a better error message
        base_url = variations_to_try[0][0]  # Original URL
        
        head_response = session.head(base_url, headers=headers, timeout=15, allow_redirects=True)
        final_url = head_response.url
        
        response = session.get(final_url, headers=headers, timeout=20)
        response.raise_for_status()
        
        return TosResponse(
            url=final_url,  # Return the final URL after redirects
            success=False,
            message=f"No Terms of Service link found with standard method on the final destination page: {final_url}.",
            method_used="standard_failed"
        )
    except requests.RequestException as e:
        error_msg = ""
        
        # Handle request errors with more specific messages
        if hasattr(e, 'response') and e.response is not None:
            status_code = e.response.status_code
            if status_code == 404:
                error_msg = f"The URL {base_url} returned a 404 Not Found error."
            elif status_code == 403:
                error_msg = f"Access to {base_url} was denied (HTTP 403 Forbidden). The site is likely blocking web scraping."
            elif status_code == 401:
                error_msg = f"Access to {base_url} requires authentication (HTTP 401 Unauthorized)."
            elif status_code == 400:
                error_msg = f"The server at {base_url} returned HTTP 400 Bad Request. This often happens when a site blocks scraping attempts or requires cookies/JavaScript."
            elif status_code == 429:
                error_msg = f"Too many requests to {base_url} (HTTP 429). The site is rate-limiting requests."
            elif status_code >= 500:
                error_msg = f"The server at {base_url} encountered an error (HTTP {status_code})."
            else:
                error_msg = f"Error fetching {base_url}: HTTP status code {status_code}."
        else:
            error_msg = f"Error connecting to {base_url}: {str(e)}"
        
        # Return the error in the response
        return TosResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed"
        )
    except Exception as e:
        error_msg = f"Error processing URL {base_url} with standard method: {str(e)}"
        
        # Return the error in the response
        return TosResponse(
            url=base_url,
            success=False,
            message=error_msg,
            method_used="standard_failed"
        )

def find_tos_link(url: str, soup: BeautifulSoup) -> str | None:
    """Find Terms of Service link in the page."""
    logger.info(f"Searching for ToS link in {url}")
    
    # Get the base domain for comparison
    base_domain = urlparse(url).netloc.lower().replace('www.', '')
    logger.info(f"Base domain: {base_domain}")
    
    # Common terms that might indicate a Terms of Service link
    tos_terms = [
        'terms', 'terms of service', 'terms of use', 'terms & conditions',
        'terms and conditions', 'legal', 'tos', 'conditions of use',
        'user agreement', 'service agreement'
    ]
    
    def is_valid_tos_url(href: str) -> bool:
        """Check if the URL is a valid ToS link."""
        if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            return False
            
        # Make the URL absolute if it's relative
        absolute_url = urljoin(url, href)
        found_domain = urlparse(absolute_url).netloc.lower().replace('www.', '')
        
        # Only accept URLs from the same domain
        return found_domain == base_domain and not is_likely_article_link(href)
    
    def check_link(link) -> str | None:
        """Check if a link element is a valid ToS link."""
        href = link.get('href', '').strip()
        if not is_valid_tos_url(href):
            return None
            
        text = ' '.join(link.stripped_strings).lower()
        href_lower = href.lower()
        
        # Check if the link text or href contains terms-related keywords
        if any(term in text for term in tos_terms) or any(term in href_lower for term in tos_terms):
            absolute_url = urljoin(url, href)
            logger.info(f"Found valid ToS link: {absolute_url}")
            return absolute_url
        return None
    
    # First check all links
    for link in soup.find_all('a', href=True):
        tos_url = check_link(link)
        if tos_url:
            return tos_url
    
    # Then check footer links specifically
    footers = soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower()))
    for footer in footers:
        for link in footer.find_all('a', href=True):
            tos_url = check_link(link)
            if tos_url:
                return tos_url
    
    logger.warning(f"No valid ToS link found for {url}")
    return None

def is_valid_external_domain(found_domain: str, base_domain: str) -> bool:
    """
    Check if the found domain is a valid external domain for hosting terms of service.
    """
    # List of known valid external domains for specific sites
    domain_mappings = {
        'google.com': ['policies.google.com'],
        'youtube.com': ['policies.google.com'],
        'android.com': ['policies.google.com'],
        'facebook.com': ['m.facebook.com', 'web.facebook.com'],
        'instagram.com': ['help.instagram.com'],
        'microsoft.com': ['privacy.microsoft.com', 'go.microsoft.com'],
        'apple.com': ['www.apple.com'],
        'github.com': ['docs.github.com', 'help.github.com'],
        'twitter.com': ['twitter.com', 'help.twitter.com', 'legal.twitter.com'],
        'linkedin.com': ['www.linkedin.com', 'legal.linkedin.com'],
        'amazon.com': ['www.amazon.com', 'aws.amazon.com'],
        'netflix.com': ['help.netflix.com'],
        'spotify.com': ['www.spotify.com'],
        'reddit.com': ['www.redditinc.com', 'reddit.com'],
        'twitch.tv': ['www.twitch.tv', 'legal.twitch.tv'],
        'discord.com': ['discord.com', 'support.discord.com'],
        'slack.com': ['slack.com', 'api.slack.com'],
        'zoom.us': ['zoom.us', 'explore.zoom.us'],
        'dropbox.com': ['www.dropbox.com', 'help.dropbox.com'],
        'adobe.com': ['www.adobe.com', 'helpx.adobe.com'],
        'salesforce.com': ['www.salesforce.com', 'legal.salesforce.com'],
        'atlassian.com': ['www.atlassian.com', 'confluence.atlassian.com'],
        'notion.so': ['www.notion.so'],
        'figma.com': ['help.figma.com', 'www.figma.com'],
        'canva.com': ['www.canva.com'],
        'medium.com': ['policy.medium.com', 'help.medium.com'],
        'wordpress.com': ['wordpress.com', 'automattic.com'],
        'wix.com': ['www.wix.com', 'support.wix.com'],
        'squarespace.com': ['www.squarespace.com'],
        'shopify.com': ['www.shopify.com', 'help.shopify.com'],
        'stripe.com': ['stripe.com', 'support.stripe.com'],
        'paypal.com': ['www.paypal.com'],
        'cloudflare.com': ['www.cloudflare.com', 'developers.cloudflare.com'],
        'digitalocean.com': ['www.digitalocean.com', 'docs.digitalocean.com'],
        'heroku.com': ['www.heroku.com', 'devcenter.heroku.com'],
        'mongodb.com': ['www.mongodb.com'],
        'mysql.com': ['www.mysql.com'],
        'postgresql.org': ['www.postgresql.org'],
        'redis.io': ['redis.io'],
        'elastic.co': ['www.elastic.co'],
        'docker.com': ['www.docker.com', 'docs.docker.com'],
        'kubernetes.io': ['kubernetes.io'],
        'nginx.com': ['www.nginx.com'],
        'apache.org': ['www.apache.org'],
        'jenkins.io': ['www.jenkins.io'],
        'gitlab.com': ['about.gitlab.com', 'docs.gitlab.com'],
        'bitbucket.org': ['bitbucket.org', 'confluence.atlassian.com'],
        'jira.com': ['www.atlassian.com'],
        'trello.com': ['trello.com', 'help.trello.com'],
        'asana.com': ['asana.com', 'help.asana.com'],
        'monday.com': ['monday.com', 'support.monday.com'],
        'clickup.com': ['clickup.com', 'docs.clickup.com'],
        'notion.so': ['www.notion.so', 'help.notion.so'],
        'airtable.com': ['www.airtable.com', 'support.airtable.com'],
        'hubspot.com': ['www.hubspot.com', 'legal.hubspot.com'],
        'zendesk.com': ['www.zendesk.com', 'support.zendesk.com'],
        'intercom.com': ['www.intercom.com'],
        'freshworks.com': ['www.freshworks.com'],
        'segment.com': ['segment.com', 'www.segment.com'],
        'mixpanel.com': ['mixpanel.com', 'help.mixpanel.com'],
        'amplitude.com': ['amplitude.com', 'help.amplitude.com'],
        'optimizely.com': ['www.optimizely.com'],
        'mailchimp.com': ['mailchimp.com', 'legal.mailchimp.com'],
        'sendgrid.com': ['sendgrid.com', 'docs.sendgrid.com'],
        'twilio.com': ['www.twilio.com', 'support.twilio.com'],
        'auth0.com': ['auth0.com', 'docs.auth0.com'],
        'okta.com': ['www.okta.com', 'help.okta.com'],
        'onelogin.com': ['www.onelogin.com'],
        'pingidentity.com': ['www.pingidentity.com'],
        'duo.com': ['duo.com', 'guide.duo.com'],
        'lastpass.com': ['www.lastpass.com', 'support.lastpass.com'],
        '1password.com': ['1password.com', 'support.1password.com'],
        'bitwarden.com': ['bitwarden.com', 'help.bitwarden.com'],
        'dashlane.com': ['www.dashlane.com', 'support.dashlane.com'],
        'keeper.io': ['keeper.io', 'docs.keeper.io'],
        'nordvpn.com': ['nordvpn.com', 'support.nordvpn.com'],
        'expressvpn.com': ['www.expressvpn.com', 'support.expressvpn.com'],
        'protonvpn.com': ['protonvpn.com', 'proton.me'],
        'surfshark.com': ['surfshark.com', 'support.surfshark.com'],
        'openvpn.net': ['openvpn.net', 'docs.openvpn.net'],
        'wireguard.com': ['www.wireguard.com'],
        'cloudflare.com': ['www.cloudflare.com', '1.1.1.1'],
        'cisco.com': ['www.cisco.com', 'tools.cisco.com'],
        'juniper.net': ['www.juniper.net', 'support.juniper.net'],
        'paloaltonetworks.com': ['www.paloaltonetworks.com'],
        'fortinet.com': ['www.fortinet.com', 'docs.fortinet.com'],
        'checkpoint.com': ['www.checkpoint.com', 'supportcenter.checkpoint.com'],
        'symantec.com': ['www.symantec.com', 'support.symantec.com'],
        'mcafee.com': ['www.mcafee.com', 'service.mcafee.com'],
        'norton.com': ['norton.com', 'support.norton.com'],
        'kaspersky.com': ['www.kaspersky.com', 'support.kaspersky.com'],
        'bitdefender.com': ['www.bitdefender.com', 'bitdefender.com'],
        'avast.com': ['www.avast.com', 'support.avast.com'],
        'avg.com': ['www.avg.com', 'support.avg.com'],
        'malwarebytes.com': ['www.malwarebytes.com', 'support.malwarebytes.com'],
        'trendmicro.com': ['www.trendmicro.com', 'success.trendmicro.com'],
        'sophos.com': ['www.sophos.com', 'support.sophos.com'],
        'eset.com': ['www.eset.com', 'support.eset.com'],
        'f-secure.com': ['www.f-secure.com', 'help.f-secure.com'],
        'avira.com': ['www.avira.com', 'support.avira.com'],
        'webroot.com': ['www.webroot.com', 'support.webroot.com'],
        'carbonblack.com': ['www.carbonblack.com', 'community.carbonblack.com'],
        'crowdstrike.com': ['www.crowdstrike.com', 'supportportal.crowdstrike.com'],
        'fireeye.com': ['www.fireeye.com', 'docs.fireeye.com'],
        'rapid7.com': ['www.rapid7.com', 'docs.rapid7.com'],
        'tenable.com': ['www.tenable.com', 'docs.tenable.com'],
        'qualys.com': ['www.qualys.com', 'qualysguard.qualys.com'],
        'nessus.org': ['www.tenable.com', 'docs.tenable.com'],
        'acunetix.com': ['www.acunetix.com', 'www.invicti.com'],
        'portswigger.net': ['portswigger.net', 'forum.portswigger.net'],
        'owasp.org': ['owasp.org', 'wiki.owasp.org'],
        'metasploit.com': ['www.metasploit.com', 'docs.rapid7.com'],
        'kali.org': ['www.kali.org', 'docs.kali.org'],
        'parrotsec.org': ['parrotsec.org', 'docs.parrotsec.org'],
        'blackarch.org': ['blackarch.org', 'wiki.blackarch.org'],
        'pentoo.ch': ['www.pentoo.ch'],
        'backbox.org': ['www.backbox.org'],
        'offensive-security.com': ['www.offensive-security.com', 'help.offensive-security.com'],
        'hackthebox.eu': ['www.hackthebox.com', 'help.hackthebox.com'],
        'vulnhub.com': ['www.vulnhub.com'],
        'tryhackme.com': ['tryhackme.com', 'docs.tryhackme.com'],
        'pentesterlab.com': ['pentesterlab.com'],
        'pentestit.com': ['lab.pentestit.ru'],
        'vulnmachines.com': ['www.vulnmachines.com'],
        'rootme.org': ['www.root-me.org'],
        'ctftime.org': ['ctftime.org'],
        'picoctf.org': ['picoctf.org'],
        'overthewire.org': ['overthewire.org'],
        'underthewire.tech': ['underthewire.tech'],
        'microcorruption.com': ['microcorruption.com'],
        'cryptopals.com': ['cryptopals.com'],
        'cryptohack.org': ['cryptohack.org'],
        'pwnable.kr': ['pwnable.kr'],
        'pwnable.tw': ['pwnable.tw'],
        'reversing.kr': ['reversing.kr'],
        'crackmes.one': ['crackmes.one'],
        'exploit.education': ['exploit.education'],
        'ropemporium.com': ['ropemporium.com'],
        'io.netgarage.org': ['io.netgarage.org'],
        'smashthestack.org': ['smashthestack.org'],
        'ringzer0ctf.com': ['ringzer0ctf.com'],
        'hackthissite.org': ['www.hackthissite.org'],
        'enigmagroup.org': ['www.enigmagroup.org'],
        'hellboundhackers.org': ['www.hellboundhackers.org'],
        'hackxor.net': ['hackxor.net'],
        'hackquest.com': ['www.hackquest.com'],
        'hackerone.com': ['www.hackerone.com', 'docs.hackerone.com'],
        'bugcrowd.com': ['www.bugcrowd.com', 'docs.bugcrowd.com'],
        'intigriti.com': ['www.intigriti.com', 'api.intigriti.com'],
        'yeswehack.com': ['www.yeswehack.com'],
        'synack.com': ['www.synack.com'],
        'cobalt.io': ['www.cobalt.io'],
        'detectify.com': ['detectify.com'],
        'hackenproof.com': ['hackenproof.com'],
        'zerocopter.com': ['www.zerocopter.com'],
        'openbugbounty.org': ['www.openbugbounty.org'],
        'immunefi.com': ['immunefi.com'],
        'hacktrophy.com': ['www.hacktrophy.com'],
        'federacy.com': ['www.federacy.com'],
        'antihack.me': ['www.antihack.me'],
        'safehats.com': ['www.safehats.com'],
        'yogosha.com': ['www.yogosha.com'],
        'vdp.com': ['vdp.com'],
        'hacktivity.com': ['hacktivity.com'],
        'hackrx.com': ['www.hackrx.com'],
        'hackedu.io': ['www.hackedu.com']
    }
    
    # Check if the base domain has any known valid external domains
    if base_domain in domain_mappings:
        return found_domain in domain_mappings[base_domain]
    
    # For domains not in our mapping, only allow exact domain match
    return found_domain == base_domain

async def playwright_tos_finder(url: str) -> TosResponse:
    """
    Find Terms of Service links using Playwright for JavaScript-rendered content.
    This is a fallback method for when the standard approach fails.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            # Set a reasonable timeout for navigation
            page.set_default_timeout(45000)  # 45 seconds
            
            try:
                # Navigate to the URL with a longer timeout
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Get the final URL after any redirects
                final_url = page.url
                
                # Get the content
                content = await page.content()
                
                # Parse with BeautifulSoup
                soup = BeautifulSoup(content, 'html.parser')
                
                # Find links using our existing function
                tos_link = find_tos_link(final_url, soup)
                
                # If The Verge and no ToS link found, try additional strategies before giving up
                if 'theverge.com' in final_url and not tos_link:
                    logger.info("Extra processing for The Verge with Playwright")
                    
                    # Try scrolling to bottom where footer links usually are
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)  # Wait for any lazy-loaded content
                    
                    # Get updated content after scrolling
                    updated_content = await page.content()
                    updated_soup = BeautifulSoup(updated_content, 'html.parser')
                    
                    # Try to find 'Vox Media' links that might lead to ToS
                    vox_links = await page.query_selector_all('a:text-matches("Vox Media", "i")')
                    for link in vox_links:
                        try:
                            # Try clicking Vox Media link to see if it reveals more links
                            await link.click()
                            await page.wait_for_timeout(2000)
                            
                            # Check for ToS links after clicking
                            post_click_content = await page.content()
                            post_click_soup = BeautifulSoup(post_click_content, 'html.parser')
                            tos_link = find_tos_link(final_url, post_click_soup)
                            if tos_link:
                                break
                        except:
                            continue
                    
                    # If still not found, look for footer links directly
                    if not tos_link:
                        footers = updated_soup.find_all(['footer', 'div'], class_=lambda c: c and ('footer' in c.lower()))
                        for footer in footers:
                            for link in footer.find_all('a', href=True):
                                href = link.get('href')
                                if href and "voxmedia.com" in href and any(term in href.lower() for term in ['/legal', '/terms', '/tos']):
                                    tos_link = href
                                    break
                            if tos_link:
                                break
                
                # If standard approach didn't find links, try using Playwright's own selectors
                if not tos_link:
                    # Try to find links with text containing ToS terms
                    tos_keywords = ["terms", "terms of service", "terms of use", "tos", "terms and conditions", "legal terms"]
                    
                    for keyword in tos_keywords:
                        # Use case-insensitive search for links with text containing the keyword
                        links = await page.query_selector_all(f'a:text-matches("{keyword}", "i")')
                        
                        for link in links:
                            href = await link.get_attribute('href')
                            if href and not href.startswith('javascript:') and href != '#':
                                # Check if it's likely to be an article link
                                if not is_likely_article_link(href.lower(), urljoin(final_url, href)):
                                    tos_link = urljoin(final_url, href)
                                    break
                        
                        if tos_link:
                            break
                    
                    # If still not found, try clicking "I agree" or cookie consent buttons to reveal TOS links
                    if not tos_link:
                        # Try to find and click buttons that might reveal TOS content
                        consent_buttons = await page.query_selector_all('button:text-matches("(accept|agree|got it|cookie|consent)", "i")')
                        
                        for button in consent_buttons:
                            try:
                                await button.click()
                                await page.wait_for_timeout(1000)  # Wait for any changes to take effect
                                
                                # Check for new links after clicking
                                content_after_click = await page.content()
                                soup_after_click = BeautifulSoup(content_after_click, 'html.parser')
                                tos_link = find_tos_link(final_url, soup_after_click)
                                
                                if tos_link:
                                    break
                            except:
                                continue
                
                # Close the browser
                await browser.close()
                
                if tos_link:
                    return TosResponse(
                        url=final_url,
                        tos_url=tos_link,
                        success=True,
                        message=f"Terms of Service link found using JavaScript-enabled browser rendering on page: {final_url}",
                        method_used="playwright"
                    )
                else:
                    return TosResponse(
                        url=final_url,
                        success=False,
                        message=f"No Terms of Service link found even with JavaScript-enabled browser rendering on page: {final_url}",
                        method_used="playwright_failed"
                    )
            
            except Exception as e:
                await browser.close()
                if "Timeout" in str(e) or "timeout" in str(e).lower():
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Timeout while loading page with Playwright: {url}. The site may be slow or blocking automated access.",
                        method_used="playwright_failed_timeout"
                    )
                elif "Navigation failed" in str(e) or "ERR_CONNECTION" in str(e):
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Navigation failed for {url}. The site may be unavailable or blocking automated access.",
                        method_used="playwright_failed_navigation"
                    )
                else:
                    return TosResponse(
                        url=url,
                        success=False,
                        message=f"Error using Playwright to process URL {url}: {str(e)}",
                        method_used="playwright_failed"
                    )
    
    except Exception as e:
        error_msg = f"Error using Playwright to process URL {url}: {str(e)}"
        logger.error(error_msg)
        
        return TosResponse(
            url=url,
            success=False,
            message=error_msg,
            method_used="playwright_failed"
        )

def is_likely_article_link(href: str, base_url: str) -> bool:
    """Check if the URL looks like an article link rather than a ToS link."""
    article_indicators = [
        '/blog/', '/news/', '/article/', '/post/',
        '/2023/', '/2022/', '/2021/', '/2020/',
        '/category/', '/tag/', '/author/'
    ]
    return any(indicator in href.lower() for indicator in article_indicators)

def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs belong to the same domain."""
    domain1 = urlparse(url1).netloc.lower()
    domain2 = urlparse(url2).netloc.lower()
    
    # Remove 'www.' prefix for comparison
    domain1 = domain1.replace('www.', '')
    domain2 = domain2.replace('www.', '')
    
    return domain1 == domain2