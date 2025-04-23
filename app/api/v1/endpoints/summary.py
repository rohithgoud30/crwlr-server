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
        base_url = ""
        document_type = request.document_type or "tos"
        
        # Handle input from extract endpoint
        if hasattr(request, 'extract_response') and request.extract_response:
            if request.extract_response.text:
                text = request.extract_response.text
                # Use document type from extract response if available
                if request.extract_response.document_type:
                    document_type = request.extract_response.document_type
                if request.extract_response.url:
                    base_url = request.extract_response.url
                
        logger.info(f"Processing summary request for document type: {document_type}")
        
        # Get API key from multiple possible sources
        API_KEY = settings.GEMINI_API_KEY
        
        # Try to get API key directly from environment if not in settings
        if not API_KEY:
            API_KEY = os.environ.get("GEMINI_API_KEY")
            if API_KEY:
                logger.info("Found GEMINI_API_KEY in environment variables")
            
        # Log API key status (without revealing the key)
        logger.info(f"GEMINI_API_KEY status: {'Available' if API_KEY else 'Not Available'}")
        
        if not API_KEY:
            logger.error("GEMINI_API_KEY not found in environment variables or settings")
            response.status_code = 500
            return SummaryResponse(
                url=base_url,
                document_type=document_type,
                success=False,
                message="GEMINI_API_KEY not configured"
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
                logger.error(f"API request failed with status code {api_response.status_code}: {api_response.text}")
                response.status_code = 500
                return SummaryResponse(
                    url=base_url,
                    document_type=document_type,
                    success=False,
                    message=f"Error from Gemini API: {api_response.text}"
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
                response.status_code = 500
                return SummaryResponse(
                    url=base_url,
                    document_type=document_type,
                    success=False,
                    message=f"Error parsing Gemini API response: {str(e)}"
                )
                
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        response.status_code = 500
        return SummaryResponse(
            url="",
            document_type=request.document_type or "tos",
            success=False,
            message=f"Error generating summary: {str(e)}"
        ) 