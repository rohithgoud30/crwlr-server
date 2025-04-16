from pydantic import BaseModel
from typing import Optional, Dict, Any

class PrivacyRequest(BaseModel):
    url: str

class PrivacyResponse(BaseModel):
    url: str
    pp_url: Optional[str] = None
    success: bool
    message: str
    method_used: str 