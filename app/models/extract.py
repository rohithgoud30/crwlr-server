from pydantic import BaseModel, field_validator, HttpUrl
from typing import Optional, Any, List, Dict, Literal

class ExtractRequest(BaseModel):
    url: str  # URL to extract text from
    document_type: Optional[Literal["tos", "pp"]] = None  # Type of legal document to find and extract
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Basic URL validation."""
        if not v:
            raise ValueError("URL cannot be empty")
        
        # Add scheme if missing
        if not v.startswith(('http://', 'https://')):
            v = 'https://' + v
        
        return v


class ExtractResponse(BaseModel):
    url: str  # The actual document URL
    document_type: Literal["tos", "pp"]  # Type of document (only "tos" or "pp" allowed)
    text: Optional[str] = None  # Extracted text content
    success: bool  # Indicates if the operation was successful
    message: str  # Status message or additional information about the processing result
    method_used: Literal["standard", "playwright", "pdf", "simple_fetch"]  # Method used for extraction