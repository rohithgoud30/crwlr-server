from pydantic import BaseModel, field_validator, HttpUrl
from typing import Optional, Any, List, Dict

class ExtractRequest(BaseModel):
    url: str  # URL to extract text from
    
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
    url: str  # Original URL that was processed
    success: bool  # Whether the extraction was successful
    text: Optional[str] = None  # Extracted text content
    message: str  # Status message
    method_used: str  # Method used for extraction (standard, playwright, or pdf)