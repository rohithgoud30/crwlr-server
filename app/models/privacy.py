from pydantic import BaseModel
from typing import Optional

class PrivacyRequest(BaseModel):
    url: str

class PrivacyResponse(BaseModel):
    url: str
    privacy_url: Optional[str] = None
    success: bool
    message: str
    method_used: str 