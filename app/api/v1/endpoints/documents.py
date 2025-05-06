from fastapi import APIRouter, Depends, HTTPException, Query, Path
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID
from datetime import datetime
import json

from app.core.auth import get_api_key
from app.models.database import Document
from app.crud.document import document_crud
from pydantic import BaseModel, Field

router = APIRouter()

# Define the structure for items in the search/list response
class DocumentListItem(BaseModel):
    id: str  # Changed from UUID to str to support Firestore document IDs
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
    sort_by: str = "company_name"
    sort_order: Literal["asc", "desc"] = "asc"


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
        query=search_request.search_text, 
        document_type=search_request.document_type,
        page=search_request.page,
        per_page=search_request.per_page
    )
    return results


class DocumentCountResponse(BaseModel):
    tos_count: int
    pp_count: int
    total_count: int


@router.get("/documents/stats", response_model=DocumentCountResponse)
async def get_document_counts(
    api_key: str = Depends(get_api_key)
):
    """
    Get total counts of ToS and Privacy Policy documents.
    
    Returns:
    - **tos_count**: Total number of Terms of Service documents
    - **pp_count**: Total number of Privacy Policy documents
    - **total_count**: Total number of all documents
    """
    counts = await document_crud.get_document_counts()
    return counts


# Use the full Document model for fetching a single document
@router.get("/documents/{document_id}", response_model=Document)
async def get_document(
    document_id: str = Path(...),  # Changed from UUID to str
    api_key: str = Depends(get_api_key)
):
    """
    Get a specific document by ID and increment its view counter.
    Returns the full document details.
    
    - **document_id**: ID of the document to retrieve
    """
    try:
        # Get the document
        document = await document_crud.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # For debugging
        print(f"Document found: {document}")
        
        # Parse JSON fields if they are strings
        if document.get('word_frequencies') and isinstance(document['word_frequencies'], str):
            try:
                document['word_frequencies'] = json.loads(document['word_frequencies'])
            except json.JSONDecodeError:
                document['word_frequencies'] = []
                
        if document.get('text_mining_metrics') and isinstance(document['text_mining_metrics'], str):
            try:
                document['text_mining_metrics'] = json.loads(document['text_mining_metrics'])
            except json.JSONDecodeError:
                document['text_mining_metrics'] = {}
        
        try:
            # Increment the view counter
            updated_document = await document_crud.increment_views(document_id)
            if not updated_document:
                # If increment_views fails, just return the original document
                print("View increment failed, using original document")
                return Document(**document)
            
            # Parse JSON fields for updated document if needed
            if updated_document.get('word_frequencies') and isinstance(updated_document['word_frequencies'], str):
                try:
                    updated_document['word_frequencies'] = json.loads(updated_document['word_frequencies'])
                except json.JSONDecodeError:
                    updated_document['word_frequencies'] = []
                    
            if updated_document.get('text_mining_metrics') and isinstance(updated_document['text_mining_metrics'], str):
                try:
                    updated_document['text_mining_metrics'] = json.loads(updated_document['text_mining_metrics'])
                except json.JSONDecodeError:
                    updated_document['text_mining_metrics'] = {}
            
            # Return the updated document
            return Document(**updated_document)
        except Exception as view_error:
            print(f"Error incrementing view: {str(view_error)}")
            # If there's an error incrementing views, still return the document
            return Document(**document)
    except Exception as e:
        print(f"Error retrieving document: {str(e)}")
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/documents/{document_id}", response_model=dict)
async def delete_document(
    document_id: str = Path(..., description="The ID of the document to delete"),  # Changed from UUID to str
    api_key: str = Depends(get_api_key)
):
    """
    Delete a document by ID.
    
    - **document_id**: ID of the document to delete
    
    Returns:
    - **success**: Boolean indicating whether deletion was successful
    - **message**: Status message
    """
    try:
        # Check if document exists first
        document = await document_crud.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Delete the document
        success = await document_crud.delete_document(document_id)
        
        if success:
            return {"success": True, "message": "Document deleted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete document")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") 