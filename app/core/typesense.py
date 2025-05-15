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
SUBMISSIONS_COLLECTION_NAME = "submissions"

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
    'connection_timeout_seconds': 10
}

# Document collection schema
documents_schema = {
    'name': TYPESENSE_COLLECTION_NAME,
    'fields': [
        {'name': 'id', 'type': 'string'},
        {'name': 'url', 'type': 'string'},
        {'name': 'title', 'type': 'string', 'optional': True},
        {'name': 'content', 'type': 'string'},
        {'name': 'summary', 'type': 'string', 'optional': True},
        {'name': 'document_type', 'type': 'string'},
        {'name': 'created_at', 'type': 'int64'},
        {'name': 'updated_at', 'type': 'int64'}
    ]
}

# Submissions collection schema
submissions_schema = {
    'name': SUBMISSIONS_COLLECTION_NAME,
    'fields': [
        {'name': 'id', 'type': 'string'},
        {'name': 'url', 'type': 'string'},
        {'name': 'document_type', 'type': 'string'},
        {'name': 'status', 'type': 'string'},
        {'name': 'document_id', 'type': 'string', 'optional': True},
        {'name': 'error_message', 'type': 'string', 'optional': True},
        {'name': 'user_email', 'type': 'string', 'facet': True},
        {'name': 'created_at', 'type': 'int64'},
        {'name': 'updated_at', 'type': 'int64'}
    ]
}

client = None

def init_typesense():
    """Initialize Typesense client and create collections if they don't exist."""
    global client
    
    if not TYPESENSE_API_KEY:
        logger.warning("TYPESENSE_API_KEY not set. Typesense search will not be available.")
        return None
    
    try:
        client = typesense.Client(typesense_config)
        
        # Initialize both collections
        collections_to_init = [
            (TYPESENSE_COLLECTION_NAME, documents_schema),
            (SUBMISSIONS_COLLECTION_NAME, submissions_schema)
        ]
        
        for collection_name, schema in collections_to_init:
            create_new_collection = True
            
            try:
                # Check if collection exists
                client.collections[collection_name].retrieve()
                create_new_collection = False
                logger.info(f"Collection {collection_name} already exists")
            except Exception as e:
                if "not found" in str(e).lower():
                    create_new_collection = True
                else:
                    logger.error(f"Error checking collection {collection_name}: {str(e)}")
                    continue
            
            if create_new_collection:
                try:
                    client.collections.create(schema)
                    logger.info(f"Created new collection: {collection_name}")
                except Exception as e:
                    logger.error(f"Error creating collection {collection_name}: {str(e)}")
        
        return client
    except Exception as e:
        logger.error(f"Error initializing Typesense: {str(e)}")
        return None

def get_typesense_client():
    """Get Typesense client instance, initialize if needed."""
    global client
    if client is None:
        client = init_typesense()
    return client 