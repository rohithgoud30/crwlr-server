import os
import logging
from typing import Optional, Dict, List, Any
from algoliasearch.search.client import SearchClient, SearchClientAsync
from algoliasearch.search.models import IndexSettings
from app.core.config import settings
from datetime import datetime
from algoliasearch.search_client import SearchClient

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Algolia client
algolia_client = None
tos_index = None
pp_index = None

def init_algolia():
    """Initialize Algolia client and indices"""
    global algolia_client, tos_index, pp_index
    
    try:
        app_id = os.getenv("ALGOLIA_APP_ID")
        api_key = os.getenv("ALGOLIA_API_KEY")
        
        if not app_id or not api_key:
            logger.warning("Algolia credentials not found in environment variables.")
            return None
            
        algolia_client = SearchClient.create(app_id, api_key)
        
        # Initialize indices
        tos_index = algolia_client.init_index('tos_documents')
        pp_index = algolia_client.init_index('pp_documents')
        
        # Configure index settings to focus on company name and URL fields
        search_settings = {
            'searchableAttributes': [
                'company_name',  # First priority 
                'url',           # Second priority
                'retrieved_url'  # Third priority
            ],
            'attributesForFaceting': ['company_name', 'document_type'],
            'customRanking': ['desc(views)'],
            'attributesToRetrieve': [
                'objectID',
                'company_name',
                'url',
                'retrieved_url',
                'logo_url',
                'document_type',
                'views',
                'created_at',
                'updated_at',
                'one_sentence_summary',
                'hundred_word_summary'
            ]
        }
        
        # Apply same settings to both indices
        tos_index.set_settings(search_settings)
        pp_index.set_settings(search_settings)
        
        logger.info("Algolia client and indices initialized with focus on company name and URL fields")
        return algolia_client
    except Exception as e:
        logger.error(f"Error initializing Algolia client: {e}")
        return None

def save_document_to_algolia(doc_id: str, document: Dict[str, Any]) -> bool:
    """
    Save a document to the appropriate Algolia index.
    
    Args:
        doc_id: Document ID
        document: Document data
        
    Returns:
        True if successful, False otherwise
    """
    global tos_index, pp_index
    
    try:
        if not tos_index or not pp_index:
            if not init_algolia():
                logger.warning("Cannot save to Algolia - client not initialized")
                return False
        
        # Prepare document for Algolia
        doc_type = document.get('document_type', '').lower()
        
        # Choose the right index
        index = None
        if doc_type == 'tos':
            index = tos_index
        elif doc_type == 'pp':
            index = pp_index
        else:
            logger.warning(f"Unknown document type '{doc_type}' - cannot save to Algolia")
            return False
            
        # Prepare the document (Algolia requires objectID)
        algolia_doc = document.copy()
        algolia_doc['objectID'] = doc_id
        
        # Convert datetime objects to ISO strings for Algolia
        for field in ['created_at', 'updated_at']:
            if field in algolia_doc and isinstance(algolia_doc[field], datetime):
                algolia_doc[field] = algolia_doc[field].isoformat()
        
        # Save to Algolia
        index.save_object(algolia_doc)
        logger.info(f"Document {doc_id} saved to Algolia index {index.name}")
        return True
    except Exception as e:
        logger.error(f"Error saving document to Algolia: {e}")
        return False

