from fastapi import APIRouter, Response, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Literal
import httpx
import logging
import re
from app.core.config import settings

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

class SummaryRequest(BaseModel):
    text: str = Field(..., description="Text content to summarize")
    document_type: Literal["Privacy Policy", "Terms of Service"] = Field(..., 
                          description="Type of document being summarized")

class SummaryResponse(BaseModel):
    success: bool
    document_type: str
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    message: str

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
    """
    try:
        logger.info(f"Processing summary request for document type: {request.document_type}")
        
        # Get API key from environment variable
        API_KEY = settings.GEMINI_API_KEY
        
        if not API_KEY:
            logger.error("GEMINI_API_KEY not found in environment variables")
            response.status_code = 500
            return SummaryResponse(
                success=False,
                document_type=request.document_type,
                message="GEMINI_API_KEY not configured"
            )
        
        # Prepare the content
        content = request.text
        doc_type = request.document_type
        
        # Construct the prompt
        prompt = f"""100-WORD SUMMARY

Write a concise, factual 100-word summary of the {doc_type} for the company described. Focus on the company policies and practices without referencing external services, other companies, or general industry practices.

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

Write a single sentence (maximum 40 words) summarizing the most important aspect of the {doc_type} for the company described. Focus on the company policies and practices without referencing external services, other companies, or general industry practices.

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
        
        # Send API request
        async with httpx.AsyncClient() as client:
            api_response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-8b-001:generateContent?key={API_KEY}",
                headers={"Content-Type": "application/json"},
                json=json_payload,
                timeout=60.0
            )
            
            # Check if request was successful
            if api_response.status_code != 200:
                logger.error(f"API request failed with status code {api_response.status_code}: {api_response.text}")
                response.status_code = 500
                return SummaryResponse(
                    success=False,
                    document_type=doc_type,
                    message=f"Error from Gemini API: {api_response.text}"
                )
            
            # Parse response
            response_data = api_response.json()
            
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
                    success=True,
                    document_type=doc_type,
                    one_sentence_summary=one_sentence_summary,
                    hundred_word_summary=hundred_word_summary,
                    message="Successfully generated summaries"
                )
                
            except (KeyError, IndexError) as e:
                logger.error(f"Error parsing API response: {e}")
                logger.error(f"Response data: {response_data}")
                response.status_code = 500
                return SummaryResponse(
                    success=False,
                    document_type=doc_type,
                    message=f"Error parsing Gemini API response: {str(e)}"
                )
                
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        response.status_code = 500
        return SummaryResponse(
            success=False,
            document_type=request.document_type,
            message=f"Error generating summary: {str(e)}"
        ) 