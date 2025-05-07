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
        
        # Configure index settings for better search
        tos_index.set_settings({
            'searchableAttributes': [
                'company_name',
                'one_sentence_summary',
                'hundred_word_summary',
                'unordered(raw_text)'
            ],
            'attributesForFaceting': ['company_name', 'document_type'],
            'customRanking': ['desc(views)']
        })
        
        pp_index.set_settings({
            'searchableAttributes': [
                'company_name',
                'one_sentence_summary',
                'hundred_word_summary',
                'unordered(raw_text)'
            ],
            'attributesForFaceting': ['company_name', 'document_type'],
            'customRanking': ['desc(views)']
        })
        
        logger.info("Algolia client and indices initialized successfully")
        return algolia_client
    except Exception as e:
        logger.error(f"Error initializing Algolia client: {e}")
        return None

def save_document_to_algolia(document_id: str, document_data: Dict[str, Any]) -> bool:
    """
    Save a document to Algolia.
    
    Args:
        document_id: The document ID (will be used as objectID in Algolia)
        document_data: Document data to be indexed
        
    Returns:
        Boolean indicating success/failure
    """
    try:
        # Make sure client is initialized
        if not algolia_client:
            init_algolia()
            if not algolia_client:
                return False
                
        # Determine which index to use
        doc_type = document_data.get('document_type', '').lower()
        index = tos_index if doc_type == 'tos' else pp_index if doc_type == 'pp' else None
        
        if not index:
            logger.error(f"Unknown document type: {doc_type}")
            return False
            
        # Set the objectID to match our document ID
        document_data['objectID'] = document_id
        
        # Limit raw_text to avoid hitting Algolia record size limits (10KB)
        if 'raw_text' in document_data and document_data['raw_text']:
            # Truncate to ~5KB (approx 5000 chars)
            document_data['raw_text'] = document_data['raw_text'][:5000]
        
        # Save to Algolia
        index.save_object(document_data)
        logger.info(f"Document {document_id} saved to Algolia index")
        return True
    except Exception as e:
        logger.error(f"Error saving document to Algolia: {e}")
        return False

def batch_save_documents(documents: List[Dict[str, Any]], doc_type: str) -> bool:
    """
    Save multiple documents to Algolia in a batch operation.
    
    Args:
        documents: List of document data to be indexed
        doc_type: Document type ('tos' or 'pp')
        
    Returns:
        Boolean indicating success/failure
    """
    try:
        # Make sure client is initialized
        if not algolia_client:
            init_algolia()
            if not algolia_client:
                return False
                
        # Determine which index to use
        index = tos_index if doc_type.lower() == 'tos' else pp_index if doc_type.lower() == 'pp' else None
        
        if not index:
            logger.error(f"Unknown document type: {doc_type}")
            return False
            
        # Prepare documents for Algolia
        algolia_objects = []
        for doc in documents:
            # Make a copy of the document to avoid modifying the original
            algolia_doc = doc.copy()
            
            # Ensure each document has an objectID
            if 'id' in doc and 'objectID' not in algolia_doc:
                algolia_doc['objectID'] = doc['id']
                
            # Limit raw_text to avoid hitting record size limits
            if 'raw_text' in algolia_doc and algolia_doc['raw_text']:
                algolia_doc['raw_text'] = algolia_doc['raw_text'][:5000]
                
            algolia_objects.append(algolia_doc)
            
        # Save to Algolia
        index.save_objects(algolia_objects)
        logger.info(f"Batch of {len(algolia_objects)} documents saved to Algolia {doc_type} index")
        return True
    except Exception as e:
        logger.error(f"Error batch saving documents to Algolia: {e}")
        return False

def search_documents(query: str, doc_type: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search for documents in Algolia.
    
    Args:
        query: Search query string
        doc_type: Optional document type filter ('tos' or 'pp')
        limit: Maximum number of results to return
        
    Returns:
        List of matching document data
    """
    try:
        # Make sure client is initialized
        if not algolia_client:
            init_algolia()
            if not algolia_client:
                return []
                
        # If document type is specified, search only that index
        if doc_type:
            index = tos_index if doc_type.lower() == 'tos' else pp_index if doc_type.lower() == 'pp' else None
            
            if not index:
                logger.error(f"Unknown document type: {doc_type}")
                return []
                
            results = index.search(query, {
                'hitsPerPage': limit
            })
            
            return results['hits']
        else:
            # Search both indices and combine results
            tos_results = tos_index.search(query, {
                'hitsPerPage': limit // 2  # Split limit between both indices
            })
            
            pp_results = pp_index.search(query, {
                'hitsPerPage': limit // 2
            })
            
            # Combine and return results
            return tos_results['hits'] + pp_results['hits']
    except Exception as e:
        logger.error(f"Error searching documents in Algolia: {e}")
        return []

def delete_document(document_id: str, doc_type: str) -> bool:
    """
    Delete a document from Algolia.
    
    Args:
        document_id: The document ID to delete
        doc_type: Document type ('tos' or 'pp')
        
    Returns:
        Boolean indicating success/failure
    """
    try:
        # Make sure client is initialized
        if not algolia_client:
            init_algolia()
            if not algolia_client:
                return False
                
        # Determine which index to use
        index = tos_index if doc_type.lower() == 'tos' else pp_index if doc_type.lower() == 'pp' else None
        
        if not index:
            logger.error(f"Unknown document type: {doc_type}")
            return False
            
        # Delete from Algolia
        index.delete_object(document_id)
        logger.info(f"Document {document_id} deleted from Algolia")
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