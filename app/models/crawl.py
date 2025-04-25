from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID

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
    document_id: Optional[UUID] = None
    success: bool
    message: str

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
    document_id: Optional[UUID] = None
    success: bool
    message: str 