def batch_save_documents(documents: List[Dict[str, Any]]) -> Dict[str, int]:
    """
    Save multiple documents to Algolia in batch mode.
    
    Args:
        documents: List of documents with their IDs
        
    Returns:
        Dictionary with count of successful/failed operations
    """
    global tos_index, pp_index
    
    if not tos_index or not pp_index:
        if not init_algolia():
            logger.warning("Cannot batch save to Algolia - client not initialized")
            return {"success": 0, "failed": len(documents)}
    
    # Separate documents by type
    tos_docs = []
    pp_docs = []
    unknown_count = 0
    
    for doc in documents:
        doc_id = doc.get('id')
        doc_type = doc.get('document_type', '').lower()
        
        if not doc_id:
            unknown_count += 1
            continue
            
        # Prepare for Algolia (needs objectID)
        algolia_doc = doc.copy()
        algolia_doc['objectID'] = doc_id
        
        # Convert datetime objects to ISO strings
        for field in ['created_at', 'updated_at']:
            if field in algolia_doc and isinstance(algolia_doc[field], datetime):
                algolia_doc[field] = algolia_doc[field].isoformat()
        
        # Add to appropriate batch
        if doc_type == 'tos':
            tos_docs.append(algolia_doc)
        elif doc_type == 'pp':
            pp_docs.append(algolia_doc)
        else:
            unknown_count += 1
    
    # Track results
    results = {
        "success": 0,
        "failed": unknown_count  # Start with unknown types as failed
    }
    
    # Send batches to Algolia
    try:
        if tos_docs:
            tos_index.save_objects(tos_docs)
            results["success"] += len(tos_docs)
            logger.info(f"Batch saved {len(tos_docs)} TOS documents to Algolia")
    except Exception as e:
        logger.error(f"Error batch saving TOS documents to Algolia: {e}")
        results["failed"] += len(tos_docs)
    
    try:
        if pp_docs:
            pp_index.save_objects(pp_docs)
            results["success"] += len(pp_docs)
            logger.info(f"Batch saved {len(pp_docs)} PP documents to Algolia")
    except Exception as e:
        logger.error(f"Error batch saving PP documents to Algolia: {e}")
        results["failed"] += len(pp_docs)
    
    return results

