from fastapi import APIRouter, Response, HTTPException, status, Depends, Header
import logging
import asyncio
from urllib.parse import urlparse
import requests
import random
from typing import Dict, Optional, Tuple, Any, Union
import string
import json
import re
import time
from datetime import datetime

from app.api.v1.endpoints.tos import find_tos, ToSRequest
from app.api.v1.endpoints.privacy import find_privacy_policy, PrivacyRequest
from app.api.v1.endpoints.extract import extract_text, extract_with_playwright
from app.api.v1.endpoints.summary import generate_summary
from app.api.v1.endpoints.wordfrequency import analyze_word_freq_endpoint, analyze_text_frequency
from app.api.v1.endpoints.textmining import analyze_text as analyze_text_mining, perform_text_mining
from app.api.v1.endpoints.company_info import extract_company_info, extract_company_name_from_domain, get_company_info
from app.core.config import settings
from app.core.database import (
    get_document_by_url, get_document_by_retrieved_url, create_document, increment_views
)
from app.core.firebase import db

from app.models.summary import SummaryRequest, SummaryResponse
from app.models.extract import ExtractRequest, ExtractResponse
from app.models.wordfrequency import WordFrequencyRequest, WordFrequencyResponse, WordFrequency
from app.models.textmining import TextMiningRequest, TextMiningResponse, TextMiningResults
from app.models.crawl import CrawlTosRequest, CrawlTosResponse, CrawlPrivacyRequest, CrawlPrivacyResponse
from app.models.database import DocumentCreate, SubmissionCreate
from app.models.company_info import CompanyInfoRequest

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Default logo URL
DEFAULT_LOGO_URL = "/placeholder.svg?height=48&width=48"

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
    
    # Run all of these analyses in parallel since they don't depend on each other
    # and can take a few seconds each
    
    # Get word frequencies and text mining metrics directly
    word_freqs = get_word_frequencies(extracted_text)
    text_mining = extract_text_mining_metrics(extracted_text)
    
    # Generate summaries
    one_sentence_summary = await generate_one_sentence_summary(extracted_text, doc_url)
    hundred_word_summary = await generate_hundred_word_summary(extracted_text, doc_url)
    
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
    user_id: Optional[str] = None  # Changed from UUID to str
) -> Optional[str]:  # Changed return type from UUID to str
    """
    Save document to database after crawling.
    
    Args:
        original_url: The original URL requested by the user.
        retrieved_url: The actual URL the content was retrieved from.
        document_type: Type of document (TOS or PP).
        document_content: The parsed content.
        analysis: Dictionary of analysis results.
        user_id: Optional user ID for submission tracking.
        
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
                logo_url = DEFAULT_LOGO_URL
        
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
        
        # Check if the Firebase database is available
        if db is None:
            logger.error("Firebase database not initialized")
            raise Exception("Firebase database not initialized")
        
        # Create document data for Firestore
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
            "views": 0,
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        
        # Add to Firestore documents collection
        document_ref = db.collection("documents").document()
        document_id = document_ref.id
        document_ref.set(document_data)
        
        logger.info(f"Document saved to Firestore with ID: {document_id}")
        
        # If user_id is provided, create a submission record
        if user_id:
            submission_data = {
                "user_id": user_id,
                "document_id": document_id,
                "created_at": datetime.now()
            }
            db.collection("submissions").document().set(submission_data)
            logger.info(f"Submission record created for user {user_id} and document {document_id}")
        
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
                    tos_url=existing_doc.get('retrieved_url', ''),
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
                response.logo_url = existing_doc.get('logo_url', DEFAULT_LOGO_URL)
                
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
        
        # Check if this exact tos_url has already been crawled
        try:
            existing_doc = get_document_by_retrieved_url(tos_url, "tos")
            if existing_doc:
                logger.info(f"Document for retrieved URL {tos_url} already exists in database with ID {existing_doc['id']}. Returning existing document.")
                
                # Update views count for the existing document - increment_views is async
                await increment_views(existing_doc['id'])
                
                # Create a new response object with success=False
                response = CrawlTosResponse(
                    url=request.url,
                    success=False,  # CRITICAL: Must be False for existing documents
                    message="Document already exists in database.",
                    document_id=existing_doc['id'],
                    tos_url=existing_doc.get('retrieved_url', ''),
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
                response.logo_url = existing_doc.get('logo_url', DEFAULT_LOGO_URL)
                
                # CRITICAL: Ensure success is False for existing documents
                response.success = False
                
                # Final check to ensure success is False for existing documents
                if is_existing_document:
                    response.success = False
                    
                return response
        except Exception as e:
            logger.warning(f"Error checking for existing document: {e}")
            # Continue with processing if the check fails
        
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
        logo_url = DEFAULT_LOGO_URL
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
                if company_info_response.logo_url and company_info_response.logo_url != DEFAULT_LOGO_URL:
                    logo_url = company_info_response.logo_url
                else:
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
            logger.info(f"Exception in company info extraction, using domain-based name: {company_name}")
            logo_url = DEFAULT_LOGO_URL
        
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
            analysis.get('word_frequencies') and analysis.get('text_mining')
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
                    user_id=None
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
        response.logo_url = DEFAULT_LOGO_URL
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
                    pp_url=existing_doc.get('retrieved_url', ''),
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
                response.logo_url = existing_doc.get('logo_url', DEFAULT_LOGO_URL)
                
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
        
        # Check if this exact pp_url has already been crawled
        try:
            existing_doc = get_document_by_retrieved_url(pp_url, "pp")
            if existing_doc:
                logger.info(f"Document for retrieved URL {pp_url} already exists in database with ID {existing_doc['id']}. Returning existing document.")
                
                # Update views count for the existing document - increment_views is async
                await increment_views(existing_doc['id'])
                
                # Create a new response object with success=False
                response = CrawlPrivacyResponse(
                    url=request.url,
                    success=False,  # CRITICAL: Must be False for existing documents
                    message="Document already exists in database.",
                    document_id=existing_doc['id'],
                    pp_url=existing_doc.get('retrieved_url', ''),
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
                response.logo_url = existing_doc.get('logo_url', DEFAULT_LOGO_URL)
                
                # CRITICAL: Ensure success is False for existing documents
                response.success = False
                
                # Final check to ensure success is False for existing documents
                if is_existing_document:
                    response.success = False
                    
                return response
        except Exception as e:
            logger.warning(f"Error checking for existing document: {e}")
            # Continue with processing if the check fails
        
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
        logo_url = DEFAULT_LOGO_URL
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
                if company_info_response.logo_url and company_info_response.logo_url != DEFAULT_LOGO_URL:
                    logo_url = company_info_response.logo_url
                else:
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
            logger.info(f"Exception in company info extraction, using domain-based name: {company_name}")
            logo_url = DEFAULT_LOGO_URL
        
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
            analysis.get('word_frequencies') and analysis.get('text_mining')
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
                    user_id=None
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
        response.logo_url = DEFAULT_LOGO_URL
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
    Extract the most frequent words from the text.
    
    Args:
        text: The text to analyze.
        max_words: Maximum number of words to return.
        
    Returns:
        List of word frequency objects.
    """
    # Simple implementation - more sophisticated implementation would use proper NLP
    words = text.lower().split()
    word_counts = {}
    
    for word in words:
        # Remove punctuation
        word = word.strip('.,;:!?()[]{}"\'-')
        if word and len(word) > 3:  # Skip short words
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

