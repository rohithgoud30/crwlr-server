from fastapi import APIRouter, Response, HTTPException, status, Depends, Header, Query
import logging
import asyncio
from urllib.parse import urlparse
import requests
import random
from typing import Dict, Optional, Tuple, Any, Union, List, Literal
import string
import json
import re
import time
from datetime import datetime
from pydantic import BaseModel, Field
from firebase_admin import firestore

from app.api.v1.endpoints.tos import find_tos, ToSRequest
from app.api.v1.endpoints.privacy import find_privacy_policy, PrivacyRequest
from app.api.v1.endpoints.extract import extract_text, extract_with_playwright
from app.api.v1.endpoints.summary import generate_summary
from app.api.v1.endpoints.wordfrequency import analyze_word_freq_endpoint, analyze_text_frequency
from app.api.v1.endpoints.textmining import analyze_text as analyze_text_mining, perform_text_mining
from app.api.v1.endpoints.company_info import extract_company_info, extract_company_name_from_domain, get_company_info
from app.core.config import settings
from app.core.firebase import db
from app.core.typesense import get_typesense_client, TYPESENSE_COLLECTION_NAME
from app.core.database import (
    get_document_by_url,
    increment_views
)

from app.models.summary import SummaryRequest, SummaryResponse
from app.models.extract import ExtractRequest, ExtractResponse
from app.models.wordfrequency import WordFrequencyRequest, WordFrequencyResponse, WordFrequency
from app.models.textmining import TextMiningRequest, TextMiningResponse, TextMiningResults
from app.models.crawl import CrawlTosRequest, CrawlTosResponse, CrawlPrivacyRequest, CrawlPrivacyResponse, ReanalyzeTosRequest, ReanalyzeTosResponse, ReanalyzePrivacyRequest, ReanalyzePrivacyResponse
from app.models.database import DocumentCreate, SubmissionCreate
from app.models.company_info import CompanyInfoRequest
from app.crud.submission import submission_crud
from app.core.auth import get_api_key

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Replace the static DEFAULT_LOGO_URL with a function
def get_default_logo_url(url=None):
    """
    Returns a Google favicon URL for the given domain.
    If no URL is provided, returns a generic Google favicon.
    """
    if not url:
        return "https://www.google.com/s2/favicons?domain=example.com&sz=128"
    
    try:
        # Extract domain from URL
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        if not domain:
            # Try to handle URLs without scheme
            domain = url.split('/')[0]
        
        # Remove www. prefix if present
        domain = re.sub(r'^www\.', '', domain)
        
        if domain:
            return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
        else:
            return "https://www.google.com/s2/favicons?domain=example.com&sz=128"
    except:
        return "https://www.google.com/s2/favicons?domain=example.com&sz=128"

# Keep this for backward compatibility but it will be deprecated
DEFAULT_LOGO_URL = get_default_logo_url()  # Now uses Google favicon as default

async def perform_parallel_analysis(doc_url: str, extracted_text: str, doc_type: str):
    """
    Perform parallel analysis of extracted text.
    
    Args:
        doc_url: The URL of the document.
        extracted_text: The extracted text to analyze.
        doc_type: The type of document (TOS or PP).
        
    Returns:
        Dictionary containing all analysis results.
    """
    logger.info(f"Starting parallel analysis for document type: {doc_type}")
    
    # Create async wrapper functions for synchronous operations
    async def get_word_frequencies_async():
        return get_word_frequencies(extracted_text)
        
    async def extract_text_mining_metrics_async():
        return extract_text_mining_metrics(extracted_text)
    
    # Run all analyses in parallel using asyncio.gather
    word_freqs_task = get_word_frequencies_async()
    text_mining_task = extract_text_mining_metrics_async()
    one_sentence_summary_task = generate_one_sentence_summary(extracted_text, doc_url, doc_type)
    hundred_word_summary_task = generate_hundred_word_summary(extracted_text, doc_url, doc_type)
    
    # Wait for all tasks to complete
    word_freqs, text_mining, one_sentence_summary, hundred_word_summary = await asyncio.gather(
        word_freqs_task,
        text_mining_task,
        one_sentence_summary_task,
        hundred_word_summary_task
    )
    
    # Combine results into a single dictionary
    results = {
        "one_sentence_summary": one_sentence_summary,
        "hundred_word_summary": hundred_word_summary,
        "word_frequencies": word_freqs,
        "text_mining": text_mining
    }
    
    logger.info("All analyses completed successfully")
    return results

