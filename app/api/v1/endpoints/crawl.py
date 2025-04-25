from fastapi import APIRouter, Response, HTTPException, status, Depends, Header
import logging
import asyncio
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlparse
import requests
import random
from typing import Dict, Optional, Tuple, Any
import string

from app.api.v1.endpoints.tos import find_tos
from app.api.v1.endpoints.privacy import find_privacy_policy
from app.api.v1.endpoints.extract import extract_text
from app.api.v1.endpoints.summary import generate_summary
from app.api.v1.endpoints.wordfrequency import analyze_word_freq_endpoint, analyze_text_frequency
from app.api.v1.endpoints.textmining import analyze_text as analyze_text_mining, perform_text_mining
from app.api.v1.endpoints.company_info import extract_company_info, extract_company_name_from_domain
from app.core.config import settings

from app.models.tos import ToSRequest
from app.models.privacy import PrivacyRequest
from app.models.extract import ExtractRequest
from app.models.summary import SummaryRequest, SummaryResponse
from app.models.wordfrequency import WordFrequencyRequest, WordFrequencyResponse
from app.models.textmining import TextMiningRequest, TextMiningResponse, TextMiningResults
from app.models.crawl import CrawlTosRequest, CrawlTosResponse, CrawlPrivacyRequest, CrawlPrivacyResponse
from app.models.database import DocumentCreate, SubmissionCreate

from app.crud.document import document_crud
from app.crud.submission import submission_crud

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Default logo URL
DEFAULT_LOGO_URL = "/placeholder.svg?height=48&width=48"

async def perform_parallel_analysis(doc_url: str, extracted_text: str, doc_type: str):
    """
    Performs summary, word frequency, and text mining analyses in parallel.
    Returns a tuple of (summary_response, word_freq_response, text_mining_response)
    """
    # Create all the requests
    summary_request = SummaryRequest(
        url=doc_url,
        text=extracted_text,
        document_type=doc_type
    )
    
    word_freq_request = WordFrequencyRequest(
        url=doc_url,
        document_type=doc_type,
        text=extracted_text,
        max_words=50  # Explicitly setting max_words to avoid potential issues
    )
    
    text_mining_request = TextMiningRequest(
        url=doc_url,
        document_type=doc_type,
        text=extracted_text
    )
    
    try:
        # Run all analyses in parallel for better performance
        summary_task = asyncio.create_task(generate_summary(summary_request, Response()))
        word_freq_task = asyncio.create_task(analyze_word_freq_endpoint(word_freq_request))
        text_mining_task = asyncio.create_task(analyze_text_mining(text_mining_request))
        
        # Wait for all tasks to complete
        responses = await asyncio.gather(summary_task, word_freq_task, text_mining_task, 
                                        return_exceptions=True)
        
        # Handle potential exceptions in each task
        summary_response = responses[0] if not isinstance(responses[0], Exception) else None
        word_freq_response = responses[1] if not isinstance(responses[1], Exception) else None
        text_mining_response = responses[2] if not isinstance(responses[2], Exception) else None
        
        # Create default responses for any failed tasks
        if isinstance(responses[0], Exception):
            logger.error(f"Summary generation failed: {str(responses[0])}")
            summary_response = SummaryResponse(
                url=doc_url, document_type=doc_type, 
                success=False, message=f"Error: {str(responses[0])}"
            )
            
        if isinstance(responses[1], Exception):
            logger.error(f"Word frequency analysis failed: {str(responses[1])}")
            word_freq_response = WordFrequencyResponse(
                url=doc_url, document_type=doc_type, word_frequencies=[],
                success=False, message=f"Error: {str(responses[1])}"
            )
            
        if isinstance(responses[2], Exception):
            logger.error(f"Text mining analysis failed: {str(responses[2])}")
            text_mining_response = TextMiningResponse(
                url=doc_url, 
                document_type=doc_type,
                text_mining=TextMiningResults(
                    word_count=0,
                    avg_word_length=0.0,
                    sentence_count=0,
                    avg_sentence_length=0.0,
                    readability_score=0.0,
                    readability_interpretation="Analysis failed",
                    unique_word_ratio=0.0,
                    capital_letter_freq=0.0,
                    punctuation_density=0.0,
                    question_frequency=0.0,
                    paragraph_count=0,
                    common_word_percentage=0.0
                ),
                success=False, 
                message=f"Error: {str(responses[2])}"
            )
        
        # Return the responses
        return summary_response, word_freq_response, text_mining_response
        
    except Exception as e:
        # Handle any unexpected errors in the parallel processing itself
        logger.error(f"Error in parallel analysis: {str(e)}")
        
        # Create default error responses
        summary_response = SummaryResponse(
            url=doc_url, document_type=doc_type, 
            success=False, message=f"Parallel processing error: {str(e)}"
        )
        
        word_freq_response = WordFrequencyResponse(
            url=doc_url, document_type=doc_type, word_frequencies=[],
            success=False, message=f"Parallel processing error: {str(e)}"
        )
        
        text_mining_response = TextMiningResponse(
            url=doc_url, 
            document_type=doc_type,
            text_mining=TextMiningResults(
                word_count=0,
                avg_word_length=0.0,
                sentence_count=0,
                avg_sentence_length=0.0,
                readability_score=0.0,
                readability_interpretation="Analysis failed",
                unique_word_ratio=0.0,
                capital_letter_freq=0.0,
                punctuation_density=0.0,
                question_frequency=0.0,
                paragraph_count=0,
                common_word_percentage=0.0
            ),
            success=False, 
            message=f"Parallel processing error: {str(e)}"
        )
        
        return summary_response, word_freq_response, text_mining_response