def extract_text_mining_metrics(text: str):
    """
    Extract text mining metrics from the text.
    
    Args:
        text: The text to analyze.
        
    Returns:
        Dictionary of text mining metrics.
    """
    try:
        # Split text into words, sentences, paragraphs
        words = text.split()
        sentences = text.split('.')
        paragraphs = text.split('\n\n')
        
        # Count number of words, sentences, paragraphs
        word_count = len(words)
        sentence_count = len(sentences)
        paragraph_count = len(paragraphs)
        
        # Average word length
        avg_word_length = sum(len(word) for word in words) / word_count if word_count else 0
        
        # Average sentence length
        avg_sentence_length = word_count / sentence_count if sentence_count else 0
        
        # Calculate readability score (simple approximation of Flesch-Kincaid)
        readability_score = 206.835 - (1.015 * avg_sentence_length) - (84.6 * avg_word_length / 5)
        readability_score = max(0, min(100, readability_score))
        
        # Interpret readability score
        if readability_score >= 90:
            interpretation = "Very Easy"
        elif readability_score >= 80:
            interpretation = "Easy"
        elif readability_score >= 70:
            interpretation = "Fairly Easy"
        elif readability_score >= 60:
            interpretation = "Standard"
        elif readability_score >= 50:
            interpretation = "Fairly Difficult"
        elif readability_score >= 30:
            interpretation = "Difficult"
        else:
            interpretation = "Very Difficult"
        
        # Unique word ratio
        unique_words = set(words)
        unique_word_ratio = len(unique_words) / word_count if word_count else 0
        
        # Other metrics
        capital_letter_freq = sum(1 for char in text if char.isupper()) / len(text) if text else 0
        punctuation_freq = sum(1 for char in text if char in '.,;:!?()[]{}"\'-') / len(text) if text else 0
        question_freq = text.count('?') / sentence_count if sentence_count else 0
        
        # Common word percentage (new field required by TextMiningResults)
        common_words = ['the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her', 'she', 'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there', 'their', 'what', 'so', 'up', 'out', 'if', 'about', 'who', 'get', 'which', 'go', 'me', 'when']
        common_word_count = sum(1 for word in words if word.lower() in common_words)
        common_word_percentage = common_word_count / word_count * 100 if word_count else 0
        
        # Return metrics as a TextMiningResults object
        return TextMiningResults(
            word_count=word_count,
            avg_word_length=avg_word_length,
            sentence_count=sentence_count,
            avg_sentence_length=avg_sentence_length,
            readability_score=readability_score,
            readability_interpretation=interpretation,
            unique_word_ratio=unique_word_ratio,
            capital_letter_freq=capital_letter_freq,
            punctuation_density=punctuation_freq,
            question_frequency=question_freq,
            paragraph_count=paragraph_count,
            common_word_percentage=common_word_percentage  # Added required field
        )
    except Exception as e:
        logger.error(f"Error extracting text mining metrics: {str(e)}")
        return TextMiningResults(
            word_count=0,
            avg_word_length=0,
            sentence_count=0,
            avg_sentence_length=0,
            readability_score=0,
            readability_interpretation="Error in analysis",
            unique_word_ratio=0,
            capital_letter_freq=0,
            punctuation_density=0,
            question_frequency=0,
            paragraph_count=0,
            common_word_percentage=0  # Added required field with default value
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
    control_char_count = sum(1 for char in sample_text if ord(char) < 32 and char not in '\t\n\r')
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