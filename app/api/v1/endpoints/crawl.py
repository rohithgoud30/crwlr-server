from fastapi import APIRouter, Response, HTTPException, status
import logging
import asyncio

from app.api.v1.endpoints.tos import find_tos
from app.api.v1.endpoints.privacy import find_privacy_policy
from app.api.v1.endpoints.extract import extract_text
from app.api.v1.endpoints.summary import generate_summary
from app.api.v1.endpoints.wordfrequency import analyze_word_freq_endpoint
from app.api.v1.endpoints.textmining import analyze_text as analyze_text_mining

from app.models.tos import ToSRequest
from app.models.privacy import PrivacyRequest
from app.models.extract import ExtractRequest
from app.models.summary import SummaryRequest, SummaryResponse
from app.models.wordfrequency import WordFrequencyRequest, WordFrequencyResponse
from app.models.textmining import TextMiningRequest, TextMiningResponse, TextMiningResults
from app.models.crawl import CrawlTosRequest, CrawlTosResponse, CrawlPrivacyRequest, CrawlPrivacyResponse

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

@router.post("/crawl-tos", response_model=CrawlTosResponse)
async def crawl_tos(request: CrawlTosRequest) -> CrawlTosResponse:
    """
    Crawls a website to find, extract, summarize, analyze word frequency,
    and perform text mining on its Terms of Service in one request.
    
    Steps:
    1. Find the Terms of Service URL
    2. Extract the text content
    3. Perform all analyses (summary, word frequency, text mining) in parallel
    
    Returns all results in a single response, excluding the full extracted text
    for better performance.
    """
    logger.info(f"Processing optimized crawl-tos request for URL: {request.url}")
    
    # Step 1: Find ToS URL
    tos_request = ToSRequest(url=request.url)
    tos_response = await find_tos(tos_request)
    
    if not tos_response.success or not tos_response.tos_url:
        return CrawlTosResponse(
            url=request.url,
            success=False,
            message=f"Failed to find Terms of Service URL: {tos_response.message}",
        )
    
    # Step 2: Extract text from ToS URL
    extract_request = ExtractRequest(url=tos_response.tos_url, document_type="tos")
    extract_response = await extract_text(extract_request, Response())
    
    if not extract_response.success or not extract_response.text:
        return CrawlTosResponse(
            url=request.url,
            tos_url=tos_response.tos_url,
            success=False,
            message=f"Failed to extract text from ToS URL: {extract_response.message}",
        )
    
    # Make sure the document type is consistently set to "tos"
    extract_response.document_type = "tos"
    
    # Extract successful - proceed with parallel analyses
    extracted_text = extract_response.text
    
    # Step 3: Perform all analyses in parallel
    summary_response, word_freq_response, text_mining_response = await perform_parallel_analysis(
        tos_response.tos_url, extracted_text, "tos"
    )
    
    # Check if summary failed but continue with other analyses
    summary_message = "Successfully generated summaries"
    one_sentence_summary = None
    hundred_word_summary = None
    
    if not summary_response.success:
        summary_message = f"Failed to generate summary: {summary_response.message}"
        logger.warning(summary_message)
    else:
        one_sentence_summary = summary_response.one_sentence_summary
        hundred_word_summary = summary_response.hundred_word_summary
    
    # Return the complete response with all analyses
    return CrawlTosResponse(
        url=request.url,
        tos_url=tos_response.tos_url,
        # Not returning the full text to reduce response size
        extracted_text=None,
        one_sentence_summary=one_sentence_summary,
        hundred_word_summary=hundred_word_summary,
        text_mining=text_mining_response.text_mining if text_mining_response.success else None,
        word_frequencies=word_freq_response.word_frequencies if word_freq_response.success else None,
        success=True,
        message=f"Successfully crawled and analyzed Terms of Service. {summary_message}",
    )

@router.post("/crawl-pp", response_model=CrawlPrivacyResponse)
async def crawl_privacy_policy(request: CrawlPrivacyRequest) -> CrawlPrivacyResponse:
    """
    Crawls a website to find, extract, summarize, analyze word frequency,
    and perform text mining on its Privacy Policy in one request.
    
    Steps:
    1. Find the Privacy Policy URL
    2. Extract the text content
    3. Perform all analyses (summary, word frequency, text mining) in parallel
    
    Returns all results in a single response, excluding the full extracted text
    for better performance.
    """
    logger.info(f"Processing optimized crawl-pp request for URL: {request.url}")
    
    # Step 1: Find Privacy Policy URL
    pp_request = PrivacyRequest(url=request.url)
    pp_response = await find_privacy_policy(pp_request)
    
    if not pp_response.success or not pp_response.pp_url:
        return CrawlPrivacyResponse(
            url=request.url,
            success=False,
            message=f"Failed to find Privacy Policy URL: {pp_response.message}",
        )
    
    # Step 2: Extract text from Privacy Policy URL
    extract_request = ExtractRequest(url=pp_response.pp_url, document_type="pp")
    extract_response = await extract_text(extract_request, Response())
    
    if not extract_response.success or not extract_response.text:
        return CrawlPrivacyResponse(
            url=request.url,
            pp_url=pp_response.pp_url,
            success=False,
            message=f"Failed to extract text from Privacy Policy URL: {extract_response.message}",
        )
    
    # Make sure the document type is consistently set to "pp"
    extract_response.document_type = "pp"
    
    # Extract successful - proceed with parallel analyses
    extracted_text = extract_response.text
    
    # Step 3: Perform all analyses in parallel
    summary_response, word_freq_response, text_mining_response = await perform_parallel_analysis(
        pp_response.pp_url, extracted_text, "pp"
    )
    
    # Check if summary failed but continue with other analyses
    summary_message = "Successfully generated summaries"
    one_sentence_summary = None
    hundred_word_summary = None
    
    if not summary_response.success:
        summary_message = f"Failed to generate summary: {summary_response.message}"
        logger.warning(summary_message)
    else:
        one_sentence_summary = summary_response.one_sentence_summary
        hundred_word_summary = summary_response.hundred_word_summary
    
    # Return the complete response with all analyses
    return CrawlPrivacyResponse(
        url=request.url,
        pp_url=pp_response.pp_url,
        # Not returning the full text to reduce response size
        extracted_text=None,
        one_sentence_summary=one_sentence_summary,
        hundred_word_summary=hundred_word_summary,
        text_mining=text_mining_response.text_mining if text_mining_response.success else None,
        word_frequencies=word_freq_response.word_frequencies if word_freq_response.success else None,
        success=True,
        message=f"Successfully crawled and analyzed Privacy Policy. {summary_message}",
    ) 