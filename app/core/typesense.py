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
        {'name': 'company_name', 'type': 'string', 'optional': True},
        {'name': 'content', 'type': 'string', 'optional': True},
        {'name': 'summary', 'type': 'string', 'optional': True},
<<<<<<< HEAD
        {'name': 'views', 'type': 'int32'},
=======
        {'name': 'views', 'type': 'int32', 'optional': True},
>>>>>>> 1b5d93d (Fix(typesense): add hardcoded fallback for Typesense credentials)
        {'name': 'logo_url', 'type': 'string', 'optional': True},
        {'name': 'updated_at', 'type': 'int64', 'optional': True}  # Store as Unix timestamp
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
        
        # Check if collection exists, if not create it
        collections = client.collections.retrieve()
        collection_exists = False
        
        for collection in collections:
            if collection['name'] == TYPESENSE_COLLECTION_NAME:
                collection_exists = True
                break
        
        if not collection_exists:
            client.collections.create(documents_schema)
            logger.info(f"Created Typesense collection: {TYPESENSE_COLLECTION_NAME}")
        
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