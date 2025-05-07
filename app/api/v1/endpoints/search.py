from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from typing import Optional, List, Dict, Any
import logging
from app.core.algolia import search_documents, configure_indices, algolia_config, index_document
from app.crud.document import document_crud
from pydantic import BaseModel

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

class SearchQuery(BaseModel):
    """Model for search query."""
    query: str
    document_type: Optional[str] = None
    page: int = 1
    per_page: int = 20
    sort_by: Optional[str] = "updated_at"
    sort_order: Optional[str] = "desc"

class SearchResponse(BaseModel):
    """Model for search response."""
    items: List[Dict[str, Any]]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_next: bool
    has_prev: bool
    search_provider: str

@router.post("/search", response_model=SearchResponse)
async def search(query: SearchQuery) -> SearchResponse:
    """
    Search for documents using Algolia or Firebase.
    
    This endpoint attempts to use Algolia for faster search results.
    If Algolia is not available, it falls back to Firebase.
    
    Args:
        query: Search query parameters
        
    Returns:
        SearchResponse with results
    """
    results = await document_crud.search_documents(
        query=query.query,
        document_type=query.document_type,
        page=query.page,
        per_page=query.per_page,
        sort_by=query.sort_by,
        sort_order=query.sort_order
    )
    
    return SearchResponse(
        items=results.get("items", []),
        total=results.get("total", 0),
        page=results.get("page", 1),
        per_page=results.get("per_page", 10),
        total_pages=results.get("total_pages", 0),
        has_next=results.get("has_next", False),
        has_prev=results.get("has_prev", False),
        search_provider=results.get("search_provider", "unknown")
    )

@router.get("/search", response_model=SearchResponse)
async def search_get(
    query: str = Query(..., description="Search query"),
    document_type: Optional[str] = Query(None, description="Filter by document type (tos or pp)"),
    page: int = Query(1, description="Page number (1-indexed)"),
    per_page: int = Query(20, description="Results per page"),
    sort_by: Optional[str] = Query("updated_at", description="Field to sort by"),
    sort_order: Optional[str] = Query("desc", description="Sort direction (asc or desc)")
) -> SearchResponse:
    """
    Search for documents using GET method.
    
    This endpoint uses the same search functionality as the POST method
    but allows for simpler integration with browser searches.
    
    Args:
        query: Search query
        document_type: Optional filter by document type
        page: Page number (1-indexed)
        per_page: Results per page
        sort_by: Field to sort by
        sort_order: Sort direction
        
    Returns:
        SearchResponse with results
    """
    results = await document_crud.search_documents(
        query=query,
        document_type=document_type,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order
    )
    
    return SearchResponse(
        items=results.get("items", []),
        total=results.get("total", 0),
        page=results.get("page", 1),
        per_page=results.get("per_page", 10),
        total_pages=results.get("total_pages", 0),
        has_next=results.get("has_next", False),
        has_prev=results.get("has_prev", False),
        search_provider=results.get("search_provider", "unknown")
    )

@router.post("/configure-algolia")
async def configure_algolia_indices():
    """
    Configure Algolia indices with optimal settings for search.
    
    This endpoint should be called once to set up Algolia indices when:
    1. Setting up a new environment
    2. After changing search configuration
    
    Returns:
        Dict with status message
    """
    # Check if Algolia credentials are configured
    if not algolia_config.ALGOLIA_APP_ID or not algolia_config.ALGOLIA_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Algolia is not configured. Please set ALGOLIA_APP_ID and ALGOLIA_API_KEY environment variables."
        )
    
    # Configure indices
    success = await configure_indices()
    
    if success:
        return {"status": "success", "message": "Algolia indices configured successfully"}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to configure Algolia indices. Check server logs for details."
        )

@router.get("/algolia-status")
async def get_algolia_status():
    """
    Check if Algolia is configured and available.
    
    Returns:
        Dict with Algolia status information
    """
    # Check if Algolia credentials are configured
    is_configured = (
        algolia_config.ALGOLIA_APP_ID is not None and 
        algolia_config.ALGOLIA_API_KEY is not None
    )
    
    # Return status information
    return {
        "is_configured": is_configured,
        "app_id_configured": algolia_config.ALGOLIA_APP_ID is not None,
        "api_key_configured": algolia_config.ALGOLIA_API_KEY is not None,
        "tos_index": algolia_config.ALGOLIA_INDEX_NAME_TOS,
        "pp_index": algolia_config.ALGOLIA_INDEX_NAME_PP
    }

# Helper function to index all documents in background
async def index_all_documents_background(document_type: Optional[str] = None):
    """
    Index all documents to Algolia in the background.
    
    Args:
        document_type: Optional filter by document type
    """
    try:
        # Get all documents
        if document_type:
            # Get documents of specific type
            if document_type.lower() == "tos":
                query = document_crud.collection.where("document_type", "==", "tos")
            elif document_type.lower() == "pp":
                query = document_crud.collection.where("document_type", "==", "pp")
            else:
                logger.warning(f"Unknown document type for indexing: {document_type}")
                return
        else:
            # Get all documents
            query = document_crud.collection
        
        # Stream all documents
        docs = query.stream()
        count = 0
        success_count = 0
        
        # Process each document
        for doc_snapshot in docs:
            count += 1
            doc_data = doc_snapshot.to_dict()
            doc_data["id"] = doc_snapshot.id
            
            # Index the document in Algolia
            try:
                success = await index_document(doc_data)
                if success:
                    success_count += 1
                    
                # Log progress for every 10 documents
                if count % 10 == 0:
                    logger.info(f"Indexed {count} documents so far ({success_count} successful)")
            except Exception as e:
                logger.error(f"Error indexing document {doc_snapshot.id}: {e}")
        
        logger.info(f"Completed indexing {count} documents to Algolia ({success_count} successful)")
    except Exception as e:
        logger.error(f"Error during background indexing: {e}")

@router.post("/index-all-documents")
async def index_all_documents(
    background_tasks: BackgroundTasks,
    document_type: Optional[str] = Query(None, description="Filter by document type (tos or pp)")
):
    """
    Start a background task to index all documents to Algolia.
    
    This is useful when:
    1. Setting up Algolia for the first time
    2. After making changes to document structure
    3. If Algolia indices get out of sync with Firebase
    
    Args:
        document_type: Optional filter by document type
        
    Returns:
        Dict with status message
    """
    # Check if Algolia credentials are configured
    if not algolia_config.ALGOLIA_APP_ID or not algolia_config.ALGOLIA_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Algolia is not configured. Please set ALGOLIA_APP_ID and ALGOLIA_API_KEY environment variables."
        )
    
    # Add background task to index all documents
    background_tasks.add_task(index_all_documents_background, document_type)
    
    # Return immediate response
    return {
        "status": "success", 
        "message": f"Started background task to index {'all' if not document_type else document_type} documents to Algolia"
    } 