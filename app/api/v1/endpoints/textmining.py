from fastapi import APIRouter, Response, HTTPException, Depends
import re
import logging
from collections import Counter
import textstat
import nltk
import ssl

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Download required NLTK data at initialization
try:
    # Fix SSL certificate verification issues with nltk downloads
    try:
        _create_unverified_https_context = ssl._create_unverified_context
    except AttributeError:
        pass
    else:
        ssl._create_default_https_context = _create_unverified_https_context
    
    # Download necessary NLTK resources - download both punkt and punkt_tab
    nltk.download('punkt', quiet=True)
    nltk.download('stopwords', quiet=True)
    
    # Additional resources that might be needed
    try:
        # Try to download punkt_tab specifically
        import nltk.data
        try:
            nltk.data.find('tokenizers/punkt_tab/english')
        except LookupError:
            # If not found, try to download it, but it may not exist as a package
            try:
                nltk.download('punkt_tab', quiet=True)
                logger.info("Successfully downloaded punkt_tab resource")
            except Exception as punkt_error:
                logger.warning(f"Could not download punkt_tab: {str(punkt_error)}. " 
                              "This is normal as it may not exist as a separate package.")
    except Exception as e:
        logger.warning(f"Error checking punkt_tab: {str(e)}. Will continue with available resources.")
        
    logger.info("Successfully downloaded NLTK resources")
    from nltk.tokenize import word_tokenize, sent_tokenize
    from nltk.probability import FreqDist
    from nltk.corpus import stopwords
    STOPWORDS = set(stopwords.words('english'))
except Exception as e:
    logger.warning(f"Failed to download NLTK resources: {str(e)}. Some text mining features may be limited.")
    # Fallback to regex-based methods if NLTK fails
    STOPWORDS = {"the", "and", "a", "to", "of", "in", "is", "you", "that", "it", 
                "for", "on", "with", "as", "are", "be", "this", "was", "have", "or",
                "at", "from", "by", "an", "will", "can", "not", "or", "but", "if",
                "they", "their", "we", "us", "our", "your", "my", "he", "she", "his",
                "her", "i", "me", "what", "when", "where", "who", "how", "which", "there"}

from app.models.extract import ExtractResponse
from app.models.textmining import TextMiningRequest, TextMiningResponse, TextMiningResults

router = APIRouter()

