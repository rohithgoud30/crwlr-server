from fastapi import APIRouter, Depends, HTTPException, Query, Path, Body
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
import json
import logging

from app.core.auth import get_api_key
from app.models.database import Document
from app.crud.document import document_crud
from app.crud.stats import stats_crud
from pydantic import BaseModel, Field

# Setup logger
logger = logging.getLogger(__name__)

router = APIRouter()

# Define the structure for items in the search/list response
class DocumentListItem(BaseModel):
    id: str
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
    Search for documents matching the provided text in company name or URL only.
    Includes filtering and pagination.
    Returns a limited set of fields for each document in the list.
    
    - **search_text**: Text to search for (only searches in company name and URL fields)
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
        per_page=search_request.per_page,
        sort_by=search_request.sort_by,
        sort_order=search_request.sort_order
    )
    return results


class DocumentCountResponse(BaseModel):
    tos_count: int
    pp_count: int
    total_count: int
    last_updated: Optional[datetime] = None


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
    - **last_updated**: When the stats were last updated
    """
    try:
        counts = await document_crud.get_document_counts()
        
        # Convert last_updated if it exists
        last_updated = counts.get("last_updated")
        
        return {
            "tos_count": counts.get("tos_count", 0),
            "pp_count": counts.get("pp_count", 0),
            "total_count": counts.get("total_count", 0),
            "last_updated": last_updated
        }
    except Exception as e:
        logger.error(f"Error getting document counts: {str(e)}")
        # Return default counts in case of an error
        return {
            "tos_count": 0,
            "pp_count": 0,
            "total_count": 0,
            "last_updated": None
        }


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


class UpdateCompanyNameRequest(BaseModel):
    company_name: str = Field(..., description="The new company name")


@router.patch("/documents/{document_id}/company-name", response_model=Document)
async def update_document_company_name(
    request: UpdateCompanyNameRequest,
    document_id: str = Path(..., description="The ID of the document to update"),
    api_key: str = Depends(get_api_key)
):
    """
    Update a document's company name by ID.
    
    - **document_id**: ID of the document to update
    - **company_name**: The new company name
    
    Returns:
    - The updated document
    """
    try:
        # Check if document exists first
        document = await document_crud.get(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Update the document's company name
        updated_document = await document_crud.update_company_name(document_id, request.company_name)
        
        if not updated_document:
            raise HTTPException(status_code=500, detail="Failed to update document company name")
        
        # Parse JSON fields if they are strings
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
        
        return Document(**updated_document)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/recount-stats", response_model=Dict[str, Any])
async def recount_stats():
    """
    Force a recount of all documents and update the stats table.
    
    This is useful when the stats table may be out of sync with the actual document counts.
    It performs a full scan of the documents collection and updates the stats with accurate counts.
    """
    logger.info("Manual stats recount triggered")
    
    try:
        recount_result = await stats_crud.force_recount_stats()
        
        if not recount_result.get("success", False):
            logger.error(f"Stats recount failed: {recount_result.get('message', 'Unknown error')}")
            return {
                "success": False,
                "message": recount_result.get("message", "Stats recount failed"),
                "counts": {
                    "tos_count": recount_result.get("tos_count", 0),
                    "pp_count": recount_result.get("pp_count", 0),
                    "total_count": recount_result.get("total_count", 0)
                },
                "timestamp": datetime.now().isoformat()
            }
            
        logger.info(f"Stats recount successful: ToS={recount_result.get('tos_count', 0)}, PP={recount_result.get('pp_count', 0)}, Total={recount_result.get('total_count', 0)}")
        
        return {
            "success": True,
            "message": "Stats recounted successfully",
            "counts": {
                "tos_count": recount_result.get("tos_count", 0),
                "pp_count": recount_result.get("pp_count", 0),
                "total_count": recount_result.get("total_count", 0)
            },
            "last_updated": recount_result.get("last_updated", datetime.now()).isoformat() if isinstance(recount_result.get("last_updated"), datetime) else recount_result.get("last_updated"),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error during stats recount: {str(e)}")
        return {
            "success": False,
            "message": f"Error during stats recount: {str(e)}",
            "timestamp": datetime.now().isoformat()
        } 