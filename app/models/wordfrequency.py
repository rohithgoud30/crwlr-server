from pydantic import BaseModel, Field, computed_field
from typing import Dict, List, Optional, Literal

class WordFrequency(BaseModel):
    word: str
    count: int
    percentage: float
    
    @computed_field
    @property
    def percentage_display(self) -> str:
        """Return the percentage as a formatted string with % symbol"""
        return f"{self.percentage * 100:.2f}%"

class WordFrequencyRequest(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    text: Optional[str] = None
    max_words: Optional[int] = 20

class WordFrequencyResponse(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    word_frequencies: List[WordFrequency]
    success: bool  # Indicates if the operation was successful
    message: str  # Status message or additional information about the processing result 