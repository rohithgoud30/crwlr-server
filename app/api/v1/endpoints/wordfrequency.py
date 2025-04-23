from fastapi import APIRouter, Response, HTTPException, Depends
import logging
from collections import Counter
import re

from app.models.wordfrequency import WordFrequencyRequest, WordFrequencyResponse, WordFrequency

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/wordfrequency", response_model=WordFrequencyResponse)
async def analyze_word_frequency(request: WordFrequencyRequest) -> WordFrequencyResponse:
    """
    Analyzes word frequency in the provided text.
    Returns word frequency data without storing in database.
    """
    try:
        logger.info(f"Processing word frequency request for document type: {request.document_type}")
        
        if not request.text or len(request.text.strip()) == 0:
            return WordFrequencyResponse(
                url=request.url,  # Use the URL from the request
                document_type=request.document_type,
                word_frequencies=[],
                success=False,
                message="Empty text provided"
            )
        
        # Word frequency analysis
        max_words = min(request.max_words, 100) if request.max_words else 20  # Default to 20, max 100
        word_freqs = analyze_text_frequency(request.text, max_words)  # Renamed to avoid name conflict
        
        return WordFrequencyResponse(
            url=request.url,  # Use the URL from the request
            document_type=request.document_type,
            word_frequencies=word_freqs,
            success=True,
            message="Word frequency analysis completed successfully"
        )
        
    except Exception as e:
        logger.error(f"Error in word frequency analysis: {str(e)}")
        return WordFrequencyResponse(
            url=request.url,  # Use the URL from the request
            document_type=request.document_type,
            word_frequencies=[],
            success=False,
            message=f"Error in word frequency analysis: {str(e)}"
        )

# Create a wrapper function to handle either a request object or a direct text string
async def analyze_word_freq_endpoint(request_or_text):
    """
    Wrapper function that can handle either a WordFrequencyRequest object or text directly.
    This makes the function more flexible for different calling patterns.
    """
    try:
        if isinstance(request_or_text, WordFrequencyRequest):
            # If it's a request object, pass it directly to the main handler
            logger.info(f"Processing word frequency request from: {request_or_text.url}")
            return await analyze_word_frequency(request_or_text)
        elif isinstance(request_or_text, str):
            # If it's a string, create a request object and then process
            logger.info("Processing word frequency from direct text input")
            dummy_request = WordFrequencyRequest(
                url="internal-call",
                document_type="unknown",
                text=request_or_text,
                max_words=20
            )
            return await analyze_word_frequency(dummy_request)
        else:
            # Handle unexpected input
            input_type = type(request_or_text).__name__
            logger.error(f"Unexpected input type to analyze_word_freq_endpoint: {input_type}")
            return WordFrequencyResponse(
                url="internal-call", 
                document_type="unknown",
                word_frequencies=[],
                success=False,
                message=f"Invalid input type: {input_type}"
            )
    except Exception as e:
        # Additional error handling
        logger.error(f"Error in analyze_word_freq_endpoint: {str(e)}")
        # Determine URL and document type from the input if possible
        url = getattr(request_or_text, 'url', 'internal-call') if hasattr(request_or_text, 'url') else 'internal-call'
        doc_type = getattr(request_or_text, 'document_type', 'unknown') if hasattr(request_or_text, 'document_type') else 'unknown'
        
        return WordFrequencyResponse(
            url=url,
            document_type=doc_type,
            word_frequencies=[],
            success=False,
            message=f"Error processing word frequency: {str(e)}"
        )

def analyze_text_frequency(text: str, max_words: int = 20) -> list[WordFrequency]:
    """
    Analyzes word frequency in the given text.
    Returns a list of WordFrequency objects for the most frequent words.
    
    Args:
        text: The text to analyze
        max_words: Maximum number of frequent words to return (default: 20)
    """
    # Clean the text and split into words
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())  # Only words with 3+ chars
    
    # Skip common stopwords to make analysis more meaningful
    stopwords = {
        "the", "and", "a", "to", "of", "in", "is", "you", "that", "it", "for", 
        "on", "with", "as", "are", "be", "this", "was", "have", "or", "not", 
        "your", "by", "any", "all", "may", "will", "can", "from", "our", "their",
        "we", "us", "an", "its", "if", "at", "which", "these", "they", "them",
        "such", "been", "has", "when", "who", "would", "could", "should", "than",
        "then", "now", "into", "only", "other", "some", "what", "there", "also"
    }
    
    filtered_words = [word for word in words if word not in stopwords]
    
    # Count frequencies
    word_counts = Counter(filtered_words)
    total_words = len(filtered_words)
    
    # Convert to list of WordFrequency objects
    result = []
    for word, count in word_counts.most_common(max_words):
        result.append(WordFrequency(
            word=word,
            count=count,
            percentage=round(count / total_words if total_words > 0 else 0, 4)
        ))
    
    return result 