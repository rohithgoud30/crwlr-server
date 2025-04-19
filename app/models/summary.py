from pydantic import BaseModel, Field
from typing import Optional, Literal

class SummaryRequest(BaseModel):
    text: str = Field(..., description="Text content to summarize")
    document_type: Literal["Privacy Policy", "Terms of Service"] = Field(..., 
                          description="Type of document being summarized")

class SummaryResponse(BaseModel):
    success: bool
    document_type: str
    one_sentence_summary: Optional[str] = None
    hundred_word_summary: Optional[str] = None
    message: str