async def save_document_to_db(
    original_url: str,
    document_type: str,
    document_content: str,
    analysis: Dict,
    user_id: Optional[UUID] = None
) -> Optional[UUID]:
    """
    Save document to database after crawling.
    
    Args:
        original_url: The original URL crawled
        document_type: Type of document (TOS or PP)
        document_content: The parsed content
        analysis: Dictionary of analysis results
        user_id: Optional user ID for submission tracking
        
    Returns:
        UUID of created/existing document or None if operation fails
    """
    try:
        # Set a default logo URL
        default_logo_url = "/placeholder.svg?height=48&width=48"
        logo_url = default_logo_url
        
        # Get the domain
        parsed_url = urlparse(original_url)
        domain = parsed_url.netloc
        
        # Extract company name from domain
        company_name = extract_company_name_from_domain(domain)
        
        # Try to get a logo from Google's favicon service
        try:
            logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
            # Test if logo exists with a head request
            response = requests.head(logo_url, timeout=5)
            if response.status_code != 200:
                logo_url = default_logo_url
        except Exception as e:
            logger.warning(f"Failed to get logo for {domain}: {e}")
            logo_url = default_logo_url
        
        logger.info(f"Extracted company name: {company_name}, logo URL: {logo_url}")
        
        # Handle serialization of word frequencies
        serializable_word_freqs = []
        try:
            if isinstance(analysis['word_frequencies'], list):
                serializable_word_freqs = [
                    wf.dict() if hasattr(wf, "dict") and callable(getattr(wf, "dict")) else wf
                    for wf in analysis['word_frequencies']
                ]
            else:
                serializable_word_freqs = analysis['word_frequencies']
        except Exception as e:
            logger.error(f"Error serializing word frequencies: {e}")
            serializable_word_freqs = []
        
        # Handle serialization of text mining metrics
        serializable_text_mining = {}
        if isinstance(analysis['text_mining'], dict):
            serializable_text_mining = analysis['text_mining']
        elif hasattr(analysis['text_mining'], "dict") and callable(getattr(analysis['text_mining'], "dict")):
            serializable_text_mining = analysis['text_mining'].dict()
        else:
            serializable_text_mining = analysis['text_mining']
        
        # Check if a document with this URL already exists
        existing_doc = await document_crud.get_by_retrieved_url(original_url, document_type)
        
        if existing_doc:
            logger.info(f"Document with URL {original_url} already exists. Using existing document.")
            document_id = existing_doc['id']
            
            # Update the document's views count
            await document_crud.increment_views(document_id)
        else:
            # Create a new document
            logger.info(f"Creating new document for URL: {original_url}")
            
            # Create document data dictionary
            document_data = {
                "url": original_url,
                "document_type": document_type,
                "retrieved_url": original_url,
                "company_name": company_name,
                "logo_url": logo_url,
                "raw_text": document_content,
                "one_sentence_summary": analysis['one_sentence_summary'],
                "hundred_word_summary": analysis['hundred_word_summary'],
                "word_frequencies": serializable_word_freqs,
                "text_mining_metrics": serializable_text_mining,
                "views": 0
            }
            
            document = await document_crud.create(document_data)
            document_id = document['id']
        
        return document_id
    except Exception as e:
        logger.error(f"Error saving document to database: {e}")
        raise

