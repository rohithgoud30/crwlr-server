from pydantic import BaseModel
from typing import Dict, List, Optional, Literal

from app.models.extract import ExtractResponse

class TextMiningRequest(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    text: Optional[str] = None

class TextMiningResults(BaseModel):
    word_count: int  # Total number of words
    avg_word_length: float  # Average length of words (characters)
    sentence_count: int  # Total number of sentences
    avg_sentence_length: float  # Average number of words per sentence
    readability_score: float  # Readability score (0-100)
    readability_interpretation: str  # Human-friendly interpretation of readability score
    unique_word_ratio: float  # Percentage of unique words (0-100)
    capital_letter_freq: float  # Percentage of words starting with capital letters (0-100)
    punctuation_density: float  # Percentage of punctuation marks relative to word count (0-100)
    question_frequency: float  # Percentage of sentences that are questions (0-100)
    paragraph_count: int  # Total number of paragraphs
    common_word_percentage: float  # Percentage of common words (0-100)

class TextMiningResponse(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    text_mining: TextMiningResults
    success: bool  # Indicates if the operation was successful
    message: str  # Status message or additional information about the processing result 