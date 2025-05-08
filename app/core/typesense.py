import typesense
import os
from typing import Optional
import logging

# Setup logger
logger = logging.getLogger(__name__)

# Debug environment variables
logger.info("Checking Typesense Environment Variables...")
logger.info(f"TYPESENSE_HOST from env: {os.getenv('TYPESENSE_HOST', 'not set')}")
logger.info(f"TYPESENSE_PORT from env: {os.getenv('TYPESENSE_PORT', 'not set')}")
logger.info(f"TYPESENSE_PROTOCOL from env: {os.getenv('TYPESENSE_PROTOCOL', 'not set')}")
logger.info(f"TYPESENSE_API_KEY from env: {os.getenv('TYPESENSE_API_KEY', 'not set')[:3] + '***' if os.getenv('TYPESENSE_API_KEY') else 'not set'}")

# Typesense configuration
TYPESENSE_HOST = os.getenv("TYPESENSE_HOST", "localhost")
TYPESENSE_PORT = os.getenv("TYPESENSE_PORT", "8108")
TYPESENSE_PROTOCOL = os.getenv("TYPESENSE_PROTOCOL", "http")
TYPESENSE_API_KEY = os.getenv("TYPESENSE_API_KEY", "")
TYPESENSE_COLLECTION_NAME = "documents"

# Hardcoded values as fallback
if not TYPESENSE_API_KEY:
    logger.warning("TYPESENSE_API_KEY not found in environment, using hardcoded values for Typesense")
    TYPESENSE_HOST = "509bju8l6t7kergmp-1.a1.typesense.net"
    TYPESENSE_PORT = "443"
    TYPESENSE_PROTOCOL = "https"
    TYPESENSE_API_KEY = "Tz29Eu320gLRS1sLXXTodvTdSAtkDuNT"
    logger.info(f"Using hardcoded Typesense host: {TYPESENSE_HOST}")

# Create configuration for Typesense client
typesense_config = {
    'api_key': TYPESENSE_API_KEY,
    'nodes': [
        {
            'host': TYPESENSE_HOST,
            'port': TYPESENSE_PORT,
            'protocol': TYPESENSE_PROTOCOL
        }
    ],
    'connection_timeout_seconds': 2
}

# Document collection schema
documents_schema = {
    'name': TYPESENSE_COLLECTION_NAME,
    'fields': [
        {'name': 'id', 'type': 'string'},
        {'name': 'url', 'type': 'string'},
        {'name': 'document_type', 'type': 'string', 'facet': True},
        {'name': 'company_name', 'type': 'string', 'sort': True, 'optional': True},
        {'name': 'content', 'type': 'string', 'optional': True},
        {'name': 'summary', 'type': 'string', 'optional': True},
        {'name': 'views', 'type': 'int32'},
        {'name': 'logo_url', 'type': 'string', 'optional': True},
        {'name': 'updated_at', 'type': 'int64', 'sort': True, 'optional': True}  # Store as Unix timestamp
    ],
    'default_sorting_field': 'views'
}

client: Optional[typesense.Client] = None

def init_typesense():
    """Initialize Typesense client and create collection if it doesn't exist."""
    global client
    
    if not TYPESENSE_API_KEY:
        logger.warning("TYPESENSE_API_KEY not set. Typesense search will not be available.")
        return None
    
    try:
        client = typesense.Client(typesense_config)
        create_new_collection = True
        
        try:
            # Check if collection exists
            collections = client.collections.retrieve()
            for collection in collections:
                if collection['name'] == TYPESENSE_COLLECTION_NAME:
                    logger.info(f"Found existing Typesense collection: {TYPESENSE_COLLECTION_NAME}")
                    
                    try:
                        # Try a simple search to verify the collection is working
                        test_search = client.collections[TYPESENSE_COLLECTION_NAME].documents.search({
                            'q': '*',
                            'query_by': 'company_name',
                            'per_page': 1
                        })
                        logger.info(f"Typesense collection {TYPESENSE_COLLECTION_NAME} exists and is working")
                        create_new_collection = False
                    except Exception as search_err:
                        logger.warning(f"Collection exists but search failed: {str(search_err)}")
                        logger.warning("Will drop and recreate collection")
                        
                        try:
                            client.collections[TYPESENSE_COLLECTION_NAME].delete()
                            logger.info(f"Successfully deleted collection: {TYPESENSE_COLLECTION_NAME}")
                        except Exception as del_err:
                            logger.error(f"Error deleting collection: {str(del_err)}")
                            # Continue anyway, as we'll try to recreate it
                    break
        except Exception as coll_err:
            logger.warning(f"Error retrieving collections: {str(coll_err)}")
            # Continue to attempt creating a new collection
        
        # Create new collection if needed
        if create_new_collection:
            try:
                # First try to delete if it exists (in case we couldn't check properly)
                try:
                    client.collections[TYPESENSE_COLLECTION_NAME].delete()
                    logger.info(f"Deleted existing collection before recreation: {TYPESENSE_COLLECTION_NAME}")
                except:
                    # Ignore errors here - the collection might not exist
                    pass
                
                # Now create a fresh collection
                client.collections.create(documents_schema)
                logger.info(f"Created new Typesense collection: {TYPESENSE_COLLECTION_NAME}")
            except Exception as create_err:
                logger.error(f"Error creating collection: {str(create_err)}")
                return None
        
        logger.info("Typesense client initialized successfully")
        return client
    except Exception as e:
        logger.error(f"Error initializing Typesense: {str(e)}")
        client = None
        return None

def get_typesense_client():
    """Get Typesense client instance, initialize if needed."""
    global client
    if client is None:
        client = init_typesense()
    return client 