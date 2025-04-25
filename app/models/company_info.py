from pydantic import BaseModel, Field
from typing import Optional

class CompanyInfoRequest(BaseModel):
    """Request model for company information extraction"""
    url: str = Field(..., description="URL of the website to extract company information from")
    logo_url: Optional[str] = Field(None, description="Optional custom logo URL to use")

class CompanyInfoResponse(BaseModel):
    """Response model for company information extraction"""
    url: str = Field(..., description="Original URL that was provided in the request")
    company_name: Optional[str] = Field(None, description="Extracted company name")
    logo_url: Optional[str] = Field(None, description="URL to the company logo/favicon")
    success: bool = Field(..., description="Whether the extraction was successful")
    message: str = Field(..., description="Success message or error details") 