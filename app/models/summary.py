from pydantic import BaseModel, Field
from typing import Optional, Literal

from app.models.extract import ExtractResponse

class SummaryRequest(BaseModel):
    url: Optional[str] = None  # The actual document URL, now optional
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    text: Optional[str] = None
    extract_response: Optional[ExtractResponse] = None  # Results from extract endpoint if available
    company_name: Optional[str] = None  # Company name for more realistic summaries
    provider: Optional[Literal["google", "zai"]] = Field(default=None, description="Summary provider override")
    model: Optional[str] = Field(default=None, description="Model override used for the selected provider")

class SummaryResponse(BaseModel):
    url: Optional[str] = None  # The actual document URL, now optional
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    success: bool  # Indicates if the operation was successful
    message: str  # Status message or additional information about the processing result