@router.post("/textmining", response_model=TextMiningResponse)
async def analyze_text(request: TextMiningRequest) -> TextMiningResponse:
    """
    Performs comprehensive text mining analysis on the provided text.
    Returns various text metrics.
    
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
        
        logger.info(f"Processing text mining request for document type: {document_type}")
        
        if not text or len(text.strip()) == 0:
            return TextMiningResponse(
                url=base_url,
                document_type=document_type,
                text_mining=TextMiningResults(
                    word_count=0,
                    avg_word_length=0,
                    sentence_count=0,
                    avg_sentence_length=0,
                    readability_score=0,  # 0%
                    readability_interpretation="Not applicable - No text to analyze",
                    unique_word_ratio=0,  # 0%
                    capital_letter_freq=0,  # 0%
                    punctuation_density=0,  # 0%
                    question_frequency=0,  # 0%
                    paragraph_count=0,
                    common_word_percentage=0  # 0%
                ),
                success=False,
                message="Empty text provided"
            )
        
        # Text mining analysis
        text_mining_results = perform_text_mining(text)
        
        return TextMiningResponse(
            url=base_url,
            document_type=document_type,
            text_mining=text_mining_results,
            success=True,
            message="Text mining analysis completed successfully"
        )
        
    except Exception as e:
        logger.error(f"Error in text mining analysis: {str(e)}")
        return TextMiningResponse(
            url="",
            document_type=request.document_type or "tos",
            text_mining=TextMiningResults(
                word_count=0,
                avg_word_length=0,
                sentence_count=0,
                avg_sentence_length=0,
                readability_score=0,  # 0%
                readability_interpretation="Not applicable - Analysis failed",
                unique_word_ratio=0,  # 0%
                capital_letter_freq=0,  # 0%
                punctuation_density=0,  # 0%
                question_frequency=0,  # 0%
                paragraph_count=0,
                common_word_percentage=0  # 0%
            ),
            success=False,
            message=f"Error in text mining analysis: {str(e)}"
        )

def perform_text_mining(text: str) -> TextMiningResults:
    """
    Performs text mining analysis on the given text using NLTK and textstat for improved accuracy.
    With fallback to regex methods if NLTK fails.
    """
    # Clean the text while preserving essential structures
    clean_text = text.strip()
    
    try:
        # Try using NLTK for better accuracy
        try:
            words = word_tokenize(clean_text.lower())
            # Filter out punctuation tokens
            words = [word for word in words if re.match(r'\w+', word)]
            
            # Sentence detection with NLTK
            sentences = sent_tokenize(clean_text)
            
            # Capital words detection with NLTK tokens
            original_words = word_tokenize(clean_text)
            capital_words = [word for word in original_words if re.match(r'^[A-Z][a-zA-Z]*$', word)]
            
            # Question sentences using NLTK
            question_sentences = [s for s in sentences if s.strip().endswith('?')]
        except LookupError as e:
            # Handle missing resource errors specifically
            logger.warning(f"NLTK resource lookup error: {str(e)}. Using regex fallback.")
            raise  # Re-raise to fall back to regex method
            
    except Exception as e:
        logger.warning(f"NLTK processing failed, falling back to regex: {str(e)}")
        # Fallback to regex methods
        words = re.findall(r'\b\w+\b', clean_text.lower())
        sentences = re.split(r'[.!?]+(?=\s+[A-Z]|\s*$)', clean_text)
        sentences = [s for s in sentences if s.strip()]
        original_words = re.findall(r'\b\w+\b', clean_text)
        capital_words = [word for word in original_words if word and word[0].isupper()]
        question_marks = re.findall(r'\?', clean_text)
        question_sentences = question_marks
    
    # Continue with the calculations
    word_count = len(words)
    sentence_count = len(sentences)
    
    # 2. Average Word Length
    total_word_length = sum(len(word) for word in words)
    avg_word_length = total_word_length / word_count if word_count > 0 else 0
    
    # 4. Average Sentence Length
    avg_sentence_length = word_count / sentence_count if sentence_count > 0 else 0
    
    # 5. Unique Word Ratio
    unique_words = set(words)
    unique_word_ratio = len(unique_words) / word_count if word_count > 0 else 0
    
    # 6. Capital Letter Frequency
    capital_letter_freq = len(capital_words) / word_count if word_count > 0 else 0
    
    # 7. Punctuation Density
    punctuation_marks = re.findall(r'[^\w\s]', clean_text)
    punctuation_density = len(punctuation_marks) / word_count if word_count > 0 else 0
    
    # 8. Question Frequency
    question_frequency = len(question_sentences) / sentence_count if sentence_count > 0 else 0
    
    # 9. Paragraph Count
    paragraphs = re.split(r'\n\s*\n', clean_text)
    paragraphs = [p for p in paragraphs if p.strip()]
    paragraph_count = len(paragraphs)
    
    # 10. Common Word Percentage
    common_word_count = sum(1 for word in words if word.lower() in STOPWORDS)
    common_word_percentage = common_word_count / word_count if word_count > 0 else 0
    
    # 11. Readability Score
    try:
        # Try using textstat's Flesch Reading Ease score
        readability_score = textstat.flesch_reading_ease(clean_text)
    except Exception as e:
        logger.warning(f"Textstat processing failed, using simplified scoring: {str(e)}")
        # Fallback to simplified scoring
        words_per_sentence = avg_sentence_length
        syllables_per_word = avg_word_length / 3
        readability_score = 206.835 - (1.015 * words_per_sentence) - (84.6 * syllables_per_word)
    
    # Ensure readability score stays in the 0-100 range
    readability_score = max(0, min(100, readability_score))
    
    # Interpret the readability score
    readability_interpretation = get_readability_interpretation(readability_score)
    
    return TextMiningResults(
        word_count=word_count,
        avg_word_length=round(avg_word_length, 2),
        sentence_count=sentence_count,
        avg_sentence_length=round(avg_sentence_length, 2),
        readability_score=round(readability_score, 2),
        readability_interpretation=readability_interpretation,
        unique_word_ratio=round(unique_word_ratio * 100, 2),  # Convert to percentage
        capital_letter_freq=round(capital_letter_freq * 100, 2),  # Convert to percentage
        punctuation_density=round(punctuation_density * 100, 2),  # Convert to percentage
        question_frequency=round(question_frequency * 100, 2),  # Convert to percentage
        paragraph_count=paragraph_count,
        common_word_percentage=round(common_word_percentage * 100, 2)  # Convert to percentage
    )

def get_readability_interpretation(score: float) -> str:
    """
    Returns a human-friendly interpretation of the Flesch Reading Ease score.
    
    Args:
        score: Flesch Reading Ease score (0-100)
    
    Returns:
        A string describing the readability level and audience
    """
    if score >= 90:
        return "Very Easy: 5th-grade level - Easily understood by an average 11-year-old student."
    elif score >= 80:
        return "Easy: 6th-grade level - Conversational English for consumers."
    elif score >= 70:
        return "Fairly Easy: 7th-grade level - Accessible to most users."
    elif score >= 60:
        return "Standard: 8th-9th grade level - Plain English, easily understood by teenagers."
    elif score >= 50:
        return "Fairly Difficult: 10th-12th grade level - Requires high school education."
    elif score >= 30:
        return "Difficult: College level - Complex, technical content for specialized audiences."
    else:
        return "Very Difficult: College graduate level - Extremely complex, potentially legal or scientific content." 