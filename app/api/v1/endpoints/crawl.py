from fastapi import APIRouter, Response, HTTPException, status, Depends
import logging
import asyncio
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlparse

from app.api.v1.endpoints.tos import find_tos
from app.api.v1.endpoints.privacy import find_privacy_policy
from app.api.v1.endpoints.extract import extract_text
from app.api.v1.endpoints.summary import generate_summary, generate_one_sentence_summary, generate_hundred_word_summary
from app.api.v1.endpoints.wordfrequency import analyze_word_freq_endpoint, get_word_frequencies
from app.api.v1.endpoints.textmining import analyze_text as analyze_text_mining, extract_text_mining_metrics

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
    doc_type: str,
    url: str,
    doc_url: str,
    one_sentence_summary: str,
    hundred_word_summary: str,
    text_mining_metrics,
    word_frequencies,
    raw_text=None,
):
    """Save document to database and create a submission record."""
    try:
        # Set default logo URL
        default_logo_url = "/placeholder.svg?height=48&width=48"
        
        # Extract company name from URL
        domain = urlparse(url).netloc
        if domain.startswith('www.'):
            domain = domain[4:]
        company_name = domain.split('.')[0].capitalize()
        
        # Initialize with default logo URL
        logo_url = default_logo_url
        
        # Try to extract a better logo URL using Google's favicon service
        try:
            logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
            logger.info(f"Using logo URL: {logo_url} for domain: {domain}")
        except Exception as e:
            logger.warning(f"Failed to generate logo URL, using default: {e}")
            logo_url = default_logo_url
        
        logger.info(f"Using company name: {company_name} and logo URL: {logo_url}")
        
        # Handle serialization of word frequencies
        serializable_word_freqs = []
        try:
            if isinstance(word_frequencies, list):
                serializable_word_freqs = [
                    wf.dict() if hasattr(wf, "dict") and callable(getattr(wf, "dict")) else wf
                    for wf in word_frequencies
                ]
            else:
                serializable_word_freqs = word_frequencies
        except Exception as e:
            logger.error(f"Error serializing word frequencies: {e}")
            serializable_word_freqs = []
        
        # Handle serialization of text mining metrics
        serializable_text_mining = {}
        if isinstance(text_mining_metrics, dict):
            serializable_text_mining = text_mining_metrics
        elif hasattr(text_mining_metrics, "dict") and callable(getattr(text_mining_metrics, "dict")):
            serializable_text_mining = text_mining_metrics.dict()
        else:
            serializable_text_mining = text_mining_metrics
        
        # Check if a document with this URL already exists
        existing_doc = await document_crud.get_by_retrieved_url(doc_url, doc_type)
        
        if existing_doc:
            logger.info(f"Document with URL {doc_url} already exists. Using existing document.")
            document_id = existing_doc['id']
            
            # Update the document's views count
            await document_crud.increment_views(document_id)
        else:
            # Create a new document
            logger.info(f"Creating new document for URL: {doc_url}")
            
            # Create document data dictionary
            document_data = {
                "url": url,
                "document_type": doc_type,
                "retrieved_url": doc_url,
                "company_name": company_name,
                "logo_url": logo_url,
                "raw_text": raw_text,
                "one_sentence_summary": one_sentence_summary,
                "hundred_word_summary": hundred_word_summary,
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
async def crawl_tos(request: CrawlTosRequest):
    logger.info(f"Received request to crawl ToS for URL: {request.url}")
    
    response = CrawlTosResponse(
        url=request.url,
        success=False,
        message="Failed to crawl Terms of Service"
    )
    
    try:
        tos_url = None
        try:
            tos_url = await find_tos_url(request.url)
            if tos_url:
                logger.info(f"Found Terms of Service URL: {tos_url}")
                response.tos_url = tos_url
            else:
                response.message = "Could not find Terms of Service URL"
                return response
        except Exception as e:
            logger.error(f"Error finding Terms of Service URL: {e}")
            response.message = f"Error finding Terms of Service URL: {str(e)}"
            return response
        
        extracted_text = None
        try:
            extracted_text = await extract_text_from_url(tos_url)
            if not extracted_text:
                response.message = "Could not extract text from Terms of Service page"
                return response
            logger.info(f"Successfully extracted text from Terms of Service page. Text length: {len(extracted_text)}")
        except Exception as e:
            logger.error(f"Error extracting text from Terms of Service page: {e}")
            response.message = f"Error extracting text from Terms of Service page: {str(e)}"
            return response
        
        # Generate one-sentence summary
        try:
            one_sentence_summary = await generate_one_sentence_summary(extracted_text)
            response.one_sentence_summary = one_sentence_summary
            logger.info(f"Generated one-sentence summary: {one_sentence_summary}")
        except Exception as e:
            logger.error(f"Error generating one-sentence summary: {e}")
            response.one_sentence_summary = "Error generating summary"
        
        # Generate hundred-word summary
        try:
            hundred_word_summary = await generate_hundred_word_summary(extracted_text)
            response.hundred_word_summary = hundred_word_summary
            logger.info("Generated hundred-word summary successfully")
        except Exception as e:
            logger.error(f"Error generating hundred-word summary: {e}")
            response.hundred_word_summary = "Error generating summary"
        
        # Generate word frequencies
        try:
            word_frequencies = get_word_frequencies(extracted_text)
            response.word_frequencies = word_frequencies
            logger.info(f"Generated word frequencies. Top frequency: {word_frequencies[0].word if word_frequencies else 'None'}")
        except Exception as e:
            logger.error(f"Error generating word frequencies: {e}")
            response.word_frequencies = []
        
        # Generate text mining metrics
        try:
            text_mining_metrics = extract_text_mining_metrics(extracted_text)
            response.text_mining = text_mining_metrics
            logger.info("Generated text mining metrics successfully")
        except Exception as e:
            logger.error(f"Error generating text mining metrics: {e}")
            response.text_mining = TextMiningResults()
        
        # Save document to database
        try:
            document_id = await save_document_to_db(
                doc_type="tos",
                url=request.url,
                doc_url=tos_url,
                one_sentence_summary=response.one_sentence_summary,
                hundred_word_summary=response.hundred_word_summary,
                text_mining_metrics=response.text_mining,
                word_frequencies=response.word_frequencies,
                raw_text=extracted_text
            )
            response.document_id = document_id
            logger.info(f"Saved document to database with ID: {document_id}")
        except Exception as e:
            logger.error(f"Error saving document to database: {e}")
            # Don't fail the response if we couldn't save to the database
            # The document analysis is still returned
        
        response.success = True
        response.message = "Successfully crawled and analyzed Terms of Service"
        
        # Don't return the full extracted text to improve response performance
        return response
    
    except Exception as e:
        logger.error(f"Unexpected error during ToS crawling: {e}")
        response.message = f"Unexpected error: {str(e)}"
        return response

@router.post("/crawl-pp", response_model=CrawlPrivacyResponse)
async def crawl_privacy_policy(request: CrawlPrivacyRequest):
    logger.info(f"Received request to crawl Privacy Policy for URL: {request.url}")
    
    response = CrawlPrivacyResponse(
        url=request.url,
        success=False,
        message="Failed to crawl Privacy Policy"
    )
    
    try:
        pp_url = None
        try:
            pp_url = await find_privacy_policy_url(request.url)
            if pp_url:
                logger.info(f"Found Privacy Policy URL: {pp_url}")
                response.pp_url = pp_url
            else:
                response.message = "Could not find Privacy Policy URL"
                return response
        except Exception as e:
            logger.error(f"Error finding Privacy Policy URL: {e}")
            response.message = f"Error finding Privacy Policy URL: {str(e)}"
            return response
        
        extracted_text = None
        try:
            extracted_text = await extract_text_from_url(pp_url)
            if not extracted_text:
                response.message = "Could not extract text from Privacy Policy page"
                return response
            logger.info(f"Successfully extracted text from Privacy Policy page. Text length: {len(extracted_text)}")
        except Exception as e:
            logger.error(f"Error extracting text from Privacy Policy page: {e}")
            response.message = f"Error extracting text from Privacy Policy page: {str(e)}"
            return response
        
        # Generate one-sentence summary
        try:
            one_sentence_summary = await generate_one_sentence_summary(extracted_text)
            response.one_sentence_summary = one_sentence_summary
            logger.info(f"Generated one-sentence summary: {one_sentence_summary}")
        except Exception as e:
            logger.error(f"Error generating one-sentence summary: {e}")
            response.one_sentence_summary = "Error generating summary"
        
        # Generate hundred-word summary
        try:
            hundred_word_summary = await generate_hundred_word_summary(extracted_text)
            response.hundred_word_summary = hundred_word_summary
            logger.info("Generated hundred-word summary successfully")
        except Exception as e:
            logger.error(f"Error generating hundred-word summary: {e}")
            response.hundred_word_summary = "Error generating summary"
        
        # Generate word frequencies
        try:
            word_frequencies = get_word_frequencies(extracted_text)
            response.word_frequencies = word_frequencies
            logger.info(f"Generated word frequencies. Top frequency: {word_frequencies[0].word if word_frequencies else 'None'}")
        except Exception as e:
            logger.error(f"Error generating word frequencies: {e}")
            response.word_frequencies = []
        
        # Generate text mining metrics
        try:
            text_mining_metrics = extract_text_mining_metrics(extracted_text)
            response.text_mining = text_mining_metrics
            logger.info("Generated text mining metrics successfully")
        except Exception as e:
            logger.error(f"Error generating text mining metrics: {e}")
            response.text_mining = TextMiningResults()
        
        # Save document to database
        try:
            document_id = await save_document_to_db(
                doc_type="pp",
                url=request.url,
                doc_url=pp_url,
                one_sentence_summary=response.one_sentence_summary,
                hundred_word_summary=response.hundred_word_summary,
                text_mining_metrics=response.text_mining,
                word_frequencies=response.word_frequencies,
                raw_text=extracted_text
            )
            response.document_id = document_id
            logger.info(f"Saved document to database with ID: {document_id}")
        except Exception as e:
            logger.error(f"Error saving document to database: {e}")
            # Don't fail the response if we couldn't save to the database
            # The document analysis is still returned
        
        response.success = True
        response.message = "Successfully crawled and analyzed Privacy Policy"
        
        # Don't return the full extracted text to improve response performance
        return response
    
    except Exception as e:
        logger.error(f"Unexpected error during Privacy Policy crawling: {e}")
        response.message = f"Unexpected error: {str(e)}"
        return response 

async def extract_text_from_url(url: str) -> str:
    """
    Wrapper function to extract text from a URL.
    
    Args:
        url: The URL to extract text from
        
    Returns:
        The extracted text from the URL
    """
    logger.info(f"Extracting text from URL: {url}")
    
    # Create extract request
    extract_request = ExtractRequest(url=url)
    
    # Call extract_text endpoint function
    response = await extract_text(extract_request, Response())
    
    if response.success:
        logger.info(f"Successfully extracted text from URL: {url}")
        return response.text
    else:
        logger.warning(f"Failed to extract text from URL {url}: {response.message}")
        return "" 