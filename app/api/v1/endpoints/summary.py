from fastapi import APIRouter, Response, HTTPException
import httpx
import logging
import re
import os
from app.core.config import settings
from app.models.summary import SummaryRequest, SummaryResponse
from app.models.extract import ExtractResponse, ExtractRequest
# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

def clean_summary_text(text: str) -> str:
    """
    Clean summary text by removing unwanted characters and formatting
    """
    # Remove markdown formatting (asterisks, quotes, etc.)
    text = re.sub(r'[*"`\']+', '', text)
    
    # Remove excessive newlines
    text = re.sub(r'\n{2,}', '\n\n', text)
    
    # Strip leading/trailing whitespace
    text = text.strip()
    
    return text

def get_gemini_api_key():
    """
    Get the Gemini API key from environment variables or settings.
    Checks multiple possible environment variable names.
    """
    # First try settings
    api_key = settings.GEMINI_API_KEY
    
    # Then try various environment variable names
    if not api_key:
        for env_var in ["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY"]:
            api_key = os.environ.get(env_var)
            if api_key:
                logger.info(f"Found API key in environment variable: {env_var}")
                break
    
    # Log API key status (without revealing the key)
    if api_key:
        logger.info("API key status: Available")
        # Check for API key validity - minimum length and format
        if len(api_key) < 20:
            logger.warning("Gemini API key appears too short. Check for correct formatting.")
    else:
        logger.error("Gemini API key not found in any environment variable or settings")
    
    return api_key