async def find_tos_url(url: str) -> str:
    """
    Wrapper function to find Terms of Service URL for a given website URL.
    
    Args:
        url: The website URL to search for Terms of Service
        
    Returns:
        The URL of the Terms of Service page, or None if not found
    """
    logger.info(f"Looking for Terms of Service URL for: {url}")
    
    # Create ToS request
    tos_request = ToSRequest(url=url)
    
    # Call find_tos endpoint function
    response = await find_tos(tos_request)
    
    if response.success:
        logger.info(f"Found Terms of Service URL: {response.tos_url}")
        return response.tos_url
    else:
        logger.warning(f"Could not find Terms of Service URL for {url}: {response.message}")
        return None

async def find_privacy_policy_url(url: str) -> str:
    """
    Wrapper function to find Privacy Policy URL for a given website URL.
    
    Args:
        url: The website URL to search for Privacy Policy
        
    Returns:
        The URL of the Privacy Policy page, or None if not found
    """
    logger.info(f"Looking for Privacy Policy URL for: {url}")
    
    # Create Privacy Policy request
    pp_request = PrivacyRequest(url=url)
    
    # Call find_privacy_policy endpoint function
    response = await find_privacy_policy(pp_request)
    
    if response.success:
        logger.info(f"Found Privacy Policy URL: {response.pp_url}")
        return response.pp_url
    else:
        logger.warning(f"Could not find Privacy Policy URL for {url}: {response.message}")
        return None

@router.post("/crawl-tos", response_model=CrawlTosResponse)
async def crawl_tos(request: CrawlTosRequest) -> CrawlTosResponse:
    """
    Crawl a website to find its Terms of Service, analyze it, and save to database.
    """
    logger.info(f"Received request to crawl TOS from URL: {request.url}")
    response = CrawlTosResponse(url=request.url, success=False, message="Processing request...")
    
    try:
        # Find TOS URL
        tos_url = await find_tos_url(request.url)
        if not tos_url:
            logger.warning(f"No TOS URL found for {request.url}")
            response.tos_url = None
            response.success = False
            response.message = "No terms of service page found."
            return response
            
        logger.info(f"Found TOS URL: {tos_url}")
        response.tos_url = tos_url
        
        # Extract content - now returns a tuple of (text, error)
        extraction_result = await extract_text_from_url(tos_url)
        
        if not extraction_result:
            logger.warning(f"Complete failure extracting content from {tos_url}")
            response.success = False
            response.message = "Failed to extract content from terms of service page."
            return response
            
        extracted_text, extraction_error = extraction_result
        
        if not extracted_text:
            logger.warning(f"Failed to extract content from {tos_url}: {extraction_error}")
            response.success = False
            response.message = f"Failed to extract content: {extraction_error}"
            return response
            
        # Check for very short content which might indicate a bot verification page
        if len(extracted_text.split()) < 100:
            logger.warning(f"Extracted content from {tos_url} is suspiciously short ({len(extracted_text.split())} words)")
            # Check for common verification phrases
            if any(phrase in extracted_text.lower() for phrase in ["verify", "security", "check", "browser"]):
                logger.warning(f"Short extracted content appears to be a verification page: {tos_url}")
                response.success = False
                response.message = "Bot verification page detected - unable to access actual content"
                return response
        
        # Perform analysis in parallel
        analyses = await perform_parallel_analysis(tos_url, extracted_text, "tos")
        
        # Set response fields
        one_sentence_summary = analyses[0].one_sentence_summary if isinstance(analyses[0], SummaryResponse) else "Analysis failed"
        hundred_word_summary = analyses[0].hundred_word_summary if isinstance(analyses[0], SummaryResponse) else "Analysis failed"
        word_frequencies = analyses[1].word_frequencies if isinstance(analyses[1], WordFrequencyResponse) else []
        text_mining_metrics = analyses[2].text_mining if isinstance(analyses[2], TextMiningResponse) else TextMiningResults()
        
        # Get the domain for company info extraction
        parsed_url = urlparse(request.url)
        domain = parsed_url.netloc
        
        # Extract company name and try to get the logo URL
        # First try to get company info
        company_name, logo_url = "", DEFAULT_LOGO_URL
        try:
            # Use the extract_company_info function from company_info module
            company_info_result = await extract_company_info(request.url)
            if company_info_result[2]:  # Check if extraction was successful
                company_name = company_info_result[0]
                logo_url = company_info_result[1]
                logger.info(f"Successfully extracted company info: {company_name}, {logo_url}")
            else:
                # Fall back to domain extraction if company info extraction failed
                company_name = extract_company_name_from_domain(domain)
                
                # Try to get a logo from Google's favicon service
                try:
                    logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                    # Test if logo exists with a head request
                    response_head = requests.head(logo_url, timeout=5)
                    if response_head.status_code != 200:
                        logo_url = DEFAULT_LOGO_URL
                except Exception as e:
                    logger.warning(f"Failed to get logo from favicon service for {domain}: {e}")
                    logo_url = DEFAULT_LOGO_URL
        except Exception as e:
            logger.warning(f"Error extracting company info: {e}")
            # Fall back to simple domain-based extraction
            company_name = extract_company_name_from_domain(domain)
            logo_url = DEFAULT_LOGO_URL
        
        response.one_sentence_summary = one_sentence_summary
        response.hundred_word_summary = hundred_word_summary
        response.word_frequencies = word_frequencies
        response.text_mining = text_mining_metrics
        response.company_name = company_name
        response.logo_url = logo_url
        response.success = True
        response.message = "Successfully crawled and analyzed terms of service."
        
        # Log extracted company name and logo URL
        logger.info(f"Extracted company name: {company_name}, logo URL: {logo_url}")
        
        # Save document to database
        try:
            document_id = await save_document_to_db(
                original_url=request.url,
                document_type="tos",
                document_content=extracted_text,
                analysis={
                    "one_sentence_summary": one_sentence_summary,
                    "hundred_word_summary": hundred_word_summary,
                    "word_frequencies": word_frequencies,
                    "text_mining": text_mining_metrics,
                    "company_name": company_name,
                    "logo_url": logo_url
                },
                user_id=None
            )
            response.document_id = document_id
        except Exception as e:
            logger.error(f"Error saving document to database: {e}")
            # Continue with response even if database save fails
        
        return response
    except Exception as e:
        logger.error(f"Error processing TOS crawl request: {e}")
        response.success = False
        response.message = f"Error processing request: {str(e)}"
        return response
        