async def save_document_to_db(
    original_url: str, # User requested URL
    retrieved_url: str, # Actual URL content was fetched from
    document_type: str,
    document_content: str,
    analysis: Dict,
    user_email: Optional[str] = None
) -> Optional[str]:
    """
    Save document to database after crawling.
    
    Args:
        original_url: The original URL requested by the user.
        retrieved_url: The actual URL the content was retrieved from.
        document_type: Type of document (TOS or PP).
        document_content: The parsed content.
        analysis: Dictionary of analysis results.
        user_email: User's email for tracking submissions
        
    Returns:
        String ID of created/existing document or None if operation fails.
    """
    # Handle binary content - sanitize before saving to prevent UTF-8 errors
    document_content = sanitize_text_for_db(document_content)
    
    content_length = len(document_content) if document_content else 0
    # Log the length and beginning of the document content
    logger.info(f"Processing document content (Length: {content_length}, First 1000 chars): {document_content[:1000]}")
    
    # Log incoming analysis data types and content
    wf_data = analysis.get('word_frequencies')
    tm_data = analysis.get('text_mining')
    logger.info(f"Incoming word_frequencies type: {type(wf_data)}, content snippet: {str(wf_data)[:500]}")
    logger.info(f"Incoming text_mining type: {type(tm_data)}, content snippet: {str(tm_data)[:500]}")

    try:
        # Check if document already exists by URL and document type
        from app.crud.document import document_crud
        
        existing_doc = await document_crud.get_by_url_and_type(original_url, document_type)
        # Only check base URL for duplicates; allow same retrieved_url for different base URLs
        
        # Use company_name and logo_url from analysis if available
        company_name = analysis.get('company_name', None)
        logo_url = analysis.get('logo_url', None)
        
        # Only extract company name and logo if not already provided in analysis
        if not company_name or not logo_url:
            # Extract from the ORIGINAL URL for fallback info
            parsed_url = urlparse(original_url)
            domain = parsed_url.netloc
            
            if not company_name:
                company_name = extract_company_name_from_domain(domain)
                
            if not logo_url:
                logo_url = get_default_logo_url(original_url)
        
        # Serialize WordFrequency objects for Firebase storage
        serializable_word_freqs = []
        if 'word_frequencies' in analysis and isinstance(analysis['word_frequencies'], list):
            for item in analysis['word_frequencies']:
                if isinstance(item, WordFrequency):
                    serializable_word_freqs.append({
                        "word": item.word,
                        "count": item.count,
                        "percentage": item.percentage
                    })
                elif isinstance(item, dict):
                    serializable_word_freqs.append(item)
                elif hasattr(item, 'dict') and callable(getattr(item, 'dict')):
                    serializable_word_freqs.append(item.dict())
                elif hasattr(item, '__dict__'):
                    serializable_word_freqs.append(item.__dict__)
                else:
                    logger.warning(f"Skipping word frequency item of unexpected type: {type(item)}")
        
        # Serialize TextMiningResults for Firebase storage
        serializable_text_mining = {}
        if 'text_mining' in analysis:
            if isinstance(analysis['text_mining'], TextMiningResults):
                serializable_text_mining = {
                    "word_count": analysis['text_mining'].word_count,
                    "avg_word_length": analysis['text_mining'].avg_word_length,
                    "sentence_count": analysis['text_mining'].sentence_count,
                    "avg_sentence_length": analysis['text_mining'].avg_sentence_length,
                    "readability_score": analysis['text_mining'].readability_score,
                    "readability_interpretation": analysis['text_mining'].readability_interpretation,
                    "unique_word_ratio": analysis['text_mining'].unique_word_ratio,
                    "capital_letter_freq": analysis['text_mining'].capital_letter_freq,
                    "punctuation_density": analysis['text_mining'].punctuation_density,
                    "question_frequency": analysis['text_mining'].question_frequency,
                    "paragraph_count": analysis['text_mining'].paragraph_count,
                    "common_word_percentage": analysis['text_mining'].common_word_percentage
                }
            elif isinstance(analysis['text_mining'], dict):
                serializable_text_mining = analysis['text_mining']
            elif hasattr(analysis['text_mining'], "dict") and callable(getattr(analysis['text_mining'], "dict")):
                serializable_text_mining = analysis['text_mining'].dict()
            elif hasattr(analysis['text_mining'], "__dict__"):
                serializable_text_mining = analysis['text_mining'].__dict__
            else:
                serializable_text_mining = {"error": "Could not convert text mining metrics"}
                logger.warning(f"Could not convert text mining metrics to dict: {type(analysis['text_mining'])}")
        
        # Create document data for database
        document_data = {
            "url": original_url,
            "document_type": document_type,
            "retrieved_url": retrieved_url,
            "company_name": company_name,
            "logo_url": logo_url,
            "raw_text": "", # Store empty string instead of full raw text
            "one_sentence_summary": analysis.get('one_sentence_summary', ''),
            "hundred_word_summary": analysis.get('hundred_word_summary', ''),
            "word_frequencies": serializable_word_freqs,
            "text_mining_metrics": serializable_text_mining,
            "updated_at": datetime.now()
        }
        
        # Add user_email to the document data if provided - just store email directly without user collection reference
        if user_email:
            document_data["user_email"] = user_email
        
        # If the document already exists, update it instead of creating a new one
        document_id = None
        if existing_doc:
            # Document exists, update it (keeping the view count and created_at intact)
            document_id = existing_doc.get('id')
            logger.info(f"Updating existing document with ID: {document_id}")
            
            # Don't update views or created_at
            document_data.pop('views', None)
            document_data.pop('created_at', None)
            
            # Update the document using DocumentCRUD
            update_result = await document_crud.update_document_analysis(document_id, document_data)
            if not update_result:
                logger.error(f"Failed to update existing document with ID: {document_id}")
                raise Exception(f"Failed to update document with ID: {document_id}")
        else:
            # Document doesn't exist, create a new one
            logger.info("Creating new document")
            # Add additional fields needed for new documents
            document_data["views"] = 0
            document_data["created_at"] = datetime.now()
            
            # Create the document
            created_doc = await document_crud.create(document_data)
            if not created_doc:
                logger.error("Failed to create document in Firestore")
                raise Exception("Failed to create document in Firestore")
            
            document_id = created_doc.get('id')
            
        logger.info(f"Document {'updated' if existing_doc else 'saved'} in Firestore with ID: {document_id}")
        
        # Update the last_updated timestamp in stats collection
        from app.crud.stats import stats_crud
        await stats_crud.update_last_updated()
        
        return document_id
    except Exception as e:
        # Log traceback for save_document_to_db errors
        logger.error(f"Error saving document to database: {e}", exc_info=True)
        raise  # Re-raise the exception to be handled by the caller

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
    logger.info(f"Received request to crawl Terms of Service from URL: {request.url}")
    response = CrawlTosResponse(url=request.url, success=False, message="Processing request...")
    is_existing_document = False  # Flag to track if this is an existing document
    
    try:
        # FIRST: Check if document with THIS EXACT URL already exists in database
        # We only check by original URL and do it ONCE at the beginning
        try:
            existing_doc = get_document_by_url(request.url, "tos")
            if existing_doc:
                logger.info(f"Document for URL {request.url} already exists in database with ID {existing_doc['id']}. Returning existing document.")
                is_existing_document = True  # Set flag for existing document
                
                # Update views count for the existing document - increment_views is async
                await increment_views(existing_doc['id'])
                
                # Create a new response object with success=False
                response = CrawlTosResponse(
                    url=request.url,
                    success=False,  # CRITICAL: Must be False for existing documents
                    message="Document already exists in database.",
                    document_id=existing_doc['id'],
                    tos_url=existing_doc.get('retrieved_url', request.url),
                    one_sentence_summary=existing_doc.get('one_sentence_summary', ''),
                    hundred_word_summary=existing_doc.get('hundred_word_summary', '')
                )
                
                # Parse JSON fields if needed
                try:
                    if isinstance(existing_doc.get('word_frequencies'), str):
                        # Parse the JSON string to a list of dictionaries
                        word_freq_data = json.loads(existing_doc.get('word_frequencies', '[]'))
                        # Convert each dictionary to a WordFrequency model instance
                        response.word_frequencies = [
                            WordFrequency(
                                word=item.get('word', ''),
                                count=item.get('count', 0),
                                percentage=item.get('percentage', 0.0)
                            ) 
                            for item in word_freq_data
                        ]
                    else:
                        # Create WordFrequency instances from the list of dictionaries
                        word_freq_data = existing_doc.get('word_frequencies', [])
                        response.word_frequencies = [
                            WordFrequency(
                                word=item.get('word', ''),
                                count=item.get('count', 0),
                                percentage=item.get('percentage', 0.0)
                            ) 
                            for item in word_freq_data
                        ]
                        
                    if isinstance(existing_doc.get('text_mining_metrics'), str):
                        # Parse the JSON string to a dictionary
                        tm_data = json.loads(existing_doc.get('text_mining_metrics', '{}'))
                        # Create a TextMiningResults instance from the dictionary
                        response.text_mining = TextMiningResults(
                            word_count=tm_data.get('word_count', 0),
                            avg_word_length=tm_data.get('avg_word_length', 0.0),
                            sentence_count=tm_data.get('sentence_count', 0),
                            avg_sentence_length=tm_data.get('avg_sentence_length', 0.0),
                            readability_score=tm_data.get('readability_score', 0.0),
                            readability_interpretation=tm_data.get('readability_interpretation', ''),
                            unique_word_ratio=tm_data.get('unique_word_ratio', 0.0),
                            capital_letter_freq=tm_data.get('capital_letter_freq', 0.0),
                            punctuation_density=tm_data.get('punctuation_density', 0.0),
                            question_frequency=tm_data.get('question_frequency', 0.0),
                            paragraph_count=tm_data.get('paragraph_count', 0),
                            common_word_percentage=tm_data.get('common_word_percentage', 0.0)
                        )
                    else:
                        # Create a TextMiningResults instance from the dictionary
                        tm_data = existing_doc.get('text_mining_metrics', {})
                        response.text_mining = TextMiningResults(
                            word_count=tm_data.get('word_count', 0),
                            avg_word_length=tm_data.get('avg_word_length', 0.0),
                            sentence_count=tm_data.get('sentence_count', 0),
                            avg_sentence_length=tm_data.get('avg_sentence_length', 0.0),
                            readability_score=tm_data.get('readability_score', 0.0),
                            readability_interpretation=tm_data.get('readability_interpretation', ''),
                            unique_word_ratio=tm_data.get('unique_word_ratio', 0.0),
                            capital_letter_freq=tm_data.get('capital_letter_freq', 0.0),
                            punctuation_density=tm_data.get('punctuation_density', 0.0),
                            question_frequency=tm_data.get('question_frequency', 0.0),
                            paragraph_count=tm_data.get('paragraph_count', 0),
                            common_word_percentage=tm_data.get('common_word_percentage', 0.0)
                        )
                except Exception as e:
                    logger.warning(f"Error parsing JSON fields from existing document: {e}")
                    response.word_frequencies = []
                    response.text_mining = TextMiningResults(
                        word_count=0,
                        avg_word_length=0.0,
                        sentence_count=0,
                        avg_sentence_length=0.0,
                        readability_score=0.0,
                        readability_interpretation="Error parsing data",
                        unique_word_ratio=0.0,
                        capital_letter_freq=0.0,
                        punctuation_density=0.0,
                        question_frequency=0.0,
                        paragraph_count=0,
                        common_word_percentage=0.0
                    )
                
                response.company_name = existing_doc.get('company_name', '')
                response.logo_url = existing_doc.get('logo_url', get_default_logo_url(request.url))
                
                # CRITICAL: Ensure success is False for existing documents
                response.success = False
                
                # Final check to ensure success is False for existing documents
                if is_existing_document:
                    response.success = False
                    
                return response
        except Exception as e:
            logger.warning(f"Error checking for existing document: {e}")
            # Continue with processing if the check fails
        
        # Find Terms of Service URL
        tos_url = await find_tos_url(request.url)
        if not tos_url:
            logger.warning(f"No Terms of Service URL found for {request.url}")
            response.tos_url = None
            response.success = False
            response.message = "No terms of service page found."
            return response
            
        logger.info(f"Found Terms of Service URL: {tos_url}")
        response.tos_url = tos_url
        
        # Extract content - now returns a tuple of (text, error)
        extraction_result = await extract_text_from_url(tos_url, "tos")
        
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
                
        # Extract company name and logo URL right after content extraction
        # This way we can include the company name in the summary process
        parsed_url = urlparse(request.url)
        domain = parsed_url.netloc
        
        # Extract company name and try to get the logo URL
        company_name = ""
        logo_url = get_default_logo_url(request.url)
        try:
            # Create a request that includes the URL and any logo URL from the request
            request_info = CompanyInfoRequest(url=request.url)
            
            # Add the logo URL if it's available in the request
            if hasattr(request, 'logo_url') and request.logo_url:
                request_info.logo_url = request.logo_url
                
            # Send the request to get_company_info function directly
            company_info_response = await get_company_info(request_info)
            
            if company_info_response.success:
                company_name = company_info_response.company_name
                logo_url = company_info_response.logo_url
                logger.info(f"Successfully extracted company info: {company_name}, {logo_url}")
            else:
                # Fall back to domain extraction if company info extraction failed
                company_name = extract_company_name_from_domain(domain)
                logger.info(f"Company info extraction failed, using domain-based name: {company_name}")
                
                # Check if we have a logo URL
                if company_info_response.logo_url and company_info_response.logo_url != get_default_logo_url(request.url):
                    logo_url = company_info_response.logo_url
                else:
                    # Try to get a logo from Google's favicon service
                    try:
                        logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                        # Test if logo exists with a head request
                        response_head = requests.head(logo_url, timeout=5)
                        if response_head.status_code != 200:
                            logo_url = get_default_logo_url(request.url)
                    except Exception as e:
                        logger.warning(f"Failed to get logo from favicon service for {domain}: {e}")
                        logo_url = get_default_logo_url(request.url)
        except Exception as e:
            logger.warning(f"Error extracting company info: {e}")
            # Fall back to simple domain-based extraction
            company_name = extract_company_name_from_domain(domain)
            logger.info(f"Exception in company info extraction, using domain-based name: {company_name}")
            logo_url = get_default_logo_url(request.url)
        
        # Final safety check - ensure we have a company name
        if not company_name or company_name.strip() == "":
            # Try direct domain extraction as a last resort
            try:
                # Parse the URL directly
                parsed_url = urlparse(request.url)
                domain = parsed_url.netloc
                
                # If domain is empty, try to add a protocol and parse again
                if not domain:
                    if not request.url.startswith(('http://', 'https://')):
                        fixed_url = 'https://' + request.url
                        parsed_url = urlparse(fixed_url)
                        domain = parsed_url.netloc
                
                if domain:
                    company_name = extract_company_name_from_domain(domain)
                    logger.info(f"Final fallback: Using domain {domain} to extract company name: {company_name}")
                else:
                    # Use parts of the URL if domain extraction failed
                    parts = request.url.split('/')
                    if len(parts) > 0:
                        company_name = parts[0].capitalize()
                        logger.info(f"Final fallback: Using URL part {parts[0]} as company name")
                    else:
                        company_name = request.url.capitalize()
                        logger.info(f"Final fallback: Using entire URL as company name")
            except Exception as e:
                logger.error(f"Error in final company name extraction: {e}")
                company_name = request.url.capitalize()
                logger.info(f"Emergency fallback: Using URL as company name")
        
        # Now create a modified summary request that includes the company name
        # This is done by modifying the tos_url to include context
        summary_context_url = f"{tos_url}?company={company_name}"
        
        # Perform analysis in parallel with the company name context
        analysis = await perform_parallel_analysis(summary_context_url, extracted_text, "tos")
        
        # Set response fields from analysis results regardless of success
        response.one_sentence_summary = analysis.get('one_sentence_summary', "Analysis failed")
        response.hundred_word_summary = analysis.get('hundred_word_summary', "Analysis failed")
        response.word_frequencies = analysis.get('word_frequencies', [])
        response.text_mining = analysis.get('text_mining', TextMiningResults(
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
        ))
        response.company_name = company_name
        response.logo_url = logo_url
        
        # Check if all analyses were successful before attempting to save
        all_analyses_successful = (
            analysis.get('one_sentence_summary') and analysis.get('hundred_word_summary') and
            analysis.get('word_frequencies') and analysis.get('text_mining') and
            not analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('one_sentence_summary', "").startswith("Error generating summary:") and
            not analysis.get('hundred_word_summary', "").startswith("Error generating summary:") and
            not analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") and
            not analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")
        )
        
        if all_analyses_successful:
            logger.info("All analyses successful. Proceeding to save document.")
            response.success = True
            response.message = "Successfully crawled and analyzed terms of service."
            try:
                document_id = await save_document_to_db(
                    original_url=request.url, # The user's requested URL
                    retrieved_url=tos_url,   # The actual URL the TOS was found at
                    document_type="tos",
                    document_content=extracted_text,
                    analysis={
                        "one_sentence_summary": response.one_sentence_summary,
                        "hundred_word_summary": response.hundred_word_summary,
                        "word_frequencies": response.word_frequencies,
                        "text_mining": response.text_mining,
                        "company_name": company_name,
                        "logo_url": logo_url
                    },
                    user_email=getattr(request, 'user_email', None)
                )
                response.document_id = document_id
                # Success remains true since we saved the document and are returning valid data
            except Exception as e:
                logger.error(f"Error saving document to database: {e}")
                # If saving failed but we have valid analyses, set success=false
                # This indicates a new document was not created, even with valid data
                response.success = False
                response.message = "Successfully crawled and analyzed, but failed to save document."
        else:
            logger.warning("One or more analyses failed, but returning available data.")
            # Even if some analyses failed, if we have useful data, consider it partially successful
            has_useful_data = (
                (analysis.get('one_sentence_summary') or analysis.get('hundred_word_summary')) or
                analysis.get('word_frequencies') or
                analysis.get('text_mining')
            )
            
            if has_useful_data:
                # Even with useful data, if it wasn't saved, success should be false
                response.success = False
                
                # Check if summaries failed and provide a more specific message
                if (analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") or 
                    analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") or
                    analysis.get('one_sentence_summary', "").startswith("Error generating summary:") or
                    analysis.get('hundred_word_summary', "").startswith("Error generating summary:")):
                    logger.warning(f"Document NOT saved to database due to summary generation failure. One-sentence: '{analysis.get('one_sentence_summary', '')[:100]}...', Hundred-word: '{analysis.get('hundred_word_summary', '')[:100]}...'")
                    response.message = "Document analysis incomplete: Summary generation failed. Document not saved."
                elif (analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") or
                      analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")):
                    logger.warning(f"Document NOT saved to database because text is too short for summarization. Text length: {len(extracted_text)}")
                    response.message = "Document not saved: Text is too short for meaningful analysis. A minimum of 200 characters is required."
                else:
                    response.message = "Partial success: some analyses completed, returning available data but not saved."
            else:
                response.success = False
                response.message = "Failed to analyze content properly, no useful data available."
            
            # Ensure document_id is None if not saved
            response.document_id = None
            
        # Log extracted company name and logo URL (moved here to always log)
        logger.info(f"Extracted company name: {company_name}, logo URL: {logo_url}")
            
        # Final check before returning to ensure success is False for existing documents
        if is_existing_document or response.message == "Document already exists in database.":
            response.success = False
            
        return response
    except Exception as e:
        # Log traceback for crawl_tos errors
        logger.error(f"Error processing TOS crawl request: {e}", exc_info=True)
        # Ensure response fields are set appropriately on error
        response.success = False
        response.message = f"Error processing request: {str(e)}"
        response.one_sentence_summary = "Error"
        response.hundred_word_summary = "Error"
        response.word_frequencies = []
        response.text_mining = TextMiningResults(
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
        )
        response.company_name = "Error"
        response.logo_url = get_default_logo_url(request.url)
        response.document_id = None
        return response
        
@router.post("/crawl-pp", response_model=CrawlPrivacyResponse)
async def crawl_pp(request: CrawlPrivacyRequest) -> CrawlPrivacyResponse:
    """
    Crawl a website to find its Privacy Policy, analyze it, and save to database.
    """
    logger.info(f"Received request to crawl Privacy Policy from URL: {request.url}")
    response = CrawlPrivacyResponse(url=request.url, success=False, message="Processing request...")
    is_existing_document = False  # Flag to track if this is an existing document
    
    try:
        # FIRST: Check if document with THIS EXACT URL already exists in database
        # We only check by original URL and do it ONCE at the beginning
        try:
            existing_doc = get_document_by_url(request.url, "pp")
            if existing_doc:
                logger.info(f"Document for URL {request.url} already exists in database with ID {existing_doc['id']}. Returning existing document.")
                is_existing_document = True  # Set flag for existing document
                
                # Update views count for the existing document - increment_views is async
                await increment_views(existing_doc['id'])
                
                # Create a new response object with success=False
                response = CrawlPrivacyResponse(
                    url=request.url,
                    success=False,  # CRITICAL: Must be False for existing documents
                    message="Document already exists in database.",
                    document_id=existing_doc['id'],
                    pp_url=existing_doc.get('retrieved_url', request.url),
                    one_sentence_summary=existing_doc.get('one_sentence_summary', ''),
                    hundred_word_summary=existing_doc.get('hundred_word_summary', '')
                )
                
                # Parse JSON fields if needed
                try:
                    if isinstance(existing_doc.get('word_frequencies'), str):
                        # Parse the JSON string to a list of dictionaries
                        word_freq_data = json.loads(existing_doc.get('word_frequencies', '[]'))
                        # Convert each dictionary to a WordFrequency model instance
                        response.word_frequencies = [
                            WordFrequency(
                                word=item.get('word', ''),
                                count=item.get('count', 0),
                                percentage=item.get('percentage', 0.0)
                            ) 
                            for item in word_freq_data
                        ]
                    else:
                        # Create WordFrequency instances from the list of dictionaries
                        word_freq_data = existing_doc.get('word_frequencies', [])
                        response.word_frequencies = [
                            WordFrequency(
                                word=item.get('word', ''),
                                count=item.get('count', 0),
                                percentage=item.get('percentage', 0.0)
                            ) 
                            for item in word_freq_data
                        ]
                        
                    if isinstance(existing_doc.get('text_mining_metrics'), str):
                        # Parse the JSON string to a dictionary
                        tm_data = json.loads(existing_doc.get('text_mining_metrics', '{}'))
                        # Create a TextMiningResults instance from the dictionary
                        response.text_mining = TextMiningResults(
                            word_count=tm_data.get('word_count', 0),
                            avg_word_length=tm_data.get('avg_word_length', 0.0),
                            sentence_count=tm_data.get('sentence_count', 0),
                            avg_sentence_length=tm_data.get('avg_sentence_length', 0.0),
                            readability_score=tm_data.get('readability_score', 0.0),
                            readability_interpretation=tm_data.get('readability_interpretation', ''),
                            unique_word_ratio=tm_data.get('unique_word_ratio', 0.0),
                            capital_letter_freq=tm_data.get('capital_letter_freq', 0.0),
                            punctuation_density=tm_data.get('punctuation_density', 0.0),
                            question_frequency=tm_data.get('question_frequency', 0.0),
                            paragraph_count=tm_data.get('paragraph_count', 0),
                            common_word_percentage=tm_data.get('common_word_percentage', 0.0)
                        )
                    else:
                        # Create a TextMiningResults instance from the dictionary
                        tm_data = existing_doc.get('text_mining_metrics', {})
                        response.text_mining = TextMiningResults(
                            word_count=tm_data.get('word_count', 0),
                            avg_word_length=tm_data.get('avg_word_length', 0.0),
                            sentence_count=tm_data.get('sentence_count', 0),
                            avg_sentence_length=tm_data.get('avg_sentence_length', 0.0),
                            readability_score=tm_data.get('readability_score', 0.0),
                            readability_interpretation=tm_data.get('readability_interpretation', ''),
                            unique_word_ratio=tm_data.get('unique_word_ratio', 0.0),
                            capital_letter_freq=tm_data.get('capital_letter_freq', 0.0),
                            punctuation_density=tm_data.get('punctuation_density', 0.0),
                            question_frequency=tm_data.get('question_frequency', 0.0),
                            paragraph_count=tm_data.get('paragraph_count', 0),
                            common_word_percentage=tm_data.get('common_word_percentage', 0.0)
                        )
                except Exception as e:
                    logger.warning(f"Error parsing JSON fields from existing document: {e}")
                    response.word_frequencies = []
                    response.text_mining = TextMiningResults(
                        word_count=0,
                        avg_word_length=0.0,
                        sentence_count=0,
                        avg_sentence_length=0.0,
                        readability_score=0.0,
                        readability_interpretation="Error parsing data",
                        unique_word_ratio=0.0,
                        capital_letter_freq=0.0,
                        punctuation_density=0.0,
                        question_frequency=0.0,
                        paragraph_count=0,
                        common_word_percentage=0.0
                    )
                
                response.company_name = existing_doc.get('company_name', '')
                response.logo_url = existing_doc.get('logo_url', get_default_logo_url(request.url))
                
                # CRITICAL: Ensure success is False for existing documents
                response.success = False
                
                # Final check to ensure success is False for existing documents
                if is_existing_document:
                    response.success = False
                    
                return response
        except Exception as e:
            logger.warning(f"Error checking for existing document: {e}")
            # Continue with processing if the check fails
        
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
        extraction_result = await extract_text_from_url(pp_url, "pp")
        
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
                
        # Extract company name and logo URL right after content extraction
        # This way we can include the company name in the summary process
        parsed_url = urlparse(request.url)
        domain = parsed_url.netloc
        
        # Extract company name and try to get the logo URL
        company_name = ""
        logo_url = get_default_logo_url(request.url)
        try:
            # Create a request that includes the URL and any logo URL from the request
            request_info = CompanyInfoRequest(url=request.url)
            
            # Add the logo URL if it's available in the request
            if hasattr(request, 'logo_url') and request.logo_url:
                request_info.logo_url = request.logo_url
                
            # Send the request to get_company_info function directly
            company_info_response = await get_company_info(request_info)
            
            if company_info_response.success:
                company_name = company_info_response.company_name
                logo_url = company_info_response.logo_url
                logger.info(f"Successfully extracted company info: {company_name}, {logo_url}")
            else:
                # Fall back to domain extraction if company info extraction failed
                company_name = extract_company_name_from_domain(domain)
                logger.info(f"Company info extraction failed, using domain-based name: {company_name}")
                
                # Check if we have a logo URL
                if company_info_response.logo_url and company_info_response.logo_url != get_default_logo_url(request.url):
                    logo_url = company_info_response.logo_url
                else:
                    # Try to get a logo from Google's favicon service
                    try:
                        logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                        # Test if logo exists with a head request
                        response_head = requests.head(logo_url, timeout=5)
                        if response_head.status_code != 200:
                            logo_url = get_default_logo_url(request.url)
                    except Exception as e:
                        logger.warning(f"Failed to get logo from favicon service for {domain}: {e}")
                        logo_url = get_default_logo_url(request.url)
        except Exception as e:
            logger.warning(f"Error extracting company info: {e}")
            # Fall back to simple domain-based extraction
            company_name = extract_company_name_from_domain(domain)
            logger.info(f"Exception in company info extraction, using domain-based name: {company_name}")
            logo_url = get_default_logo_url(request.url)
        
        # Final safety check - ensure we have a company name
        if not company_name or company_name.strip() == "":
            # Try direct domain extraction as a last resort
            try:
                # Parse the URL directly
                parsed_url = urlparse(request.url)
                domain = parsed_url.netloc
                
                # If domain is empty, try to add a protocol and parse again
                if not domain:
                    if not request.url.startswith(('http://', 'https://')):
                        fixed_url = 'https://' + request.url
                        parsed_url = urlparse(fixed_url)
                        domain = parsed_url.netloc
                
                if domain:
                    company_name = extract_company_name_from_domain(domain)
                    logger.info(f"Final fallback: Using domain {domain} to extract company name: {company_name}")
                else:
                    # Use parts of the URL if domain extraction failed
                    parts = request.url.split('/')
                    if len(parts) > 0:
                        company_name = parts[0].capitalize()
                        logger.info(f"Final fallback: Using URL part {parts[0]} as company name")
                    else:
                        company_name = request.url.capitalize()
                        logger.info(f"Final fallback: Using entire URL as company name")
            except Exception as e:
                logger.error(f"Error in final company name extraction: {e}")
                company_name = request.url.capitalize()
                logger.info(f"Emergency fallback: Using URL as company name")
        
        # Now create a modified summary request that includes the company name
        # This is done by modifying the pp_url to include context
        summary_context_url = f"{pp_url}?company={company_name}"
        
        # Perform analysis in parallel with the company name context
        analysis = await perform_parallel_analysis(summary_context_url, extracted_text, "pp")
        
        # Set response fields from analysis results regardless of success
        response.one_sentence_summary = analysis.get('one_sentence_summary', "Analysis failed")
        response.hundred_word_summary = analysis.get('hundred_word_summary', "Analysis failed")
        response.word_frequencies = analysis.get('word_frequencies', [])
        response.text_mining = analysis.get('text_mining', TextMiningResults(
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
        ))
        response.company_name = company_name
        response.logo_url = logo_url
        
        # Check if all analyses were successful before attempting to save
        all_analyses_successful = (
            analysis.get('one_sentence_summary') and analysis.get('hundred_word_summary') and
            analysis.get('word_frequencies') and analysis.get('text_mining') and
            not analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('one_sentence_summary', "").startswith("Error generating summary:") and
            not analysis.get('hundred_word_summary', "").startswith("Error generating summary:") and
            not analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") and
            not analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")
        )
        
        if all_analyses_successful:
            logger.info("All analyses successful. Proceeding to save document.")
            response.success = True
            response.message = "Successfully crawled and analyzed privacy policy."
            try:
                document_id = await save_document_to_db(
                    original_url=request.url, # The user's requested URL
                    retrieved_url=pp_url,    # The actual URL the PP was found at
                    document_type="pp",
                    document_content=extracted_text,
                    analysis={
                        "one_sentence_summary": response.one_sentence_summary,
                        "hundred_word_summary": response.hundred_word_summary,
                        "word_frequencies": response.word_frequencies,
                        "text_mining": response.text_mining,
                        "company_name": company_name,
                        "logo_url": logo_url
                    },
                    user_email=getattr(request, 'user_email', None)
                )
                response.document_id = document_id
            except Exception as e:
                logger.error(f"Error saving document to database: {e}")
                # If saving failed but we have valid analyses, set success=false
                # This indicates a new document was not created, even with valid data
                response.success = False
                response.message = "Successfully crawled and analyzed, but failed to save document."
        else:
            logger.warning("One or more analyses failed, but returning available data.")
            # Even if some analyses failed, if we have useful data, consider it partially successful
            has_useful_data = (
                (analysis.get('one_sentence_summary') or analysis.get('hundred_word_summary')) or
                analysis.get('word_frequencies') or
                analysis.get('text_mining')
            )
            
            if has_useful_data:
                # Even with useful data, if it wasn't saved, success should be false
                response.success = False
                
                # Check if summaries failed and provide a more specific message
                if (analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") or 
                    analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") or
                    analysis.get('one_sentence_summary', "").startswith("Error generating summary:") or
                    analysis.get('hundred_word_summary', "").startswith("Error generating summary:")):
                    logger.warning(f"Summary generation failed when reanalyzing document {request.document_id}. One-sentence: '{analysis.get('one_sentence_summary', '')[:100]}...', Hundred-word: '{analysis.get('hundred_word_summary', '')[:100]}...'")
                    response.message = "Document analysis incomplete: Summary generation failed. Document not updated."
                elif (analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") or
                      analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")):
                    logger.warning(f"Document NOT saved to database because text is too short for summarization. Text length: {len(extracted_text)}")
                    response.message = "Document not updated: Text is too short for meaningful analysis. A minimum of 200 characters is required."
                else:
                    response.message = "Partial success: some analyses completed, returning available data but not saved."
            else:
                response.success = False
                response.message = "Failed to analyze content properly, no useful data available."
            
            # Ensure document_id is None if not saved
            response.document_id = None

        # Log extracted company name and logo URL (moved here to always log)
        logger.info(f"Extracted company name: {company_name}, logo URL: {logo_url}")

        # Final check before returning to ensure success is False for existing documents
        if is_existing_document or response.message == "Document already exists in database.":
            response.success = False
            
        return response
    except Exception as e:
        # Log traceback for crawl_pp errors
        logger.error(f"Error processing Privacy Policy crawl request: {e}", exc_info=True)
        response.success = False
        response.message = f"Error processing request: {str(e)}"
        response.one_sentence_summary = "Error"
        response.hundred_word_summary = "Error"
        response.word_frequencies = []
        response.text_mining = TextMiningResults(
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
        )
        response.company_name = "Error"
        response.logo_url = get_default_logo_url(request.url)
        response.document_id = None
        return response

async def extract_text_from_url(url: str, document_type: str) -> Optional[Tuple[str, str]]:
    """
    Extracts plain text content from a URL.
    
    Args:
        url: URL to extract content from
        document_type: Type of document ("tos" or "pp")
        
    Returns:
        Tuple of (extracted_text, error_message) or None if complete failure
    """
    logger.info(f"Extracting text from URL: {url} (Type: {document_type})")
    
    # Create extract request
    extract_request = ExtractRequest(url=url, document_type=document_type)
    
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
                
                # Check if the content appears to be binary data
                if is_likely_binary_content(response.text):
                    logger.warning(f"Content from {url} appears to be binary data.")
                    
                    # We'll keep the binary content but sanitize it before storing
                    # This allows the summaries and analyses to still be saved
                    logger.info(f"Successfully extracted content from URL: {url} (binary content detected)")
                    return (response.text, "Binary content detected")
                    
                # Normal successful extraction
                logger.info(f"Successfully extracted text from URL: {url} using method: {response.method_used}")
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

async def generate_one_sentence_summary(text: str, url: str = None, document_type: str = "tos") -> str:
    """
    Generate a one-sentence summary of the provided text.
    
    Args:
        text: The text to summarize
        url: Optional URL for context
        document_type: Type of document ("tos" or "pp")
        
    Returns:
        A one-sentence summary of the text
    """
    logger.info(f"Generating one-sentence summary for {document_type}")
    
    if not text or len(text.strip()) < 100:
        logger.warning("Text is too short for summarization")
        return "Text too short for summarization"
    
    try:
        # Create a summary request
        summary_request = SummaryRequest(
            text=text,
            document_type=document_type,  # Use the provided document type
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
                return f"Summary generation returned empty result for one-sentence summary. Status: {summary_response.success}, Message: {summary_response.message}"
        else:
            error_msg = summary_response.message if summary_response else "Unknown error"
            logger.error(f"Failed to generate one-sentence summary: {error_msg}")
            return f"Error generating summary: {error_msg}"
    except Exception as e:
        logger.error(f"Exception during one-sentence summary generation: {str(e)}")
        return f"Error generating summary: {str(e)}"

async def generate_hundred_word_summary(text: str, url: str = None, document_type: str = "tos") -> str:
    """
    Generate a hundred-word summary of the provided text.
    
    Args:
        text: The text to summarize
        url: Optional URL for context
        document_type: Type of document ("tos" or "pp")
        
    Returns:
        A hundred-word summary of the text
    """
    logger.info(f"Generating hundred-word summary for {document_type}")
    
    if not text or len(text.strip()) < 200:
        logger.warning("Text is too short for summarization")
        return "Text too short for summarization"
    
    try:
        # Create a summary request
        summary_request = SummaryRequest(
            text=text,
            document_type=document_type,  # Use the provided document type
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
                return f"Summary generation returned empty result for hundred-word summary. Status: {summary_response.success}, Message: {summary_response.message}"
        else:
            error_msg = summary_response.message if summary_response else "Unknown error"
            logger.error(f"Failed to generate hundred-word summary: {error_msg}")
            return f"Error generating summary: {error_msg}"
    except Exception as e:
        logger.error(f"Exception during hundred-word summary generation: {str(e)}")
        return f"Error generating summary: {str(e)}"

def get_word_frequencies(text: str, max_words: int = 20):
    """
    Extract the most frequent words from the text.
    
    Args:
        text: The text to analyze.
        max_words: Maximum number of words to return.
        
    Returns:
        List of word frequency objects.
    """
    # Keywords to ignore (common in PDF/binary content)
    ignored_keywords = set([
        "obj", "endobj", "stream", "endstream", "xref", "trailer", "startxref", 
        "eof", "font", "encrypt", "filter", "decode", "width", "height", "type",
        "subtype", "name", "length", "ca", "op", "opm", "sa", "ais", "smask", 
        "gs", "extgstate", "shading", "pattern", "cs", "colorspace", "procset",
        "xobject", "imagemask", "dctdecode", "flatedecode", "runlengthdecode",
        "lzwdecode", "asciihexdecode", "asci85decode", "jbig2decode", "jpxdecode"
    ])
    
    words = text.lower().split()
    word_counts = {}
    
    for word in words:
        # Remove punctuation
        word = word.strip('.,;:!?()[]{}"\'-')
        
        # Check if the word is mostly alphanumeric
        if word and len(word) > 3 and word not in ignored_keywords:
            alphanumeric_chars = sum(1 for char in word if char.isalnum())
            if alphanumeric_chars / len(word) > 0.5:  # Require more than 50% alphanumeric characters
                word_counts[word] = word_counts.get(word, 0) + 1
    
    # Sort by frequency
    sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
    
    # Return as list of WordFrequency objects
    result = []
    for word, count in sorted_words[:max_words]:
        percentage = count / len(words) * 100 if words else 0
        result.append(WordFrequency(
            word=word,
            count=count,  # Using 'count' instead of 'frequency'
            percentage=percentage / 100  # Store as decimal (0-1) for consistency
        ))
    
    return result

def count_syllables(word: str) -> int:
    """
    Count the number of syllables in a word.
    
    This is a simple estimation based on vowel groups:
    - Each group of consecutive vowels is counted as one syllable
    - Special cases for common patterns like 'e' at the end of words
    
    Args:
        word: The word to count syllables in
        
    Returns:
        The estimated number of syllables
    """
    word = word.lower()
    
    # Remove non-alphabetic characters
    word = ''.join(c for c in word if c.isalpha())
    
    if not word:
        return 0
        
    # Count groups of vowels as syllables
    vowels = "aeiouy"
    count = 0
    prev_is_vowel = False
    
    for i, char in enumerate(word):
        is_vowel = char in vowels
        
        # Count a new syllable at the start of a vowel group
        if is_vowel and not prev_is_vowel:
            count += 1
            
        prev_is_vowel = is_vowel
    
    # Special case: words ending with 'e' often don't count as a separate syllable
    if word.endswith('e') and len(word) > 2 and word[-2] not in vowels:
        count = max(1, count - 1)  # Ensure we have at least 1 syllable
        
    # Special case: words ending with 'le' usually count as a syllable if preceded by a consonant
    elif word.endswith('le') and len(word) > 2 and word[-3] not in vowels:
        count = max(1, count)
    
    # Special case: words ending with 'es' or 'ed' often don't count as a separate syllable
    elif (word.endswith('es') or word.endswith('ed')) and len(word) > 2:
        count = max(1, count - 1)  # Ensure we have at least 1 syllable
    
    # Every word has at least one syllable
    return max(1, count)

def extract_text_mining_metrics(text: str):
    """
    Extract text mining metrics from the given text.
    
    Args:
        text: The text to analyze
        
    Returns:
        TextMiningResults instance with the extracted metrics
    """
    try:
        # Fall back to basic text analysis if the text is empty
        if not text or len(text.strip()) == 0:
            return TextMiningResults(
                word_count=0, 
                avg_word_length=0.00, 
                sentence_count=0, 
                avg_sentence_length=0.00,
                readability_score=0.00,
                readability_interpretation="Not applicable - No text to analyze",
                unique_word_ratio=0.00,
                capital_letter_freq=0.00,
                punctuation_density=0.00,
                question_frequency=0.00,
                paragraph_count=0,
                common_word_percentage=0.00
            )
        
        # Simple text mining metrics
        words = re.findall(r'\b\w+\b', text.lower())
        word_count = len(words)
        
        unique_words = set(words)
        unique_word_ratio = (len(unique_words) / word_count) * 100 if word_count else 0
        
        avg_word_length = sum(len(word) for word in words) / word_count if word_count else 0
        
        sentences = re.split(r'[.!?]+(?=\s+|$)', text)
        sentences = [s for s in sentences if s.strip()]
        sentence_count = len(sentences)
        
        avg_sentence_length = word_count / sentence_count if sentence_count else 0
        
        # Readability metrics - simplified Flesch Reading Ease
        syllable_count = sum(count_syllables(word) for word in words)
        syllables_per_word = syllable_count / word_count if word_count else 0
        
        # Calculate readability using simplified Flesch Reading Ease formula
        readability_score = 206.835 - (1.015 * avg_sentence_length) - (84.6 * syllables_per_word)
        
        # Clamp to reasonable range
        readability_score = max(0, min(100, readability_score))
        
        # Interpret readability score
        if readability_score >= 90:
            interpretation = "Very Easy: 5th-grade level"
        elif readability_score >= 80:
            interpretation = "Easy: 6th-grade level"
        elif readability_score >= 70:
            interpretation = "Fairly Easy: 7th-grade level"
        elif readability_score >= 60:
            interpretation = "Standard: 8th-9th grade level"
        elif readability_score >= 50:
            interpretation = "Fairly Difficult: 10th-12th grade level"
        elif readability_score >= 30:
            interpretation = "Difficult: College level"
        else:
            interpretation = "Very Difficult: College graduate level"
        
        # Count paragraphs by splitting on double newlines
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p for p in paragraphs if p.strip()]
        paragraph_count = len(paragraphs)
        
        # Calculate additional metrics
        capital_letter_freq = sum(1 for word in re.findall(r'\b\w+\b', text) if word and word[0].isupper()) / word_count * 100 if word_count else 0
        punctuation_freq = sum(1 for char in text if char in '.,;:!?()[]{}"\'-') / len(text) * 100 if text else 0
        question_freq = text.count('?') / sentence_count * 100 if sentence_count else 0
        
        # Common word percentage (new field required by TextMiningResults)
        common_words = ['the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her', 'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there', 'their', 'what', 'so', 'up', 'out', 'if', 'about', 'who', 'get', 'which', 'go', 'me', 'when']
        common_word_count = sum(1 for word in words if word.lower() in common_words)
        common_word_percentage = common_word_count / word_count * 100 if word_count else 0
        
        # Return metrics as a TextMiningResults object
        return TextMiningResults(
            word_count=word_count,
            avg_word_length=round(avg_word_length, 2),
            sentence_count=sentence_count,
            avg_sentence_length=round(avg_sentence_length, 2),
            readability_score=round(readability_score, 2),
            readability_interpretation=interpretation,
            unique_word_ratio=round(unique_word_ratio, 2),
            capital_letter_freq=round(capital_letter_freq, 2),
            punctuation_density=round(punctuation_freq, 2),
            question_frequency=round(question_freq, 2),
            paragraph_count=paragraph_count,
            common_word_percentage=round(common_word_percentage, 2)
        )
    except Exception as e:
        logger.error(f"Error extracting text mining metrics: {str(e)}")
        return TextMiningResults(
            word_count=0,
            avg_word_length=0.00,
            sentence_count=0,
            avg_sentence_length=0.00,
            readability_score=0.00,
            readability_interpretation="Error in analysis",
            unique_word_ratio=0.00,
            capital_letter_freq=0.00,
            punctuation_density=0.00,
            question_frequency=0.00,
            paragraph_count=0,
            common_word_percentage=0.00
        )

def safe_model_dump(model):
    """
    Safely convert model instances to dictionaries for Firestore.
    
    Args:
        model: The model instance to convert.
        
    Returns:
        Dictionary representation of the model.
    """
    try:
        if hasattr(model, 'dict') and callable(getattr(model, 'dict')):
            # Pydantic model or similar with dict() method
            return model.dict()
        elif hasattr(model, '__dict__'):
            # Regular Python class with __dict__ attribute
            return model.__dict__
        elif isinstance(model, dict):
            # Already a dictionary
            return model
        else:
            # Try to serialize as is
            return model
    except Exception as e:
        logger.error(f"Error serializing model: {e}")
        return {"error": "Serialization failed"}

def is_likely_binary_content(text: str) -> bool:
    """
    Detect if the content appears to be binary/encoded rather than readable text.
    
    Args:
        text: The text to check
        
    Returns:
        True if the text appears to be binary/encoded, False otherwise
    """
    if not text:
        return False
        
    # Calculate the ratio of non-printable characters
    printable_chars = set(string.printable)
    if len(text) == 0:
        return False
        
    # Sample the text if it's very long
    sample_text = text[:10000] if len(text) > 10000 else text
    
    # Count non-printable characters
    non_printable_count = sum(1 for char in sample_text if char not in printable_chars)
    non_printable_ratio = non_printable_count / len(sample_text)
    
    # If more than 20% are non-printable, likely binary
    if non_printable_ratio > 0.20:
        return True
        
    # Check for common binary patterns (high frequency of control characters)
    control_char_count = sum(1 for char in sample_text if ord(char) < 32 and char not in '\t\n\r')  # Tab, LF, CR are allowed
    control_char_ratio = control_char_count / len(sample_text)
    
    if control_char_ratio > 0.05:
        return True
        
    return False

def sanitize_text_for_db(text: str) -> str:
    """
    Sanitize text before saving to database:
    - Removes null bytes and other invalid UTF-8 characters
    - Returns sanitized text or empty string if full content is binary
    
    Args:
        text: Original text that might contain invalid characters
        
    Returns:
        Sanitized text safe for database storage
    """
    if not text:
        return ""
    
    # If content appears to be primarily binary, return empty string with note
    if is_likely_binary_content(text):
        logger.warning("Content appears to be binary data; replacing with empty text")
        return "[Binary content removed - not displayable as text]"
        
    # Remove null bytes which cause PostgreSQL UTF-8 encoding errors
    sanitized = text.replace('\x00', '')
    
    # Replace other control characters except common whitespace
    control_chars = ''.join(chr(i) for i in range(32) if i not in [9, 10, 13])  # Tab, LF, CR are allowed
    for char in control_chars:
        sanitized = sanitized.replace(char, '')
    
    try:
        # Try to encode and decode to catch other UTF-8 issues
        sanitized.encode('utf-8').decode('utf-8')
    except UnicodeError:
        # If encoding fails, try a more aggressive approach - keep only ASCII printable
        sanitized = ''.join(c for c in sanitized if c in string.printable)
    
    return sanitized 

@router.post("/reanalyze-tos", response_model=ReanalyzeTosResponse)
async def reanalyze_tos(request: ReanalyzeTosRequest) -> ReanalyzeTosResponse:
    """
    Reanalyze an existing Terms of Service document.
    
    This endpoint:
    1. Retrieves the existing document using the provided ID
    2. Extracts text from the provided URL or the stored URL if not provided
    3. Performs text analysis (summaries, word frequencies, text mining)
    4. Updates the document with the new analysis
    
    Args:
        request: Contains the document_id to reanalyze and optional new URL
        
    Returns:
        Response with reanalysis results and status
    """
    # Initialize response
    response = ReanalyzeTosResponse(
        url="",
        success=False,
        message="Failed to reanalyze Terms of Service"
    )
    
    try:
        logger.info(f"Reanalyzing ToS document with ID: {request.document_id}")
        
        # Get the document from Firestore
        from app.crud.document import document_crud
        document = await document_crud.get(request.document_id)
        
        if not document:
            response.message = f"Document with ID {request.document_id} not found"
            return response
        
        # Ensure the document is a ToS document
        if document.get('document_type') != 'tos':
            response.message = f"Document with ID {request.document_id} is not a Terms of Service document"
            return response
        
        # Get the original and retrieved URLs from the document
        original_url = document.get('url', '')
        stored_retrieved_url = document.get('retrieved_url')
        
        # Set the response URL to the original document URL
        response.url = original_url
        
        # Determine which URL to use for extraction
        if request.url:
            # Use the provided URL
            extraction_url = request.url
            logger.info(f"Using provided URL for extraction: {extraction_url}")
            response.tos_url = extraction_url
        elif stored_retrieved_url:
            # Use the stored retrieved URL
            extraction_url = stored_retrieved_url
            logger.info(f"Using stored URL for extraction: {extraction_url}")
            response.tos_url = extraction_url
        else:
            response.message = "Document doesn't have a retrieved URL and no new URL was provided"
            return response
        
        # Extract text from the URL
        logger.info(f"Extracting text from URL: {extraction_url}")
        try:
            text_result = await extract_text_from_url(extraction_url, 'tos')
            
            if not text_result:
                response.message = f"Failed to extract text from URL: {extraction_url}"
                return response
            
            extracted_text, extraction_error = text_result
            
            # If there was an error but we still got some text, log it
            if extraction_error:
                logger.warning(f"Extraction warning: {extraction_error}")
        except Exception as extract_err:
            logger.error(f"Error extracting text from URL {extraction_url}: {str(extract_err)}")
            response.message = f"Error extracting text: {str(extract_err)}"
            return response
        
        # Check if we have text to analyze
        if not extracted_text or is_likely_binary_content(extracted_text):
            response.message = "Failed to extract valid text from the document URL"
            return response
        
        # Perform parallel analysis
        logger.info("Starting analysis of extracted text")
        analysis = await perform_parallel_analysis(extraction_url, extracted_text, 'tos')
        
        # Set company info
        company_name = document.get('company_name', '')
        logo_url = document.get('logo_url', get_default_logo_url(original_url))
        
        analysis['company_name'] = company_name
        analysis['logo_url'] = logo_url
        
        # Check if all analyses were successful
        all_analyses_successful = (
            analysis.get('one_sentence_summary') and analysis.get('hundred_word_summary') and
            analysis.get('word_frequencies') and analysis.get('text_mining') and
            not analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('one_sentence_summary', "").startswith("Error generating summary:") and
            not analysis.get('hundred_word_summary', "").startswith("Error generating summary:") and
            not analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") and
            not analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")
        )
        
        # Prepare serialized data for Firestore
        serializable_word_freqs = []
        if 'word_frequencies' in analysis and isinstance(analysis['word_frequencies'], list):
            for item in analysis['word_frequencies']:
                if isinstance(item, WordFrequency):
                    serializable_word_freqs.append({
                        "word": item.word,
                        "count": item.count,
                        "percentage": item.percentage
                    })
                elif isinstance(item, dict):
                    serializable_word_freqs.append(item)
                else:
                    serializable_word_freqs.append(item.__dict__ if hasattr(item, '__dict__') else {"word": str(item), "count": 0, "percentage": 0})
        
        # Serialize TextMiningResults
        serializable_text_mining = {}
        if 'text_mining' in analysis:
            if isinstance(analysis['text_mining'], TextMiningResults):
                serializable_text_mining = analysis['text_mining'].__dict__
            elif isinstance(analysis['text_mining'], dict):
                serializable_text_mining = analysis['text_mining']
            else:
                serializable_text_mining = {"error": "Could not convert text mining metrics"}
                logger.warning(f"Could not convert text mining metrics to dict: {type(analysis['text_mining'])}")
        
        # Prepare update data
        update_data = {
            "one_sentence_summary": analysis.get('one_sentence_summary', ''),
            "hundred_word_summary": analysis.get('hundred_word_summary', ''),
            "word_frequencies": serializable_word_freqs,
            "text_mining_metrics": serializable_text_mining
        }
        
        # If a new URL was provided, update the retrieved_url in the document
        if request.url:
            update_data["retrieved_url"] = request.url
        
        if all_analyses_successful:
            # Update the document with new analysis
            logger.info(f"Updating document {request.document_id} with new analysis")
            updated_doc = await document_crud.update_document_analysis(request.document_id, update_data)
            
            if updated_doc:
                response.success = True
                response.message = "Document successfully reanalyzed and updated"
                response.document_id = request.document_id
                response.company_name = company_name
                response.logo_url = logo_url
                response.one_sentence_summary = analysis.get('one_sentence_summary', '')
                response.hundred_word_summary = analysis.get('hundred_word_summary', '')
                
                # Convert to response models
                if 'word_frequencies' in analysis and analysis['word_frequencies']:
                    response.word_frequencies = analysis['word_frequencies']
                
                if 'text_mining' in analysis and analysis['text_mining']:
                    response.text_mining = analysis['text_mining']
                
                response.views = document.get('views', 0)
            else:
                response.message = "Failed to update document with new analysis"
        else:
            response.message = "Document analysis incomplete: Some analyses failed"
            logger.warning(f"Incomplete analysis when reanalyzing document {request.document_id}")
            
            # Still include any partial analysis in the response
            response.one_sentence_summary = analysis.get('one_sentence_summary', '')
            response.hundred_word_summary = analysis.get('hundred_word_summary', '')
            
            if 'word_frequencies' in analysis and analysis['word_frequencies']:
                response.word_frequencies = analysis['word_frequencies']
            
            if 'text_mining' in analysis and analysis['text_mining']:
                response.text_mining = analysis['text_mining']
                
            # Check if summaries failed and provide a more specific message
            if (analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") or 
                analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") or
                analysis.get('one_sentence_summary', "").startswith("Error generating summary:") or
                analysis.get('hundred_word_summary', "").startswith("Error generating summary:")):
                logger.warning(f"Summary generation failed when reanalyzing document {request.document_id}. One-sentence: '{analysis.get('one_sentence_summary', '')[:100]}...', Hundred-word: '{analysis.get('hundred_word_summary', '')[:100]}...'")
                response.message = "Document analysis incomplete: Summary generation failed. Document not updated."
            elif (analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") or
                  analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")):
                logger.warning(f"Document NOT saved to database because text is too short for summarization. Text length: {len(extracted_text)}")
                response.message = "Document not updated: Text is too short for meaningful analysis. A minimum of 200 characters is required."
    
    except Exception as e:
        logger.error(f"Error reanalyzing ToS document: {e}", exc_info=True)
        response.message = f"Error reanalyzing document: {str(e)}"
    
    return response

@router.post("/reanalyze-pp", response_model=ReanalyzePrivacyResponse)
async def reanalyze_pp(request: ReanalyzePrivacyRequest) -> ReanalyzePrivacyResponse:
    """
    Reanalyze an existing Privacy Policy document.
    
    This endpoint:
    1. Retrieves the existing document using the provided ID
    2. Extracts text from the provided URL or the stored URL if not provided
    3. Performs text analysis (summaries, word frequencies, text mining)
    4. Updates the document with the new analysis
    
    Args:
        request: Contains the document_id to reanalyze and optional new URL
        
    Returns:
        Response with reanalysis results and status
    """
    # Initialize response
    response = ReanalyzePrivacyResponse(
        url="",
        success=False,
        message="Failed to reanalyze Privacy Policy"
    )
    
    try:
        logger.info(f"Reanalyzing Privacy Policy document with ID: {request.document_id}")
        
        # Get the document from Firestore
        from app.crud.document import document_crud
        document = await document_crud.get(request.document_id)
        
        if not document:
            response.message = f"Document with ID {request.document_id} not found"
            return response
        
        # Ensure the document is a PP document
        if document.get('document_type') != 'pp':
            response.message = f"Document with ID {request.document_id} is not a Privacy Policy document"
            return response
        
        # Get the original and retrieved URLs from the document
        original_url = document.get('url', '')
        stored_retrieved_url = document.get('retrieved_url')
        
        # Set the response URL to the original document URL
        response.url = original_url
        
        # Determine which URL to use for extraction
        if request.url:
            # Use the provided URL
            extraction_url = request.url
            logger.info(f"Using provided URL for extraction: {extraction_url}")
            response.pp_url = extraction_url
        elif stored_retrieved_url:
            # Use the stored retrieved URL
            extraction_url = stored_retrieved_url
            logger.info(f"Using stored URL for extraction: {extraction_url}")
            response.pp_url = extraction_url
        else:
            response.message = "Document doesn't have a retrieved URL and no new URL was provided"
            return response
        
        # Extract text from the URL
        logger.info(f"Extracting text from URL: {extraction_url}")
        try:
            text_result = await extract_text_from_url(extraction_url, 'pp')
            
            if not text_result:
                response.message = f"Failed to extract text from URL: {extraction_url}"
                return response
            
            extracted_text, extraction_error = text_result
            
            # If there was an error but we still got some text, log it
            if extraction_error:
                logger.warning(f"Extraction warning: {extraction_error}")
        except Exception as extract_err:
            logger.error(f"Error extracting text from URL {extraction_url}: {str(extract_err)}")
            response.message = f"Error extracting text: {str(extract_err)}"
            return response
        
        # Check if we have text to analyze
        if not extracted_text or is_likely_binary_content(extracted_text):
            response.message = "Failed to extract valid text from the document URL"
            return response
        
        # Perform parallel analysis
        logger.info("Starting analysis of extracted text")
        analysis = await perform_parallel_analysis(extraction_url, extracted_text, 'pp')
        
        # Set company info
        company_name = document.get('company_name', '')
        logo_url = document.get('logo_url', get_default_logo_url(original_url))
        
        analysis['company_name'] = company_name
        analysis['logo_url'] = logo_url
        
        # Check if all analyses were successful
        all_analyses_successful = (
            analysis.get('one_sentence_summary') and analysis.get('hundred_word_summary') and
            analysis.get('word_frequencies') and analysis.get('text_mining') and
            not analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") and
            not analysis.get('one_sentence_summary', "").startswith("Error generating summary:") and
            not analysis.get('hundred_word_summary', "").startswith("Error generating summary:") and
            not analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") and
            not analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")
        )
        
        # Prepare serialized data for Firestore
        serializable_word_freqs = []
        if 'word_frequencies' in analysis and isinstance(analysis['word_frequencies'], list):
            for item in analysis['word_frequencies']:
                if isinstance(item, WordFrequency):
                    serializable_word_freqs.append({
                        "word": item.word,
                        "count": item.count,
                        "percentage": item.percentage
                    })
                elif isinstance(item, dict):
                    serializable_word_freqs.append(item)
                else:
                    serializable_word_freqs.append(item.__dict__ if hasattr(item, '__dict__') else {"word": str(item), "count": 0, "percentage": 0})
        
        # Serialize TextMiningResults
        serializable_text_mining = {}
        if 'text_mining' in analysis:
            if isinstance(analysis['text_mining'], TextMiningResults):
                serializable_text_mining = analysis['text_mining'].__dict__
            elif isinstance(analysis['text_mining'], dict):
                serializable_text_mining = analysis['text_mining']
            else:
                serializable_text_mining = {"error": "Could not convert text mining metrics"}
                logger.warning(f"Could not convert text mining metrics to dict: {type(analysis['text_mining'])}")
        
        # Prepare update data
        update_data = {
            "one_sentence_summary": analysis.get('one_sentence_summary', ''),
            "hundred_word_summary": analysis.get('hundred_word_summary', ''),
            "word_frequencies": serializable_word_freqs,
            "text_mining_metrics": serializable_text_mining
        }
        
        # If a new URL was provided, update the retrieved_url in the document
        if request.url:
            update_data["retrieved_url"] = request.url
        
        if all_analyses_successful:
            # Update the document with new analysis
            logger.info(f"Updating document {request.document_id} with new analysis")
            updated_doc = await document_crud.update_document_analysis(request.document_id, update_data)
            
            if updated_doc:
                response.success = True
                response.message = "Document successfully reanalyzed and updated"
                response.document_id = request.document_id
                response.company_name = company_name
                response.logo_url = logo_url
                response.one_sentence_summary = analysis.get('one_sentence_summary', '')
                response.hundred_word_summary = analysis.get('hundred_word_summary', '')
                
                # Convert to response models
                if 'word_frequencies' in analysis and analysis['word_frequencies']:
                    response.word_frequencies = analysis['word_frequencies']
                
                if 'text_mining' in analysis and analysis['text_mining']:
                    response.text_mining = analysis['text_mining']
                
                response.views = document.get('views', 0)
            else:
                response.message = "Failed to update document with new analysis"
        else:
            response.message = "Document analysis incomplete: Some analyses failed"
            logger.warning(f"Incomplete analysis when reanalyzing document {request.document_id}")
            
            # Still include any partial analysis in the response
            response.one_sentence_summary = analysis.get('one_sentence_summary', '')
            response.hundred_word_summary = analysis.get('hundred_word_summary', '')
            
            if 'word_frequencies' in analysis and analysis['word_frequencies']:
                response.word_frequencies = analysis['word_frequencies']
            
            if 'text_mining' in analysis and analysis['text_mining']:
                response.text_mining = analysis['text_mining']
                
            # Check if summaries failed and provide a more specific message
            if (analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") or 
                analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") or
                analysis.get('one_sentence_summary', "").startswith("Error generating summary:") or
                analysis.get('hundred_word_summary', "").startswith("Error generating summary:")):
                logger.warning(f"Summary generation failed when reanalyzing document {request.document_id}. One-sentence: '{analysis.get('one_sentence_summary', '')[:100]}...', Hundred-word: '{analysis.get('hundred_word_summary', '')[:100]}...'")
                response.message = "Document analysis incomplete: Summary generation failed. Document not updated."
            elif (analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") or
                  analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")):
                logger.warning(f"Document NOT saved to database because text is too short for summarization. Text length: {len(extracted_text)}")
                response.message = "Document not updated: Text is too short for meaningful analysis. A minimum of 200 characters is required."
    
    except Exception as e:
        logger.error(f"Error reanalyzing Privacy Policy document: {e}", exc_info=True)
        response.message = f"Error reanalyzing document: {str(e)}"
    
    return response 

class URLSubmissionRequest(BaseModel):
    """Request model for URL submission."""
    url: str
    document_type: Literal["tos", "pp"]
    document_url: Optional[str] = None  # User can provide specific ToS/PP URL if base URL fails
    user_email: str = Field(..., description="User's email for tracking submissions")

class URLSubmissionResponse(BaseModel):
    """Response model for URL submission."""
    id: str  # Submission ID
    url: str
    document_type: Literal["tos", "pp"]
    status: str  # "initialized", "processing", "analyzing", "success", "failed"
    document_id: Optional[str] = None
    error_message: Optional[str] = None  # Used for error info or "Document already exists" messages
    created_at: datetime
    updated_at: datetime

@router.post("/submissions", response_model=URLSubmissionResponse)
async def submit_url(
    request: URLSubmissionRequest,
    api_key: str = Depends(get_api_key)
):
    """
    Submit a URL for crawling. This creates a submission record and initiates 
    the crawling process asynchronously.
    
    The submission state is tracked through several stages:
    - initialized: Submission created
    - processing: Crawling in progress
    - analyzing: Text analysis in progress
    - success: Crawling completed successfully
    - failed: Crawling failed
    
    If a submission with the same base URL has failed, new submissions are locked
    unless an alternate URL is provided.
    
    - **url**: Base URL of the website
    - **document_type**: Type of document to crawl ("tos" or "pp")
    - **document_url**: Optional specific URL for the document (used if base URL crawl fails)
    - **user_email**: User's email for tracking submissions
    
    Returns:
    - Submission details including ID and status
    """
    url = request.url
    document_type = request.document_type
    user_email = request.user_email
    
    # Try to normalize the URL for more effective matching
    normalized_url = url.lower().strip()
    if not normalized_url.startswith(('http://', 'https://')):
        normalized_url = 'https://' + normalized_url
    
    try:
        # Extract the domain for domain-based matching
        parsed_url = urlparse(normalized_url)
        domain = parsed_url.netloc
        if not domain and '/' in normalized_url:
            domain = normalized_url.split('/')[0]
    except:
        domain = None  # If parsing fails, we'll just use the original URL
    
    # Check if we already have a document for this URL and type
    from app.crud.document import document_crud
    
    # First try exact URL match
    existing_doc = await document_crud.get_by_url_and_type(url, document_type)
    
    # If not found, try with normalized URL
    if not existing_doc and normalized_url != url:
        existing_doc = await document_crud.get_by_url_and_type(normalized_url, document_type)
    
    # If still not found, try domain-based search
    if not existing_doc and domain:
        try:
            # This would need to be implemented in your document_crud
            similar_docs = await document_crud.find_documents_by_domain(domain, document_type, limit=1)
            if similar_docs and len(similar_docs) > 0:
                existing_doc = similar_docs[0]
                logger.info(f"Found document with same domain: {domain}")
        except Exception as e:
            logger.warning(f"Error in domain-based document search: {e}")
    
    if existing_doc:
        # Document already exists, create a completed submission
        submission = await submission_crud.create_submission(
            user_email=user_email,  # Use email as user identifier directly without user collection reference
            document_id=existing_doc['id'],
            requested_url=url,
            document_type=document_type,
            status="success"
        )
        
        if submission:
            return URLSubmissionResponse(
                id=submission['id'],
                url=url,
                document_type=document_type,
                status="success",
                document_id=existing_doc['id'],
                created_at=submission['created_at'],
                updated_at=submission['updated_at'],
                error_message="Document already exists in database. Redirecting to existing document."
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to create submission record")
    
    # Check for duplicate submissions for this URL and document type across all users
    try:
        dup_query = db.collection('submissions')\
            .where("requested_url", "==", url)\
            .where("document_type", "==", document_type)
        dup_submissions = list(dup_query.stream())
        for dup in dup_submissions:
            dup_data = dup.to_dict()
            if dup_data.get('status') and dup_data['status'] != 'failed':
                return URLSubmissionResponse(
                    id=dup.id,
                    url=dup_data.get('requested_url', url),
                    document_type=dup_data.get('document_type', document_type),
                    status=dup_data.get('status'),
                    document_id=dup_data.get('document_id'),
                    created_at=dup_data.get('created_at'),
                    updated_at=dup_data.get('updated_at'),
                    error_message="Duplicate submission exists. Returning existing submission."
                )
    except Exception as e:
        logger.warning(f"Error checking duplicate submissions: {e}")

    # Check if there's a failed submission for this URL
    try:
        # Query for failed submissions with this URL
        query = db.collection('submissions').where("requested_url", "==", url).where("status", "==", "failed").limit(1)
        failed_submissions = list(query.stream())
        
        if failed_submissions and not request.document_url:
            # There's a failed submission for this URL and no alternate URL provided
            raise HTTPException(
                status_code=400, 
                detail="This URL has failed crawling before. Please provide a direct document_url for the document."
            )
    except Exception as e:
        logger.error(f"Error checking failed submissions: {str(e)}")
        # Continue with submission even if check fails
    
    # Create a new submission with initialized status - using email directly without user references
    submission = await submission_crud.create_submission(
        user_email=user_email,  # Use email directly as identifier without user collection reference
        requested_url=url,
        document_type=document_type,
        status="initialized"
    )
    
    if not submission:
        raise HTTPException(status_code=500, detail="Failed to create submission record")
    
    # Start background task to process the submission
    asyncio.create_task(process_submission(submission['id'], request))
    
    return URLSubmissionResponse(
        id=submission['id'],
        url=url,
        document_type=document_type,
        status="initialized",
        created_at=submission['created_at'],
        updated_at=submission['updated_at']
    )

async def process_submission(submission_id: str, request: URLSubmissionRequest):
    """Process a submission in the background."""
    logger.info(f"Processing submission {submission_id}")
    
    # Update submission to processing status
    submission = await submission_crud.update_submission_status(
        id=submission_id,
        status="processing"
    )
    
    if not submission:
        logger.error(f"Failed to update submission {submission_id} to processing status")
        return
    
    try:
        # Determine which URL to use
        url = request.url
        document_type = request.document_type
        
        # If document URL is provided, use it instead of finding ToS/PP URL
        extraction_url = request.document_url if request.document_url else url
        
        # Check if it's a retry (document URL was provided)
        is_retry = bool(request.document_url)
        
        if is_retry:
            logger.info(f"Processing direct URL submission for {submission_id} with URL: {extraction_url}")
            
            # Extract content from the URL directly
            extraction_result = await extract_text_from_url(extraction_url, document_type)
            
            if not extraction_result:
                logger.warning(f"Failed to extract content from {extraction_url}")
                await submission_crud.update_submission_status(
                    id=submission_id,
                    status="failed",
                    error_message="Failed to extract content from URL"
                )
                return
                
            extracted_text, extraction_error = extraction_result
            
            if not extracted_text:
                logger.warning(f"Empty content extracted from {extraction_url}: {extraction_error}")
                await submission_crud.update_submission_status(
                    id=submission_id,
                    status="failed",
                    error_message=f"Failed to extract content: {extraction_error}"
                )
                return
            
            # Check for very short content which might indicate a bot verification page
            if len(extracted_text.split()) < 100:
                logger.warning(f"Extracted content from {extraction_url} is suspiciously short ({len(extracted_text.split())} words)")
                if any(phrase in extracted_text.lower() for phrase in ["verify", "security", "check", "browser"]):
                    logger.warning(f"Short extracted content appears to be a verification page: {extraction_url}")
                    await submission_crud.update_submission_status(
                        id=submission_id,
                        status="failed",
                        error_message="Bot verification page detected - unable to access actual content"
                    )
                    return
            
            # Extract company info
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            company_name = extract_company_name_from_domain(domain)
            logo_url = get_default_logo_url(url)
            
            try:
                # Create a request for company info
                request_info = CompanyInfoRequest(url=url)
                company_info_response = await get_company_info(request_info)
                
                if company_info_response.success:
                    company_name = company_info_response.company_name
                    logo_url = company_info_response.logo_url
                    logger.info(f"Successfully extracted company info: {company_name}, {logo_url}")
            except Exception as e:
                logger.warning(f"Error extracting company info: {e}")
                # Continue with domain-based company name
            
            # Update submission to analyzing status
            await submission_crud.update_submission_status(
                id=submission_id,
                status="analyzing"
            )
            
            # Perform analysis
            analysis = await perform_parallel_analysis(extraction_url, extracted_text, document_type)
            
            # Add company info to analysis results
            analysis['company_name'] = company_name
            analysis['logo_url'] = logo_url
            
            # Check if all analyses were successful
            all_analyses_successful = (
                analysis.get('one_sentence_summary') and analysis.get('hundred_word_summary') and
                analysis.get('word_frequencies') and analysis.get('text_mining') and
                not analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") and
                not analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") and
                not analysis.get('one_sentence_summary', "").startswith("Error generating summary:") and
                not analysis.get('hundred_word_summary', "").startswith("Error generating summary:") and
                not analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") and
                not analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")
            )
            
            if all_analyses_successful:
                logger.info(f"All analyses successful for submission {submission_id}")
                
                try:
                    # Prepare analysis data - match the structure used in crawl-tos and crawl-pp
                    document_analysis = {
                        "one_sentence_summary": analysis.get('one_sentence_summary', ''),
                        "hundred_word_summary": analysis.get('hundred_word_summary', ''),
                        "word_frequencies": analysis.get('word_frequencies', []),
                        "text_mining": analysis.get('text_mining', {}),
                        "company_name": company_name,
                        "logo_url": logo_url
                    }
                    
                    # Save document to database
                    document_id = await save_document_to_db(
                        original_url=url,
                        retrieved_url=extraction_url,
                        document_type=document_type,
                        document_content=extracted_text,
                        analysis=document_analysis,
                        user_email=request.user_email
                    )
                    
                    # Update submission to success
                    await submission_crud.update_submission_status(
                        id=submission_id,
                        status="success",
                        document_id=document_id
                    )
                except Exception as e:
                    logger.error(f"Error saving document for submission {submission_id}: {e}")
                    await submission_crud.update_submission_status(
                        id=submission_id,
                        status="failed",
                        error_message=f"Error saving document: {str(e)}"
                    )
            else:
                # If analyses failed, update submission status
                logger.warning(f"Some analyses failed for submission {submission_id}")
                has_useful_data = (
                    (analysis.get('one_sentence_summary') or analysis.get('hundred_word_summary')) or
                    analysis.get('word_frequencies') or
                    analysis.get('text_mining')
                )
                
                if has_useful_data:
                    error_message = "Partial analysis completed but document not saved"
                    
                    # Check what specifically failed
                    if (analysis.get('one_sentence_summary', "").startswith("Summary generation returned empty result") or 
                        analysis.get('hundred_word_summary', "").startswith("Summary generation returned empty result") or
                        analysis.get('one_sentence_summary', "").startswith("Error generating summary:") or
                        analysis.get('hundred_word_summary', "").startswith("Error generating summary:")):
                        error_message = "Summary generation failed"
                    elif (analysis.get('one_sentence_summary', "").startswith("Text too short for summarization") or
                          analysis.get('hundred_word_summary', "").startswith("Text too short for summarization")):
                        error_message = "Text too short for summarization"
                    
                    await submission_crud.update_submission_status(
                        id=submission_id,
                        status="failed",
                        error_message=error_message
                    )
                else:
                    await submission_crud.update_submission_status(
                        id=submission_id,
                        status="failed",
                        error_message="Failed to analyze content properly"
                    )
            
            return
        
        # Standard submission flow (not a retry)
        document_id = None
        if document_type == "tos":
            # Create ToS request
            tos_request = CrawlTosRequest(url=url)
            
            # Call crawl-tos endpoint
            response = await crawl_tos(tos_request)
            
            if response.success:
                document_id = response.document_id
                
                # Update submission to success
                await submission_crud.update_submission_status(
                    id=submission_id,
                    status="success",
                    document_id=document_id
                )
            else:
                # Update submission to failed
                await submission_crud.update_submission_status(
                    id=submission_id,
                    status="failed",
                    error_message=response.message
                )
        else:  # document_type == "pp"
            # Create Privacy Policy request
            pp_request = CrawlPrivacyRequest(url=url)
            
            # Call crawl-pp endpoint
            response = await crawl_pp(pp_request)
            
            if response.success:
                document_id = response.document_id
                
                # Update submission to success
                await submission_crud.update_submission_status(
                    id=submission_id,
                    status="success",
                    document_id=document_id
                )
            else:
                # Update submission to failed
                await submission_crud.update_submission_status(
                    id=submission_id,
                    status="failed",
                    error_message=response.message
                )
    except Exception as e:
        logger.error(f"Error processing submission {submission_id}: {str(e)}")
        
        # Update submission to failed
        await submission_crud.update_submission_status(
            id=submission_id,
            status="failed",
            error_message=str(e)
        )

@router.get("/submissions/{submission_id}", response_model=URLSubmissionResponse)
async def get_submission(
    submission_id: str,
    user_email: str = Query(..., description="User's email to validate ownership"),
    role: Optional[str] = Query(None, description="User role - 'admin' gives additional permissions"),
    api_key: str = Depends(get_api_key)
):
    """
    Get the status of a URL submission.
    
    - **submission_id**: ID of the submission to retrieve
    - **user_email**: User's email to validate submission ownership
    - **role**: Optional. If set to 'admin', allows viewing any submission without permission checks
    
    Returns:
    - Submission details including ID, status, and document_id if available
    """
    try:
        submission = await submission_crud.get(submission_id)
        
        if not submission:
            logger.info(f"Submission with ID {submission_id} not found")
            # Instead of raising a 404 error, return a response with a clear message
            # with empty/placeholder values
            return URLSubmissionResponse(
                id=submission_id,
                url="",
                document_type="tos",  # Default type
                status="not_found",   # Special status to indicate not found
                document_id=None,
                error_message="No submission found with this ID",
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        
        # If role is admin, allow access to any submission without checking email
        is_admin = role == "admin"
        
        # Verify that the submission belongs to the user using email directly (no user collection needed)
        # Skip this check for admins
        if not is_admin and submission.get('user_email') != user_email:
            return URLSubmissionResponse(
                id=submission_id,
                url="",
                document_type="tos",  # Default type 
                status="access_denied",   # Special status to indicate permission issue
                document_id=None,
                error_message="You do not have permission to access this submission",
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        
        return URLSubmissionResponse(
            id=submission['id'],
            url=submission['requested_url'],
            document_type=submission['document_type'],
            status=submission['status'],
            document_id=submission.get('document_id'),
            error_message=submission.get('error_message'),
            created_at=submission['created_at'],
            updated_at=submission['updated_at']
        )
    except Exception as e:
        logger.error(f"Error fetching submission {submission_id}: {str(e)}")
        
        error_message = f"Failed to fetch submission: {str(e)}"
        error_status = "fetch_error"
        
        # Check for specific error types and provide clearer messages
        try:
            error_str = str(e).lower()
            if "connection" in error_str or "network" in error_str:
                error_message = "Database connection error"
                error_status = "connection_error"
            elif "timeout" in error_str:
                error_message = "Database operation timed out"
                error_status = "timeout_error"
            elif "permission" in error_str:
                error_message = "Permission denied accessing submission"
                error_status = "permission_error"
        except Exception as inner_e:
            # If error analysis itself fails, just use the original error message
            logger.warning(f"Error analyzing exception message: {str(inner_e)}")
        
        return URLSubmissionResponse(
            id=submission_id,
            url="",
            document_type="tos",  # Default type 
            status=error_status,  # Status indicating specific error
            document_id=None,
            error_message=error_message,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

class PaginatedSubmissionsResponse(BaseModel):
    """Response model for paginated submissions listing."""
    items: List[URLSubmissionResponse]
    total: int
    page: int
    size: int
    pages: int
    error_status: bool = False  # Flag to indicate if there was an error
    error_message: Optional[str] = None  # Error message if any

@router.get("/submissions", response_model=PaginatedSubmissionsResponse)
async def list_submissions(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(6, description="Items per page - allowed values: 6, 9, 12, 15"),
    user_email: str = Query(..., description="User's email to filter submissions"),
    sort_order: str = Query("desc", description="Sort order - 'asc' for older to newest, 'desc' for newest to oldest"),
    search_url: Optional[str] = Query(None, description="Search by base URL"),
    api_key: str = Depends(get_api_key)
):
    """
    List all submissions for a specific user with pagination and sorting options.
    
    - **page**: Page number (starts at 1)
    - **size**: Number of items per page (allowed values: 6, 9, 12, 15, default: 6)
    - **user_email**: User's email to filter submissions
    - **sort_order**: Sort order - 'asc' for older to newest, 'desc' for newest to oldest (default: desc)
    - **search_url**: Optional filter to search by base URL
    
    Returns:
    - Paginated list of submissions with their statuses
    """
    # Validate size parameter
    allowed_sizes = [6, 9, 12, 15]
    if size not in allowed_sizes:
        size = 6  # Default to 6 if invalid size provided
    
    # Validate sort_order
    if sort_order not in ["asc", "desc"]:
        sort_order = "desc"  # Default to newest first if invalid sort_order provided
    
    try:
        # Set up base query - directly using user_email for filtering without user collection reference
        query = db.collection('submissions').where("user_email", "==", user_email)
        
        # Add search filter for URL if provided
        if search_url:
            # Convert to lowercase and add wildcard search
            search_term = search_url.lower()
            # Use firebase's array-contains or similar field comparison
            query = query.where("requested_url", ">=", search_term)
            query = query.where("requested_url", "<=", search_term + "\uf8ff")
        
        # Get total count with filters applied
        total_query = query
        total_count = len(list(total_query.stream()))
        
        # Calculate offset
        offset = (page - 1) * size
        
        # Apply sorting
        direction = firestore.Query.ASCENDING if sort_order == "asc" else firestore.Query.DESCENDING
        query = query.order_by("created_at", direction=direction).offset(offset).limit(size)
        submissions_docs = list(query.stream())
        
        # Format response
        submissions = []
        for doc in submissions_docs:
            data = doc.to_dict()
            data['id'] = doc.id
            
            submissions.append(URLSubmissionResponse(
                id=data['id'],
                url=data['requested_url'],
                document_type=data['document_type'],
                status=data['status'],
                document_id=data.get('document_id'),
                error_message=data.get('error_message'),
                created_at=data['created_at'],
                updated_at=data['updated_at']
            ))
        
        # Calculate total pages
        total_pages = (total_count + size - 1) // size if total_count > 0 else 1  # Ensure at least 1 page even when empty
        
        # If no submissions found, instead of raising an error, return empty results with message
        if len(submissions) == 0 and page == 1:
            logger.info(f"No submissions found for user {user_email}")
            # Return empty results with appropriate message
            return PaginatedSubmissionsResponse(
                items=[],
                total=0,
                page=page,
                size=size,
                pages=total_pages,
                error_status=False,  # Not an error, just empty results
                error_message="No submissions found"
            )
        
        return PaginatedSubmissionsResponse(
            items=submissions,
            total=total_count,
            page=page,
            size=size,
            pages=total_pages
        )
    except Exception as e:
        logger.error(f"Error retrieving submissions: {str(e)}")
        # Instead of raising an HTTPException, return a response with error status
        error_message = f"Failed to retrieve submissions: {str(e)}"
        
        # Check for common error types and provide more specific messages
        try:
            error_str = str(e).lower()
            if "permission denied" in error_str:
                error_message = "Permission denied accessing submissions database"
            elif "not found" in error_str:
                error_message = "Submissions collection not found"
            elif "timeout" in error_str:
                error_message = "Database connection timed out"
            elif "unavailable" in error_str or "connection" in error_str:
                error_message = "Database service unavailable"
        except Exception as inner_e:
            # If error analysis itself fails, just use the original error message
            logger.warning(f"Error analyzing exception message: {str(inner_e)}")
        
        # Return an empty result set with error status
        return PaginatedSubmissionsResponse(
            items=[],
            total=0,
            page=page,
            size=size,
            pages=1,
            error_status=True,
            error_message=error_message
        )

class RetrySubmissionRequest(BaseModel):
    """Request model for retrying a failed submission."""
    document_url: str = Field(..., description="The direct URL to the document (ToS or Privacy Policy)")
    user_email: str = Field(..., description="User's email for tracking submissions")

@router.post("/submissions/{submission_id}/retry", response_model=URLSubmissionResponse)
async def retry_submission(
    submission_id: str,
    request: RetrySubmissionRequest,
    api_key: str = Depends(get_api_key)
):
    """
    Retry a failed submission with a direct document URL.
    
    This bypasses URL discovery and directly processes the provided URL.
    The submission will go through these states:
    - initialized
    - processing
    - analyzing
    - success/failed
    
    - **submission_id**: ID of the failed submission to retry
    - **document_url**: The direct URL to the document (ToS or Privacy Policy)
    - **user_email**: User's email for tracking submissions
    
    Returns:
    - Updated submission details
    """
    try:
        # Get the submission
        submission = await submission_crud.get(submission_id)
        
        if not submission:
            logger.info(f"Submission with ID {submission_id} not found for retry")
            # Instead of raising a 404 error, return a response with a clear message
            return URLSubmissionResponse(
                id=submission_id,
                url=request.document_url,
                document_type="tos",  # Default type
                status="not_found",   # Special status to indicate not found
                document_id=None,
                error_message="No submission found with this ID for retry",
                created_at=datetime.now(),
                updated_at=datetime.now()
            )
        
        # Check if the submission is in failed status
        if submission['status'] != "failed":
            logger.info(f"Attempted to retry submission {submission_id} with status {submission['status']}")
            return URLSubmissionResponse(
                id=submission_id,
                url=submission['requested_url'],
                document_type=submission['document_type'],
                status="invalid_retry",   # Special status to indicate invalid retry
                document_id=submission.get('document_id'),
                error_message=f"Only failed submissions can be retried. Current status: {submission['status']}",
                created_at=submission['created_at'],
                updated_at=submission['updated_at']
            )
        
        # Verify submission has the required fields
        if 'requested_url' not in submission or 'document_type' not in submission:
            logger.warning(f"Submission {submission_id} missing required fields for retry")
            return URLSubmissionResponse(
                id=submission_id,
                url=request.document_url,
                document_type=submission.get('document_type', 'tos'),
                status="invalid_submission",
                document_id=None,
                error_message="Submission record is missing required fields (URL or document type)",
                created_at=submission['created_at'],
                updated_at=submission['updated_at']
            )
        
        # Update submission to initialized status
        updated_submission = await submission_crud.update_submission_status(
            id=submission_id,
            status="initialized",
            error_message=None  # Clear previous error message
        )
        
        if not updated_submission:
            logger.error(f"Failed to update submission {submission_id} status for retry")
            return URLSubmissionResponse(
                id=submission_id,
                url=submission['requested_url'],
                document_type=submission['document_type'],
                status="update_failed",
                document_id=submission.get('document_id'),
                error_message="Failed to update submission for retry",
                created_at=submission['created_at'],
                updated_at=datetime.now()
            )
        
        # Create a submission request with the document URL and data from the original submission
        retry_request = URLSubmissionRequest(
            url=submission['requested_url'],
            document_type=submission['document_type'],
            document_url=request.document_url,
            user_email=request.user_email
        )
        
        # Start background task to process the submission
        try:
            asyncio.create_task(process_submission(submission_id, retry_request))
        except Exception as task_e:
            logger.error(f"Error starting retry task for submission {submission_id}: {str(task_e)}")
            # Update submission back to failed status if process task couldn't be started
            await submission_crud.update_submission_status(
                id=submission_id,
                status="failed",
                error_message=f"Failed to start retry task: {str(task_e)}"
            )
            
            # Return informative error
            return URLSubmissionResponse(
                id=submission_id,
                url=submission['requested_url'],
                document_type=submission['document_type'],
                status="retry_failed",
                document_id=submission.get('document_id'),
                error_message=f"Failed to start retry processing: {str(task_e)}",
                created_at=submission['created_at'],
                updated_at=datetime.now()
            )
        
        return URLSubmissionResponse(
            id=updated_submission['id'],
            url=updated_submission['requested_url'],
            document_type=updated_submission['document_type'],
            status="initialized",
            document_id=updated_submission.get('document_id'),
            error_message=updated_submission.get('error_message'),
            created_at=updated_submission['created_at'],
            updated_at=updated_submission['updated_at']
        )
    except Exception as e:
        logger.error(f"Error processing retry for submission {submission_id}: {str(e)}")
        
        error_message = f"Failed to process retry: {str(e)}"
        error_status = "retry_error"
        
        # Check for specific error types and provide clearer messages
        try:
            error_str = str(e).lower()
            if "connection" in error_str or "network" in error_str:
                error_message = "Database connection error while processing retry"
                error_status = "connection_error"
            elif "timeout" in error_str:
                error_message = "Operation timed out while processing retry"
                error_status = "timeout_error"
            elif "permission" in error_str:
                error_message = "Permission denied while processing retry"
                error_status = "permission_error"
        except Exception as inner_e:
            # If error analysis itself fails, just use the original error message
            logger.warning(f"Error analyzing exception message: {str(inner_e)}")
        
        return URLSubmissionResponse(
            id=submission_id,
            url=request.document_url,
            document_type="tos",  # Default type 
            status=error_status,
            document_id=None,
            error_message=error_message,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

@router.get("/search-submissions", response_model=PaginatedSubmissionsResponse)
async def search_submissions(
    query: str = Query(..., description="Search query for URLs"),
    user_email: str = Query(..., description="User's email to filter submissions"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(6, description="Items per page - allowed values: 6, 9, 12, 15"),
    sort_order: str = Query("desc", description="Sort order - 'asc' for older to newest, 'desc' for newest to oldest"),
    document_type: Optional[str] = Query(None, description="Filter by document type (tos or pp)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    api_key: str = Depends(get_api_key)
):
    """
    Search for submissions by URL using Typesense with full-text search capabilities.
    Only returns submissions that belong to the user identified by their email.
    
    - **query**: Search query (searches in URLs)
    - **user_email**: User's email to filter submissions
    - **page**: Page number (starts at 1)
    - **size**: Number of items per page (allowed values: 6, 9, 12, 15, default: 6)
    - **sort_order**: Sort order - 'asc' for older to newest, 'desc' for newest to oldest (default: desc)
    - **document_type**: Optional filter by document type (tos or pp)
    - **status**: Optional filter by status
    
    Returns:
    - Paginated list of matching submissions (only for the current user)
    """
    # Validate size parameter
    allowed_sizes = [6, 9, 12, 15]
    if size not in allowed_sizes:
        size = 6  # Default to 6 if invalid size provided
    
    # Validate sort_order
    if sort_order not in ["asc", "desc"]:
        sort_order = "desc"  # Default to newest first if invalid sort_order provided
    
    try:
        # Get Typesense client
        client = get_typesense_client()
        if not client:
            logger.warning("Typesense client not available. Falling back to Firebase query.")
            # Fallback to Firebase query (reuse list_submissions logic)
            return await list_submissions(
                page=page,
                size=size,
                user_email=user_email,
                sort_order=sort_order,
                search_url=query,
                api_key=api_key
            )
        
        # Build search parameters for Typesense with strict user filtering using email directly
        search_parameters = {
            'q': query,
            'query_by': 'url',  # Search in the URL field
            'page': page,
            'per_page': size,
            'prefix': True,     # Enable prefix searching
            'infix': 'always',  # Enable infix searching for substrings
            'filter_by': f"user_email:={user_email}"  # Strict equality to ensure only user's submissions
        }
        
        # Add document_type filter if provided
        if document_type:
            search_parameters['filter_by'] += f" && document_type:={document_type}"
        
        # Add status filter if provided
        if status:
            search_parameters['filter_by'] += f" && status:={status}"
        
        # Add sorting
        sort_field = "created_at"  # Default sort field
        search_parameters['sort_by'] = f"{sort_field}:{sort_order}"
        
        # Perform search
        SUBMISSIONS_COLLECTION = "submissions"
        search_results = client.collections[SUBMISSIONS_COLLECTION].documents.search(search_parameters)
        
        # Process results - double check that submissions belong to user by email
        submissions = []
        for hit in search_results['hits']:
            data = hit['document']
            
            # Security check - only include if user_email matches
            if data.get('user_email') == user_email:
                # Convert timestamps back to datetime for the response model
                created_at = datetime.fromtimestamp(data.get('created_at', 0))
                updated_at = datetime.fromtimestamp(data.get('updated_at', 0))
                
                # Determine document_id; fallback to Firebase if missing in Typesense
                doc_id = data.get('document_id')
                if not doc_id:
                    firebase_submission = await submission_crud.get(data['id'])
                    doc_id = firebase_submission.get('document_id') if firebase_submission else None
                submissions.append(URLSubmissionResponse(
                    id=data['id'],
                    url=data.get('url', ''),
                    document_type=data.get('document_type', ''),
                    status=data.get('status', ''),
                    document_id=doc_id,
                    error_message=data.get('error_message'),
                    created_at=created_at,
                    updated_at=updated_at
                ))
        
        # Get pagination info from results
        total_count = len(submissions)  # Count after filtering
        total_pages = (total_count + size - 1) // size if total_count > 0 else 1
        
        # Apply pagination in memory if needed
        start_idx = (page - 1) * size
        end_idx = start_idx + size
        paginated_submissions = submissions[start_idx:end_idx]
        
        # If no submissions found, instead of failing, return empty results with message
        if total_count == 0 and page == 1:
            logger.info(f"No submissions found for search query '{query}' by user {user_email}")
            # Return empty results with appropriate message
            return PaginatedSubmissionsResponse(
                items=[],
                total=0,
                page=page,
                size=size,
                pages=1,
                error_status=False,  # Not an error, just empty results
                error_message="No submissions found"
            )
        
        return PaginatedSubmissionsResponse(
            items=paginated_submissions,
            total=total_count,
            page=page,
            size=size,
            pages=total_pages
        )
        
    except Exception as e:
        logger.error(f"Error searching submissions: {str(e)}")
        # Fall back to regular listing if search fails
        logger.info("Falling back to Firebase query after Typesense search failure.")
        
        error_details = str(e)
        error_type = "search_engine_error"
        
        # Check specific error types for better categorization
        try:
            error_str = error_details.lower()
            if "connection" in error_str:
                error_type = "connection_error"
            elif "timeout" in error_str:
                error_type = "timeout_error"
            elif "permission" in error_str:
                error_type = "permission_error"
        except Exception as inner_e:
            # If error analysis itself fails, just use the original error type
            logger.warning(f"Error analyzing exception message: {str(inner_e)}")
        
        try:
            # Try to fall back to regular listing
            fallback_response = await list_submissions(
                page=page,
                size=size,
                user_email=user_email,
                sort_order=sort_order,
                search_url=query,
                api_key=api_key
            )
            
            # Keep the original fallback response but add error info about search failure
            fallback_response.error_status = True
            fallback_response.error_message = f"Search engine error: {error_details}. Using fallback database query."
            
            return fallback_response
        except Exception as fallback_e:
            # If fallback also fails, return empty results with both errors
            logger.error(f"Fallback query also failed: {str(fallback_e)}")
            
            return PaginatedSubmissionsResponse(
                items=[],
                total=0,
                page=page,
                size=size,
                pages=1,
                error_status=True,
                error_message=f"Search failed ({error_type}): {error_details}. Fallback also failed: {str(fallback_e)}"
            )

@router.post("/admin/sync-submissions-to-typesense", response_model=Dict[str, Any])
async def sync_submissions_to_typesense(
    api_key: str = Depends(get_api_key),
    limit: int = Query(1000, description="Maximum number of submissions to sync")
):
    """
    Admin endpoint to sync all submissions to Typesense.
    
    This indexes all submissions from Firebase to Typesense for fast searching.
    Only use when necessary, such as after initial setup or data recovery.
    The sync process ensures user_email field is correctly indexed for proper user-specific searching.
    
    - **limit**: Maximum number of submissions to sync (default: 1000)
    
    Returns:
    - Statistics about the sync operation
    """
    try:
        # Get Typesense client
        client = get_typesense_client()
        if not client:
            return {
                "success": False,
                "message": "Typesense client not available",
                "indexed": 0,
                "failed": 0,
                "total": 0
            }
        
        # Create simple schema for submissions collection if it doesn't exist
        SUBMISSIONS_COLLECTION = "submissions"
        try:
            # Check if submissions collection exists
            client.collections[SUBMISSIONS_COLLECTION].retrieve()
        except Exception:
            # Create submissions collection if it doesn't exist
            submissions_schema = {
                'name': SUBMISSIONS_COLLECTION,
                'fields': [
                    {'name': 'id', 'type': 'string'},
                    {'name': 'url', 'type': 'string', 'infix': True},
                    {'name': 'document_type', 'type': 'string', 'facet': True},
                    {'name': 'status', 'type': 'string', 'facet': True},
                    {'name': 'user_email', 'type': 'string', 'facet': True},
                    {'name': 'updated_at', 'type': 'int64', 'sort': True},
                    {'name': 'created_at', 'type': 'int64', 'sort': True}
                ],
                'default_sorting_field': 'created_at'
            }
            client.collections.create(submissions_schema)
            logger.info(f"Created new Typesense collection: {SUBMISSIONS_COLLECTION}")
        
        # Get all submissions from Firebase
        query = db.collection('submissions').limit(limit)
        submissions_docs = list(query.stream())
        
        # Statistics counters
        total = len(submissions_docs)
        indexed = 0
        failed = 0
        missing_user_email = 0
        
        # Process each submission
        for doc in submissions_docs:
            data = doc.to_dict()
            data['id'] = doc.id
            
            try:
                # Check if user_email field exists - critical for user-specific search
                if 'user_email' not in data or not data['user_email']:
                    logger.warning(f"Submission {doc.id} missing user_email field. Attempting to fix.")
                    # Try to find user_id field instead and convert it
                    if 'user_id' in data and data['user_id']:
                        data['user_email'] = data['user_id']
                        logger.info(f"Fixed submission {doc.id} by using user_id as user_email")
                        # Update the record in Firebase as well
                        doc.reference.update({"user_email": data['user_id']})
                    else:
                        missing_user_email += 1
                        logger.error(f"Submission {doc.id} has no user identifier. Skipping.")
                        failed += 1
                        continue
                
                # Index submission in Typesense
                result = await submission_crud._index_in_typesense(data)
                if result:
                    indexed += 1
                else:
                    failed += 1
            except Exception as e:
                logger.error(f"Error indexing submission {doc.id} in Typesense: {str(e)}")
                failed += 1
        
        return {
            "success": True,
            "message": f"Synced {indexed} of {total} submissions to Typesense",
            "indexed": indexed,
            "failed": failed,
            "missing_user_email": missing_user_email,
            "total": total
        }
    except Exception as e:
        logger.error(f"Error syncing submissions to Typesense: {str(e)}")
        return {
            "success": False,
            "message": f"Error syncing submissions: {str(e)}",
            "indexed": 0,
            "failed": 0,
            "total": 0
        }

@router.get("/admin/search-all-submissions", response_model=PaginatedSubmissionsResponse)
async def admin_search_all_submissions(
    query: str = Query("", description="Search query for URLs (empty to list all)"),
    user_email: Optional[str] = Query(None, description="Optional: Filter by user email"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(6, description="Items per page - allowed values: 6, 9, 12, 15"),
    sort_order: str = Query("desc", description="Sort order - 'asc' for older to newest, 'desc' for newest to oldest"),
    document_type: Optional[str] = Query(None, description="Filter by document type (tos or pp)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    role: str = Query(..., description="User role - must be 'admin' to access this endpoint"),
    api_key: str = Depends(get_api_key)
):
    """
    Admin-only endpoint to search ALL submissions across users.
    
    This endpoint allows administrators to search and browse submissions from all users.
    The user must have the 'admin' role to access this endpoint.
    
    - **query**: Search query (searches in URLs, empty to list all)
    - **user_email**: Optional filter by specific user email
    - **page**: Page number (starts at 1)
    - **size**: Number of items per page (allowed values: 6, 9, 12, 15)
    - **sort_order**: Sort order - 'asc' for older to newest, 'desc' for newest to oldest (default: desc)
    - **document_type**: Optional filter by document type (tos or pp)
    - **status**: Optional filter by status
    - **role**: User role - must be 'admin' to access this endpoint
    
    Returns:
    - Paginated list of matching submissions from all users
    """
    # Verify admin role
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required to access this endpoint")
    
    # Validate size parameter
    allowed_sizes = [6, 9, 12, 15]
    if size not in allowed_sizes:
        size = 6  # Default to 6 if invalid size provided
    
    # Validate sort_order
    if sort_order not in ["asc", "desc"]:
        sort_order = "desc"  # Default to newest first if invalid sort_order provided
    
    try:
        # Get Typesense client
        client = get_typesense_client()
        if not client:
            logger.warning("Typesense client not available. Falling back to Firebase query.")
            # Fallback to Firebase query with no user filtering
            return await admin_list_all_submissions(
                page=page,
                size=size,
                user_email=user_email,
                sort_order=sort_order,
                search_url=query,
                document_type=document_type,
                status=status,
                api_key=api_key
            )
        
        # Build search parameters for Typesense with no user filtering by default
        search_parameters = {
            'q': query if query else "*",  # Use * to match all when query is empty
            'query_by': 'url',  # Search in the URL field
            'page': page,
            'per_page': size,
            'prefix': True,     # Enable prefix searching
            'infix': 'always'   # Enable infix searching for substrings
        }
        
        # Add filter conditions
        filter_conditions = []
        
        # Add user_email filter if provided
        if user_email:
            filter_conditions.append(f"user_email:={user_email}")
        
        # Add document_type filter if provided
        if document_type:
            filter_conditions.append(f"document_type:={document_type}")
        
        # Add status filter if provided
        if status:
            filter_conditions.append(f"status:={status}")
        
        # Combine filter conditions if any
        if filter_conditions:
            search_parameters['filter_by'] = " && ".join(filter_conditions)
        
        # Add sorting
        sort_field = "created_at"  # Default sort field
        search_parameters['sort_by'] = f"{sort_field}:{sort_order}"
        
        # Perform search
        SUBMISSIONS_COLLECTION = "submissions"
        search_results = client.collections[SUBMISSIONS_COLLECTION].documents.search(search_parameters)
        
        # Process results
        submissions = []
        for hit in search_results['hits']:
            data = hit['document']
            
            # Convert timestamps back to datetime for the response model
            created_at = datetime.fromtimestamp(data.get('created_at', 0))
            updated_at = datetime.fromtimestamp(data.get('updated_at', 0))
            
            # Determine document_id; fallback to Firebase if missing in Typesense
            doc_id = data.get('document_id')
            if not doc_id:
                firebase_submission = await submission_crud.get(data['id'])
                doc_id = firebase_submission.get('document_id') if firebase_submission else None
            submissions.append(URLSubmissionResponse(
                id=data['id'],
                url=data.get('url', ''),
                document_type=data.get('document_type', ''),
                status=data.get('status', ''),
                document_id=doc_id,
                error_message=data.get('error_message'),
                created_at=created_at,
                updated_at=updated_at
            ))
        
        # Get pagination info from results
        total_count = search_results.get('found', len(submissions))
        total_pages = (total_count + size - 1) // size if total_count > 0 else 1
        
        return PaginatedSubmissionsResponse(
            items=submissions,
            total=total_count,
            page=page,
            size=size,
            pages=total_pages
        )
        
    except Exception as e:
        logger.error(f"Error searching all submissions: {str(e)}")
        # Fall back to regular listing if search fails
        logger.info("Falling back to Firebase query after Typesense search failure.")
        return await admin_list_all_submissions(
            page=page,
            size=size,
            user_email=user_email,
            sort_order=sort_order,
            search_url=query,
            document_type=document_type,
            status=status,
            api_key=api_key
        )

async def admin_list_all_submissions(
    page: int = 1,
    size: int = 6,  # Changed from 10 to 6 to match main function
    user_email: Optional[str] = None,
    sort_order: str = "desc",
    search_url: Optional[str] = None,
    document_type: Optional[str] = None,
    status: Optional[str] = None,
    api_key: str = None
):
    """Helper function to list all submissions for admin with Firebase fallback"""
    try:
        # Start with base query - no user filtering
        query = db.collection('submissions')
        
        # Apply filters if provided
        if user_email:
            query = query.where("user_email", "==", user_email)
            
        if document_type:
            query = query.where("document_type", "==", document_type)
            
        if status:
            query = query.where("status", "==", status)
        
        # Add search filter for URL if provided
        if search_url:
            # Convert to lowercase and add wildcard search
            search_term = search_url.lower()
            # Use firebase's range queries for prefix matching
            query = query.where("requested_url", ">=", search_term)
            query = query.where("requested_url", "<=", search_term + "\uf8ff")
        
        # Get total count with filters applied
        total_query = query
        total_count = len(list(total_query.stream()))
        
        # Calculate offset
        offset = (page - 1) * size
        
        # Apply sorting
        direction = firestore.Query.ASCENDING if sort_order == "asc" else firestore.Query.DESCENDING
        query = query.order_by("created_at", direction=direction).offset(offset).limit(size)
        submissions_docs = list(query.stream())
        
        # Format response
        submissions = []
        for doc in submissions_docs:
            data = doc.to_dict()
            data['id'] = doc.id
            
            submissions.append(URLSubmissionResponse(
                id=data['id'],
                url=data.get('requested_url', ''),
                document_type=data.get('document_type', ''),
                status=data.get('status', ''),
                document_id=data.get('document_id'),
                error_message=data.get('error_message'),
                created_at=data.get('created_at'),
                updated_at=data.get('updated_at')
            ))
        
        # Calculate total pages
        total_pages = (total_count + size - 1) // size  # Ceiling division
        
        return PaginatedSubmissionsResponse(
            items=submissions,
            total=total_count,
            page=page,
            size=size,
            pages=total_pages
        )
    except Exception as e:
        logger.error(f"Error retrieving all submissions: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve submissions: {str(e)}")