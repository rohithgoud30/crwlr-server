from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Literal, Any
from datetime import datetime


class UserBase(BaseModel):
    clerk_user_id: str
    email: str
    name: Optional[str] = None
    role: str = "user"


class UserCreate(UserBase):
    pass


class User(UserBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentBase(BaseModel):
    url: str
    document_type: Literal["tos", "pp"]
    retrieved_url: str
    company_name: Optional[str] = None
    logo_url: Optional[str] = None
    views: int = 0
    raw_text: str = ""
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    word_frequencies: Optional[List[Dict[str, Any]]] = None
    text_mining_metrics: Optional[Dict[str, Any]] = None


class DocumentCreate(DocumentBase):
    pass


class Document(DocumentBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SubmissionBase(BaseModel):
    user_id: Optional[str] = None
    document_id: Optional[str] = None
    requested_url: str
    document_type: Literal["tos", "pp"]
    status: str = "pending"
    error_message: Optional[str] = None


class SubmissionCreate(SubmissionBase):
    pass


class Submission(SubmissionBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class Stats(BaseModel):
    """Model for document statistics."""
    id: str = "global_stats"  # We'll use a single document for all stats
    tos_count: int = 0
    pp_count: int = 0
    total_count: int = 0
    last_updated: datetime = Field(default_factory=datetime.now) 