def search_documents(query: str, doc_type: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """
    Search for documents in Algolia.
    
    Focuses the search on company name and URL fields.
    
    Args:
        query: Search query
        doc_type: Optional document type to filter by
        limit: Maximum number of results to return
        
    Returns:
        List of matching documents
    """
    global tos_index, pp_index
    
    try:
        if not tos_index or not pp_index:
            if not init_algolia():
                logger.warning("Cannot search Algolia - client not initialized")
                return []
        
        # Determine which indices to search
        indices = []
        if not doc_type or doc_type.lower() == 'tos':
            indices.append(tos_index)
        if not doc_type or doc_type.lower() == 'pp':
            indices.append(pp_index)
            
        if not indices:
            logger.warning(f"Unknown document type '{doc_type}' - cannot search Algolia")
            return []
            
        # Search parameters focusing on company name and URL
        params = {
            'hitsPerPage': limit,
            'restrictSearchableAttributes': ['company_name', 'url', 'retrieved_url']
        }
        
        # Collect results from all indices
        all_results = []
        for index in indices:
            search_result = index.search(query, params)
            all_results.extend(search_result['hits'])
            
        # Convert Algolia objectID to id for consistency
        for item in all_results:
            item['id'] = item.get('objectID')
            
        return all_results
    except Exception as e:
        logger.error(f"Error searching Algolia: {e}")
        return []

def delete_document(doc_id: str, doc_type: str) -> bool:
    """
    Delete a document from Algolia.
    
    Args:
        doc_id: Document ID
        doc_type: Document type ('tos' or 'pp')
        
    Returns:
        True if successful, False otherwise
    """
    global tos_index, pp_index
    
    try:
        if not tos_index or not pp_index:
            if not init_algolia():
                logger.warning("Cannot delete from Algolia - client not initialized")
                return False
                
        # Choose the right index
        index = None
        if doc_type.lower() == 'tos':
            index = tos_index
        elif doc_type.lower() == 'pp':
            index = pp_index
        else:
            logger.warning(f"Unknown document type '{doc_type}' - cannot delete from Algolia")
            return False
            
        # Delete from Algolia
        index.delete_object(doc_id)
        logger.info(f"Document {doc_id} deleted from Algolia index {index.name}")
        return True
    except Exception as e:
        logger.error(f"Error deleting document from Algolia: {e}")
        return False

# Initialize Algolia at module load
init_algolia()

async def prepare_document_for_algolia(document: Dict[str, Any]) -> Dict[str, Any]:
    """
    Prepare a document for indexing in Algolia.
    
    Args:
        document: The document to prepare
        
    Returns:
        Document formatted for Algolia
    """
    # Create a new document with only the fields we want to index
    algolia_doc = {
        "objectID": document.get("id", ""),  # Required by Algolia
        "url": document.get("url", ""),
        "retrieved_url": document.get("retrieved_url", ""),
        "document_type": document.get("document_type", ""),
        "company_name": document.get("company_name", ""),
        "logo_url": document.get("logo_url", ""),
        "one_sentence_summary": document.get("one_sentence_summary", ""),
        "hundred_word_summary": document.get("hundred_word_summary", ""),
        # We only index the first 10 words for performance
        "word_frequencies": document.get("word_frequencies", [])[:10],
        # We need to convert these fields to make them indexable
        "views": document.get("views", 0),
        "created_at": document.get("created_at", datetime.now()).isoformat(),
        "updated_at": document.get("updated_at", datetime.now()).isoformat(),
    }
    
    # We don't index the full raw text as it can be very large
    # Instead, we include the summaries which are more useful for search
    
    return algolia_doc

async def index_document(document: Dict[str, Any]) -> bool:
    """
    Index a document in Algolia.
    
    Args:
        document: Document to index
        
    Returns:
        True if successful, False otherwise
    """
    client = await algolia_client
    if not client:
        logger.error("Failed to create Algolia client. Document not indexed.")
        return False
    
    try:
        # Prepare document for Algolia
        algolia_doc = await prepare_document_for_algolia(document)
        
        # Get the appropriate index
        doc_type = document.get("document_type", "tos").lower()
        index = tos_index if doc_type == "tos" else pp_index if doc_type == "pp" else None
        
        if not index:
            logger.error(f"Unknown document type: {doc_type}")
            return False
        
        # Index the document
        response = await index.save_object(algolia_doc)
        
        # Wait for the indexing task to complete
        await client.wait_for_task(
            index_name=index.index_name,
            task_id=response.task_id
        )
        
        logger.info(f"Successfully indexed document {algolia_doc['objectID']} in Algolia")
        return True
    
    except Exception as e:
        logger.error(f"Error indexing document in Algolia: {e}", exc_info=True)
        return False

async def search_documents(query: str, document_type: str = None, filters: str = None) -> Dict[str, Any]:
    """
    Search for documents in Algolia.
    
    Args:
        query: The search query
        document_type: Optional document type to filter by (tos or pp)
        filters: Optional Algolia filters string
        
    Returns:
        Search results
    """
    client = await algolia_client
    if not client:
        logger.error("Failed to create Algolia client. Search not performed.")
        return {"hits": [], "nbHits": 0}
    
    try:
        # Determine which indices to search
        indices = []
        if document_type:
            # Search only the specified document type
            doc_type = document_type.lower()
            index = tos_index if doc_type == "tos" else pp_index if doc_type == "pp" else None
            if index:
                indices.append(index)
            else:
                logger.warning(f"Unknown document type: {document_type}. Using TOS index as fallback.")
                indices.append(tos_index)
        else:
            # Search both document types
            indices.append(tos_index)
            indices.append(pp_index)
        
        # Prepare search requests
        requests = []
        for index in indices:
            request = {
                "indexName": index.index_name,
                "query": query,
                "hitsPerPage": 20,
            }
            
            # Add filters if provided
            if filters:
                request["filters"] = filters
                
            requests.append(request)
        
        # Execute multi-index search
        results = await client.search(
            search_method_params={
                "requests": requests
            }
        )
        
        # Parse and return results
        return results.to_dict()
    
    except Exception as e:
        logger.error(f"Error searching in Algolia: {e}", exc_info=True)
        return {"hits": [], "nbHits": 0}

async def configure_indices() -> bool:
    """
    Configure Algolia indices with the proper settings.
    
    Returns:
        True if successful, False otherwise
    """
    client = await algolia_client
    if not client:
        logger.error("Failed to create Algolia client. Indices not configured.")
        return False
    
    try:
        # Configure settings for both indices
        index_settings = IndexSettings(
            searchableAttributes=[
                "company_name",
                "one_sentence_summary",
                "hundred_word_summary",
                "url",
                "retrieved_url"
            ],
            attributesForFaceting=[
                "document_type",
                "company_name"
            ],
            ranking=[
                "typo",
                "geo",
                "words",
                "filters",
                "proximity",
                "attribute",
                "exact",
                "custom"
            ],
            customRanking=[
                "desc(views)",
                "desc(updated_at)"
            ],
            highlightPreTag="<mark>",
            highlightPostTag="</mark>"
        )
        
        # Apply settings to TOS index
        await tos_index.set_settings(index_settings.to_dict())
        
        # Apply settings to PP index
        await pp_index.set_settings(index_settings.to_dict())
        
        logger.info("Successfully configured Algolia indices")
        return True
    
    except Exception as e:
        logger.error(f"Error configuring Algolia indices: {e}", exc_info=True)
        return False 