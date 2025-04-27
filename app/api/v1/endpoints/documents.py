from fastapi import APIRouter, Depends, HTTPException, Query, Path
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID
from datetime import datetime

from app.core.auth import get_api_key
from app.models.database import Document
from app.crud.document import document_crud
from pydantic import BaseModel, Field

router = APIRouter()

# Define the structure for items in the search/list response
class DocumentListItem(BaseModel):
    id: UUID
    url: str
    document_type: Literal["tos", "pp"]
    company_name: Optional[str] = None
    logo_url: Optional[str] = None  # Added logo_url, removed retrieved_url
    views: int = 0
    updated_at: datetime  # Removed created_at, kept only updated_at

    class Config:
        from_attributes = True


class DocumentSearchResponse(BaseModel):
    items: List[DocumentListItem]  # Use the new item model
    total: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool


class DocumentSearchRequest(BaseModel):
    search_text: str  # Changed to required
    document_type: Optional[Literal["tos", "pp"]] = None
    page: int = Field(1, ge=1)
    per_page: int = Field(6, ge=1, le=100)
    sort_by: str = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"


@router.post("/documents/search", response_model=DocumentSearchResponse)
async def search_documents(
    search_request: DocumentSearchRequest,
    api_key: str = Depends(get_api_key)
):
    """
    Search for documents using text, which can be a keyword, URL, or company name.
    Includes filtering and pagination.
    Returns a limited set of fields for each document in the list.
    
    - **search_text**: Text to search for (keywords, URL, company name)
    - **document_type**: Optional filter by document type ("tos" or "pp")
    - **page**: Page number (starting from 1)
    - **per_page**: Number of items per page (default: 6, max: 100)
    - **sort_by**: Field to sort by (e.g., "created_at", "views", "company_name")
    - **sort_order**: Sort direction ("asc" or "desc")
    """
    # Always use the general search function which covers text, url, and company name fields
    results = await document_crud.search_documents(
        search_text=search_request.search_text, 
        document_type=search_request.document_type,
        page=search_request.page,
        per_page=search_request.per_page,
        sort_by=search_request.sort_by,
        sort_order=search_request.sort_order
    )
    return results


@router.get("/documents", response_model=DocumentSearchResponse)
async def get_documents(
    document_type: Optional[Literal["tos", "pp"]] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(6, ge=1, le=100),
    sort_by: str = Query("created_at"),
    sort_order: Literal["asc", "desc"] = Query("desc"),
    api_key: str = Depends(get_api_key)
):
    """
    Get a paginated list of all documents with optional filtering by document type.
    Returns a limited set of fields for each document in the list.
    
    - **document_type**: Optional filter by document type ("tos" or "pp")
    - **page**: Page number (starting from 1)
    - **per_page**: Number of items per page (default: 6, max: 100)
    - **sort_by**: Field to sort by (e.g., "created_at", "views", "company_name")
    - **sort_order**: Sort direction ("asc" or "desc")
    """
    results = await document_crud.get_documents(
        page=page,
        per_page=per_page,
        document_type=document_type,
        order_by=sort_by,
        order_direction=sort_order
    )
    return results


# Use the full Document model for fetching a single document
@router.get("/documents/{document_id}", response_model=Document)
async def get_document(
    document_id: UUID = Path(...),
    api_key: str = Depends(get_api_key)
):
    """
    Get a specific document by ID and increment its view counter.
    Returns the full document details.
    
    - **document_id**: UUID of the document to retrieve
    """
    document = await document_crud.get(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Increment the view counter - get returns the full doc, increment also returns full doc
    updated_document = await document_crud.increment_views(document_id)
    # Ensure the returned object matches the response model
    if updated_document:
        return Document(**updated_document)
    else: # Should not happen if get worked, but handle gracefully
        raise HTTPException(status_code=404, detail="Document not found after view increment attempt") 