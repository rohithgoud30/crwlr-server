from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class AlgoliaDocumentModel(BaseModel):
    """
    Model for documents to be indexed in Algolia.
    Includes configuration to allow arbitrary types.
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow"
    )
    
    objectID: str = Field(..., description="Unique identifier for the document")
    url: str = Field(..., description="Original URL requested by the user")
    retrieved_url: Optional[str] = Field(None, description="Actual URL content was retrieved from")
    document_type: str = Field(..., description="Type of document (tos or pp)")
    company_name: str = Field(..., description="Name of the company")
    logo_url: Optional[str] = Field(None, description="URL to the company logo")
    views: int = Field(0, description="Number of times the document has been viewed")
    created_at: Any = Field(..., description="Creation timestamp")
    updated_at: Any = Field(..., description="Last update timestamp")
    one_sentence_summary: Optional[str] = Field(None, description="One-sentence summary of the document")
    hundred_word_summary: Optional[str] = Field(None, description="Hundred-word summary of the document")

class AlgoliaSearchParams(BaseModel):
    """
    Model for Algolia search parameters.
    Includes configuration to allow arbitrary types.
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow"
    )
    
    query: str = Field(..., description="Search query")
    doc_type: Optional[str] = Field(None, description="Document type to filter by (tos or pp)")
    limit: int = Field(10, description="Maximum number of results to return")
    page: int = Field(1, description="Page number for pagination")

class AlgoliaSearchResponse(BaseModel):
    """
    Model for Algolia search response.
    Includes configuration to allow arbitrary types.
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow"
    )
    
    hits: List[Dict[str, Any]] = Field([], description="Search results")
    nbHits: int = Field(0, description="Total number of results")
    page: int = Field(0, description="Current page number")
    nbPages: int = Field(0, description="Total number of pages")
    hitsPerPage: int = Field(0, description="Number of hits per page")
    query: str = Field("", description="Search query used") 