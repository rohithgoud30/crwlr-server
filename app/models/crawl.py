from pydantic import BaseModel, Field, validator
from typing import Optional, List

from app.models.wordfrequency import WordFrequency
from app.models.textmining import TextMiningResults

class CrawlTosRequest(BaseModel):
    url: str

class CrawlTosResponse(BaseModel):
    url: str
    tos_url: Optional[str] = None
    company_name: Optional[str] = None
    logo_url: Optional[str] = None
    views: Optional[int] = 0
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    text_mining: Optional[TextMiningResults] = None
    word_frequencies: Optional[List[WordFrequency]] = None
    document_id: Optional[str] = None
    success: bool
    message: str
    
    @validator('success')
    def validate_success(cls, v, values):
        # If message indicates document exists, ensure success is False
        if 'message' in values and values['message'] == "Document already exists in database.":
            return False
        return v

class CrawlPrivacyRequest(BaseModel):
    url: str

class CrawlPrivacyResponse(BaseModel):
    url: str
    pp_url: Optional[str] = None
    company_name: Optional[str] = None
    logo_url: Optional[str] = None
    views: Optional[int] = 0
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    text_mining: Optional[TextMiningResults] = None
    word_frequencies: Optional[List[WordFrequency]] = None
    document_id: Optional[str] = None
    success: bool
    message: str
    
    @validator('success')
    def validate_success(cls, v, values):
        # If message indicates document exists, ensure success is False
        if 'message' in values and values['message'] == "Document already exists in database.":
            return False
        return v

class ReanalyzeTosRequest(BaseModel):
    document_id: str
    url: Optional[str] = None  # Optional new URL to use for extraction
    
class ReanalyzeTosResponse(CrawlTosResponse):
    pass
    
class ReanalyzePrivacyRequest(BaseModel):
    document_id: str
    url: Optional[str] = None  # Optional new URL to use for extraction
    
class ReanalyzePrivacyResponse(CrawlPrivacyResponse):
    pass 