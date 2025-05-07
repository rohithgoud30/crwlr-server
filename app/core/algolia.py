import os
import logging
from typing import Optional, Dict, List, Any
from algoliasearch.search.client import SearchClient, SearchClientAsync
from algoliasearch.search.models import IndexSettings
from app.core.config import settings
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AlgoliaConfig:
    """Algolia configuration wrapper class."""
    
    ALGOLIA_APP_ID: Optional[str] = None
    ALGOLIA_API_KEY: Optional[str] = None
    ALGOLIA_INDEX_NAME_TOS: str = "tos_documents"
    ALGOLIA_INDEX_NAME_PP: str = "pp_documents"
    
    @classmethod
    def initialize(cls):
        """Initialize Algolia settings from environment variables."""
        # Try to get credentials from environment variables
        cls.ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID") or settings.ALGOLIA_APP_ID
        cls.ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY") or settings.ALGOLIA_API_KEY
        
        if not cls.ALGOLIA_APP_ID or not cls.ALGOLIA_API_KEY:
            logger.warning("Algolia credentials not set. Algolia search will not be available.")
            return False
        
        logger.info(f"Algolia credentials loaded. App ID: {cls.ALGOLIA_APP_ID[:5]}...")
        return True
    
    @classmethod
    def get_client(cls) -> Optional[SearchClient]:
        """Get Algolia search client."""
        if not cls.ALGOLIA_APP_ID or not cls.ALGOLIA_API_KEY:
            if not cls.initialize():
                return None
        
        try:
            client = SearchClient(cls.ALGOLIA_APP_ID, cls.ALGOLIA_API_KEY)
            return client
        except Exception as e:
            logger.error(f"Error creating Algolia client: {e}")
            return None
    
    @classmethod
    async def get_async_client(cls) -> Optional[SearchClientAsync]:
        """Get Algolia async search client."""
        if not cls.ALGOLIA_APP_ID or not cls.ALGOLIA_API_KEY:
            if not cls.initialize():
                return None
        
        try:
            client = SearchClientAsync(cls.ALGOLIA_APP_ID, cls.ALGOLIA_API_KEY)
            return client
        except Exception as e:
            logger.error(f"Error creating Algolia async client: {e}")
            return None
    
    @classmethod
    def get_index_name(cls, document_type: str) -> str:
        """Get the appropriate index name based on document type."""
        if document_type.lower() == "tos":
            return cls.ALGOLIA_INDEX_NAME_TOS
        elif document_type.lower() == "pp":
            return cls.ALGOLIA_INDEX_NAME_PP
        else:
            logger.warning(f"Unknown document type: {document_type}. Using TOS index as fallback.")
            return cls.ALGOLIA_INDEX_NAME_TOS

# Initialize Algolia settings
algolia_config = AlgoliaConfig()
algolia_config.initialize()

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
    client = await algolia_config.get_async_client()
    if not client:
        logger.error("Failed to create Algolia client. Document not indexed.")
        return False
    
    try:
        # Prepare document for Algolia
        algolia_doc = await prepare_document_for_algolia(document)
        
        # Get the appropriate index
        index_name = algolia_config.get_index_name(document.get("document_type", "tos"))
        
        # Index the document
        response = await client.save_object(
            index_name=index_name,
            body=algolia_doc
        )
        
        # Wait for the indexing task to complete
        await client.wait_for_task(
            index_name=index_name,
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
    client = await algolia_config.get_async_client()
    if not client:
        logger.error("Failed to create Algolia client. Search not performed.")
        return {"hits": [], "nbHits": 0}
    
    try:
        # Determine which indices to search
        indices = []
        if document_type:
            # Search only the specified document type
            indices.append(algolia_config.get_index_name(document_type))
        else:
            # Search both document types
            indices.append(algolia_config.get_index_name("tos"))
            indices.append(algolia_config.get_index_name("pp"))
        
        # Prepare search requests
        requests = []
        for index_name in indices:
            request = {
                "indexName": index_name,
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
    client = await algolia_config.get_async_client()
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
        await client.update_index_settings(
            index_name=algolia_config.ALGOLIA_INDEX_NAME_TOS,
            settings=index_settings.to_dict()
        )
        
        # Apply settings to PP index
        await client.update_index_settings(
            index_name=algolia_config.ALGOLIA_INDEX_NAME_PP,
            settings=index_settings.to_dict()
        )
        
        logger.info("Successfully configured Algolia indices")
        return True
    
    except Exception as e:
        logger.error(f"Error configuring Algolia indices: {e}", exc_info=True)
        return False 