@router.post("/crawl-pp", response_model=CrawlPrivacyResponse)
async def crawl_pp(request: CrawlPrivacyRequest) -> CrawlPrivacyResponse:
    """
    Crawl a website to find its Privacy Policy, analyze it, and save to database.
    """
    logger.info(f"Received request to crawl Privacy Policy from URL: {request.url}")
    response = CrawlPrivacyResponse(url=request.url, success=False, message="Processing request...")
    
    try:
        # Find Privacy Policy URL
        pp_url = await find_privacy_policy_url(request.url)
        if not pp_url:
            logger.warning(f"No Privacy Policy URL found for {request.url}")
            response.pp_url = None
            response.success = False
            response.message = "No privacy policy page found."
            return response
            
        logger.info(f"Found Privacy Policy URL: {pp_url}")
        response.pp_url = pp_url
        
        # Extract content - now returns a tuple of (text, error)
        extraction_result = await extract_text_from_url(pp_url)
        
        if not extraction_result:
            logger.warning(f"Complete failure extracting content from {pp_url}")
            response.success = False
            response.message = "Failed to extract content from privacy policy page."
            return response
            
        extracted_text, extraction_error = extraction_result
        
        if not extracted_text:
            logger.warning(f"Failed to extract content from {pp_url}: {extraction_error}")
            response.success = False
            response.message = f"Failed to extract content: {extraction_error}"
            return response
            
        # Check for very short content which might indicate a bot verification page
        if len(extracted_text.split()) < 100:
            logger.warning(f"Extracted content from {pp_url} is suspiciously short ({len(extracted_text.split())} words)")
            # Check for common verification phrases
            if any(phrase in extracted_text.lower() for phrase in ["verify", "security", "check", "browser"]):
                logger.warning(f"Short extracted content appears to be a verification page: {pp_url}")
                response.success = False
                response.message = "Bot verification page detected - unable to access actual content"
                return response
        
        # Perform analysis in parallel
        analyses = await perform_parallel_analysis(pp_url, extracted_text, "pp")
        
        # Set response fields
        one_sentence_summary = analyses[0].one_sentence_summary if isinstance(analyses[0], SummaryResponse) else "Analysis failed"
        hundred_word_summary = analyses[0].hundred_word_summary if isinstance(analyses[0], SummaryResponse) else "Analysis failed"
        word_frequencies = analyses[1].word_frequencies if isinstance(analyses[1], WordFrequencyResponse) else []
        text_mining_metrics = analyses[2].text_mining if isinstance(analyses[2], TextMiningResponse) else TextMiningResults()
        
        # Get the domain for company info extraction
        parsed_url = urlparse(request.url)
        domain = parsed_url.netloc
        
        # Extract company name and try to get the logo URL
        # First try to get company info
        company_name, logo_url = "", DEFAULT_LOGO_URL
        try:
            # Use the extract_company_info function from company_info module
            company_info_result = await extract_company_info(request.url)
            if company_info_result[2]:  # Check if extraction was successful
                company_name = company_info_result[0]
                logo_url = company_info_result[1]
                logger.info(f"Successfully extracted company info: {company_name}, {logo_url}")
            else:
                # Fall back to domain extraction if company info extraction failed
                company_name = extract_company_name_from_domain(domain)
                
                # Try to get a logo from Google's favicon service
                try:
                    logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                    # Test if logo exists with a head request
                    response_head = requests.head(logo_url, timeout=5)
                    if response_head.status_code != 200:
                        logo_url = DEFAULT_LOGO_URL
                except Exception as e:
                    logger.warning(f"Failed to get logo from favicon service for {domain}: {e}")
                    logo_url = DEFAULT_LOGO_URL
        except Exception as e:
            logger.warning(f"Error extracting company info: {e}")
            # Fall back to simple domain-based extraction
            company_name = extract_company_name_from_domain(domain)
            logo_url = DEFAULT_LOGO_URL
        
        # Set response fields
        response.one_sentence_summary = one_sentence_summary
        response.hundred_word_summary = hundred_word_summary
        response.word_frequencies = word_frequencies
        response.text_mining = text_mining_metrics
        response.company_name = company_name
        response.logo_url = logo_url
        response.success = True
        response.message = "Successfully crawled and analyzed privacy policy."
        
        # Log extracted company name and logo URL
        logger.info(f"Extracted company name: {company_name}, logo URL: {logo_url}")
        
        # Save document to database
        try:
            document_id = await save_document_to_db(
                original_url=request.url,
                document_type="pp",
                document_content=extracted_text,
                analysis={
                    "one_sentence_summary": one_sentence_summary,
                    "hundred_word_summary": hundred_word_summary,
                    "word_frequencies": word_frequencies,
                    "text_mining": text_mining_metrics,
                    "company_name": company_name,
                    "logo_url": logo_url
                },
                user_id=None
            )
            response.document_id = document_id
        except Exception as e:
            logger.error(f"Error saving document to database: {e}")
            # Continue with response even if database save fails
        
        return response
    except Exception as e:
        logger.error(f"Error processing Privacy Policy crawl request: {e}")
        response.success = False
        response.message = f"Error processing request: {str(e)}"
        return response

