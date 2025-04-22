from pydantic import BaseModel
from typing import Optional

class ToSRequest(BaseModel):
    url: str

class ToSResponse(BaseModel):
    url: str
    tos_url: Optional[str] = None
    success: bool
    message: str
    method_used: str 