@router.post("/summary", response_model=SummaryResponse)
async def generate_summary(request: SummaryRequest, response: Response) -> SummaryResponse:
    """
    Takes text content (such as Terms of Service or Privacy Policy text) and generates 
    a one-sentence summary and a 100-word summary using Gemini API.
    
    The input can be either direct text or an ExtractResponse from the extract endpoint.
    """
    try:
        # Determine if this is an ExtractResponse object or direct text input
        text = request.text
        base_url = request.url or ""
        document_type = request.document_type or "tos"
        extraction_success = True
        extraction_message = ""
        
        # Handle input from extract endpoint
        if hasattr(request, 'extract_response') and request.extract_response:
            extraction_success = request.extract_response.success
            extraction_message = request.extract_response.message
            
            if request.extract_response.text:
                text = request.extract_response.text
                # Use document type from extract response if available
                if request.extract_response.document_type:
                    document_type = request.extract_response.document_type
                if request.extract_response.url:
                    base_url = request.extract_response.url
        
        logger.info(f"Processing summary request for document type: {document_type}")
        
        # Check if extraction failed due to bot detection or other issues
        if not extraction_success:
            logger.warning(f"Text extraction failed: {extraction_message}")
            # If extraction failed, return appropriate error message
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                success=False,
                message=f"Unable to generate summary: {extraction_message}",
                one_sentence_summary=None,
                hundred_word_summary=None
            )
            
        # Check for bot detection patterns in the extracted text
        if text and is_likely_bot_verification_text(text):
            logger.warning("Bot verification content detected in extracted text")
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                success=False,
                message="Unable to generate summary: Bot verification page detected - unable to access actual content",
                one_sentence_summary=None,
                hundred_word_summary=None
            )
        
        # If no text is provided, return an error
        if not text or len(text.strip()) < 100:
            logger.warning("Insufficient text provided for summarization")
            
            # Still return successful response with extracted text, even if insufficient
            # Rather than failing, just indicate no summary is available
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                success=True,
                message="Text is too short to generate a meaningful summary. A minimum of 100 characters is required.",
                one_sentence_summary=None,
                hundred_word_summary=None
            )
        
        # Get API key using helper function
        API_KEY = get_gemini_api_key()
        
        if not API_KEY:
            logger.error("GEMINI_API_KEY not found in environment variables or settings")
            
            # Still return a successful response with the extracted text
            # Just report that summarization failed due to API key
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                success=True,
                message="Text extraction succeeded but summarization failed: GEMINI_API_KEY not configured",
                one_sentence_summary=None, 
                hundred_word_summary=None
            )
        
        # Prepare the content
        content = text
        doc_type = document_type
        
        # Map document type to full name
        if doc_type == "pp":
            doc_type_full = "Privacy Policy"
        elif doc_type == "tos":
            doc_type_full = "Terms of Service"
        else:
            # Default to using the original value
            doc_type_full = doc_type
        
        # Construct the prompt
        prompt = f"""100-WORD SUMMARY

Write a concise, factual 100-word summary of the {doc_type_full} for the company described. Focus on the company policies and practices without referencing external services, other companies, or general industry practices.

Requirements:
- Exactly 100 words (±10)
- Single paragraph
- Objective, factual tone
- No personal pronouns (I, we, you)
- No meta-references (e.g., "this document", "this text" ,"this policy")
- No conditional language (e.g., "may", "might", "could")
- No links or external references

Provide a direct, factual, and company-specific summary.


ONE-SENTENCE SUMMARY

Write a single sentence (maximum 40 words) summarizing the most important aspect of the {doc_type_full} for the company described. Focus on the company policies and practices without referencing external services, other companies, or general industry practices.

Requirements:
- One clear, direct sentence
- Maximum 40 words
- Objective, factual tone
- No personal pronouns (I, we, you)
- No meta-references (e.g., "this document", "this text")
- No conditional language (e.g., "may", "might", "could")
- No links or external references


Here is the document content:

{content}"""
        
        # Construct JSON payload
        json_payload = {
            "contents": [{
                "parts": [{
                    "text": prompt
                }]
            }]
        }
        
        # Log that we're about to make the API request
        logger.info(f"Sending request to Gemini API for {document_type} summary")
        
        # Attempt to make API call with error handling
        try:
            # Send API request
            async with httpx.AsyncClient() as client:
                api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-8b-001:generateContent?key={API_KEY}"
                logger.info(f"Using Gemini API URL: {api_url.split('?key=')[0]}") # Log URL without the API key
                
                api_response = await client.post(
                    api_url,
                    headers={"Content-Type": "application/json"},
                    json=json_payload,
                    timeout=60.0
                )
                
                # Check if request was successful
                if api_response.status_code != 200:
                    error_msg = f"API request failed with status code {api_response.status_code}: {api_response.text}"
                    logger.error(error_msg)
                    
                    # Return with extraction success but failed summary
                    return SummaryResponse(
                        url=base_url,
                        document_type=document_type,
                        success=True,
                        message=f"Text extraction succeeded but summarization failed: {error_msg[:200]}",
                        one_sentence_summary=None,
                        hundred_word_summary=None
                    )
                
                # Parse response
                response_data = api_response.json()
                logger.info("Successfully received response from Gemini API")
                
                # Extract text from response
                try:
                    summary_text = response_data['candidates'][0]['content']['parts'][0]['text']
                    
                    # Extract the 100-word summary
                    hundred_word_start = summary_text.find("100-WORD SUMMARY")
                    one_sentence_start = summary_text.find("ONE-SENTENCE SUMMARY")
                    
                    hundred_word_summary = summary_text[hundred_word_start:one_sentence_start].strip()
                    hundred_word_summary = "\n".join(hundred_word_summary.split("\n")[1:]).strip()
                    
                    # Extract the one-sentence summary
                    one_sentence_summary = summary_text[one_sentence_start:].strip()
                    one_sentence_summary = "\n".join(one_sentence_summary.split("\n")[1:]).strip()
                    
                    # Clean summaries
                    hundred_word_summary = clean_summary_text(hundred_word_summary)
                    one_sentence_summary = clean_summary_text(one_sentence_summary)
                    
                    return SummaryResponse(
                        url=base_url,
                        document_type=document_type,
                        one_sentence_summary=one_sentence_summary,
                        hundred_word_summary=hundred_word_summary,
                        success=True,
                        message="Successfully generated summaries"
                    )
                    
                except (KeyError, IndexError) as e:
                    logger.error(f"Error parsing API response: {e}")
                    logger.error(f"Response data: {response_data}")
                    return SummaryResponse(
                        url=base_url,
                        document_type=document_type,
                        success=True,
                        message=f"Text extraction succeeded but error parsing Gemini API response: {str(e)}",
                        one_sentence_summary=None,
                        hundred_word_summary=None
                    )
                    
        except httpx.HTTPError as e:
            logger.error(f"HTTP error during API call: {str(e)}")
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                success=True,
                message=f"Text extraction succeeded but summarization failed: HTTP error - {str(e)}",
                one_sentence_summary=None,
                hundred_word_summary=None
            )
                
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return SummaryResponse(
            url=request.url or "",
            document_type=request.document_type or "tos",
            success=False,
            message=f"Error: {str(e)}",
            one_sentence_summary=None,
            hundred_word_summary=None
        ) 

def is_likely_bot_verification_text(text: str) -> bool:
    """
    Checks if the extracted text is likely from a bot verification page rather than actual content.
    """
    if not text:
        return False
        
    text_lower = text.lower()
    word_count = len(text.split())
    
    # Verification keywords that suggest this is a bot check page
    verification_phrases = [
        "verify yourself", "verification required", "security check",
        "captcha", "prove you're human", "not a robot", 
        "security verification", "security measure"
    ]
    
    # If the text is short and contains verification phrases, it's likely a bot check
    if word_count < 200 and any(phrase in text_lower for phrase in verification_phrases):
        # Check for additional bot verification indicators
        bot_indicators = [
            "browser", "reload", "retry", "try again", 
            "refresh", "access", "blocked", "temporary"
        ]
        
        matches = [phrase for phrase in verification_phrases if phrase in text_lower]
        indicators = [indicator for indicator in bot_indicators if indicator in text_lower]
        
        # If we find both verification phrases and indicators, it's very likely a bot page
        if matches and indicators:
            logger.warning(f"Bot verification content detected. Phrases: {matches}, Indicators: {indicators}")
            return True
    
    return False 