async def extract_text_from_url(url: str) -> Optional[Tuple[str, str]]:
    """
    Wrapper function to extract text from a URL with enhanced anti-bot protection.
    
    Args:
        url: The URL to extract text from
        
    Returns:
        Tuple of (extracted_text, error_message) or None if completely failed
    """
    logger.info(f"Extracting text from URL: {url}")
    
    # Create extract request
    extract_request = ExtractRequest(url=url, document_type="tos")
    
    # Try multiple times with different methods if needed
    max_attempts = 3
    last_error = ""
    
    for attempt in range(1, max_attempts + 1):
        try:
            # Call extract_text endpoint function
            logger.info(f"Extraction attempt {attempt}/{max_attempts} for URL: {url}")
            
            # Send the extraction request
            response = await extract_text(extract_request, Response())
            
            if response and hasattr(response, 'success') and response.success and response.text:
                # Check if the extracted content looks like a bot verification page
                if "verify yourself" in response.text.lower() or "security check" in response.text.lower():
                    logger.warning(f"Bot verification page detected for URL: {url}")
                    last_error = "Bot verification page detected - unable to access actual content"
                    
                    # Try once more with a different approach if this isn't our last attempt
                    if attempt < max_attempts:
                        logger.info(f"Retrying with different approach for URL: {url}")
                        await asyncio.sleep(random.uniform(5.0, 8.0))
                        continue
                else:
                    logger.info(f"Successfully extracted text from URL: {url} using method: {response.method_used}")
                    # Return the successfully extracted text and no error
                    return (response.text, None)
            else:
                # Log specific reason for failure if available
                error_msg = response.message if hasattr(response, 'message') else "Unknown extraction error"
                logger.warning(f"Failed to extract text from URL {url} on attempt {attempt}/{max_attempts}: {error_msg}")
                last_error = error_msg
                
                # If this is the last attempt, return empty string and the error
                if attempt == max_attempts:
                    logger.error(f"All extraction attempts failed for URL: {url}")
                    return ("", last_error)
                    
                # Vary wait time between attempts to avoid detection
                await asyncio.sleep(random.uniform(3.0, 5.0))
        except Exception as e:
            error_str = str(e)
            logger.error(f"Error extracting text from URL {url} on attempt {attempt}/{max_attempts}: {error_str}")
            last_error = error_str
            
            # If the error indicates bot detection, record that specifically
            if "bot verification" in error_str.lower() or "captcha" in error_str.lower():
                last_error = "Bot verification detected: " + error_str
            
            # If this is the last attempt, return empty string and the error
            if attempt == max_attempts:
                logger.error(f"All extraction attempts failed for URL: {url}")
                return ("", last_error)
                
            # Increase wait time with each failure
            await asyncio.sleep(random.uniform(2.0 * attempt, 4.0 * attempt))
    
    logger.warning(f"Failed to extract content from {url}")
    return ("", "Failed to extract content from URL")

