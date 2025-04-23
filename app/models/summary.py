from pydantic import BaseModel, Field
from typing import Optional, Literal

from app.models.extract import ExtractResponse

class SummaryRequest(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    text: Optional[str] = None

class SummaryResponse(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    success: bool  # Indicates if the operation was successful
    message: str  # Status message or additional information about the processing result