async def generate_one_sentence_summary(text: str, url: str = None) -> str:
    """
    Generate a one-sentence summary of the provided text.
    
    Args:
        text: The text to summarize
        url: Optional URL for context
        
    Returns:
        A one-sentence summary of the text
    """
    logger.info("Generating one-sentence summary")
    
    if not text or len(text.strip()) < 100:
        logger.warning("Text is too short for summarization")
        return "Text too short for summarization"
    
    try:
        # Create a summary request
        summary_request = SummaryRequest(
            text=text,
            document_type="tos",  # Default document type
            url=url  # Pass the URL if available
        )
        
        # Call the generate_summary endpoint
        summary_response = await generate_summary(summary_request, Response())
        
        if summary_response and summary_response.success:
            if summary_response.one_sentence_summary:
                logger.info("Successfully generated one-sentence summary")
                return summary_response.one_sentence_summary
            else:
                logger.warning("Empty one-sentence summary returned")
                return "Summary generation returned empty result"
        else:
            error_msg = summary_response.message if summary_response else "Unknown error"
            logger.error(f"Failed to generate one-sentence summary: {error_msg}")
            return f"Error generating summary: {error_msg}"
    except Exception as e:
        logger.error(f"Exception during one-sentence summary generation: {str(e)}")
        return f"Error generating summary: {str(e)}"

async def generate_hundred_word_summary(text: str, url: str = None) -> str:
    """
    Generate a hundred-word summary of the provided text.
    
    Args:
        text: The text to summarize
        url: Optional URL for context
        
    Returns:
        A hundred-word summary of the text
    """
    logger.info("Generating hundred-word summary")
    
    if not text or len(text.strip()) < 200:
        logger.warning("Text is too short for summarization")
        return "Text too short for summarization"
    
    try:
        # Create a summary request
        summary_request = SummaryRequest(
            text=text,
            document_type="tos",  # Default document type
            url=url  # Pass the URL if available
        )
        
        # Call the generate_summary endpoint
        summary_response = await generate_summary(summary_request, Response())
        
        if summary_response and summary_response.success:
            if summary_response.hundred_word_summary:
                logger.info("Successfully generated hundred-word summary")
                return summary_response.hundred_word_summary
            else:
                logger.warning("Empty hundred-word summary returned")
                return "Summary generation returned empty result"
        else:
            error_msg = summary_response.message if summary_response else "Unknown error"
            logger.error(f"Failed to generate hundred-word summary: {error_msg}")
            return f"Error generating summary: {error_msg}"
    except Exception as e:
        logger.error(f"Exception during hundred-word summary generation: {str(e)}")
        return f"Error generating summary: {str(e)}"

def get_word_frequencies(text: str, max_words: int = 20):
    """
    Analyze word frequencies in the provided text.
    
    Args:
        text: The text to analyze
        max_words: Maximum number of words to include in results
        
    Returns:
        List of WordFrequency objects
    """
    return analyze_text_frequency(text, max_words)

def extract_text_mining_metrics(text: str):
    """
    Extract text mining metrics from the provided text.
    
    Args:
        text: The text to analyze
        
    Returns:
        TextMiningResults object
    """
    return perform_text_mining(text) 