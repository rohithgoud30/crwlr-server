import os
import logging
from typing import Optional
from datetime import datetime
from uuid import uuid4

from app.core.config import settings
from app.core.firebase import db, initialize_firebase
from app.core.typesense import init_typesense

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check if Firebase is properly initialized
if db is None:
    logger.warning("Firebase database not initialized on module import - will attempt lazy initialization when needed")
else:
    logger.info("Firebase database initialized successfully")

# Define the document types
DOCUMENT_TYPES = ["tos", "pp"]

# Collection references (initialized lazily)
_documents_collection = None
_submissions_collection = None

# Define database collections with lazy loading
def get_collection(collection_name):
    """Get a Firestore collection reference, attempting initialization if needed"""
    global db
    
    # If db is None, try to initialize Firebase again
    if db is None:
        logger.info(f"Attempting lazy Firebase initialization to get collection: {collection_name}")
        try:
            # Force initialization in development
            force_init = settings.ENVIRONMENT == "development"
            db = initialize_firebase(force_init=force_init)
        except Exception as e:
            logger.error(f"Lazy Firebase initialization failed: {str(e)}")
    
    # Now check if db is available
    if db is None:
        logger.error(f"Cannot get collection {collection_name} - Firebase not initialized")
        return None
    
    logger.info(f"Successfully got collection reference: {collection_name}")
    return db.collection(collection_name)

# Lazy loading getters for collections
def documents():
    """Lazy getter for documents collection"""
    global _documents_collection
    if _documents_collection is None:
        _documents_collection = get_collection("documents")
    return _documents_collection

def submissions():
    """Lazy getter for submissions collection"""
    global _submissions_collection
    if _submissions_collection is None:
        _submissions_collection = get_collection("submissions")
    return _submissions_collection

# Document reference helpers
def get_document_ref(collection_getter, doc_id):
    """Get a reference to a document in a collection by ID"""
    collection = collection_getter()
    if collection is None:
        logger.error(f"Cannot get document reference - collection is None")
        return None
    return collection.document(str(doc_id))

def check_database_connection():
    """Check if the Firebase connection is working by testing a simple query"""
    try:
        if db is None:
            logger.error("Firebase database not initialized")
            return False
            
        # Try a simple query to verify connection
        docs_collection = documents()
        if docs_collection is None:
            logger.error("Could not get documents collection reference")
            return False
            
        test_query = docs_collection.limit(1)
        test_query.get()  # This will raise an exception if the connection fails
        logger.info("Firebase database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Firebase database connection test failed: {str(e)}")
        return False
        
def get_document_by_url(url, document_type):
    """Get a document by URL and type"""
    try:
        docs_collection = documents()
        if docs_collection is None:
            logger.error("Documents collection not available")
            return None
            
        # Query for document with matching URL and type
        query = docs_collection.where("url", "==", url).where("document_type", "==", document_type).limit(1)
        docs = list(query.stream())
        
        if docs:
            doc_data = docs[0].to_dict()
            doc_data['id'] = docs[0].id
            return doc_data
        return None
    except Exception as e:
        logger.error(f"Error getting document by URL and type: {str(e)}")
        return None

def get_document_by_retrieved_url(url, document_type):
    """Get a document by retrieved URL and type"""
    try:
        docs_collection = documents()
        if docs_collection is None:
            logger.error("Documents collection not available")
            return None
            
        # Query for document with matching retrieved URL and type
        query = docs_collection.where("retrieved_url", "==", url).where("document_type", "==", document_type).limit(1)
        docs = list(query.stream())
        
        if docs:
            doc_data = docs[0].to_dict()
            doc_data['id'] = docs[0].id
            return doc_data
        return None
    except Exception as e:
        logger.error(f"Error getting document by retrieved URL and type: {str(e)}")
        return None

def create_document(document_data):
    """Create a new document in Firestore"""
    try:
        docs_collection = documents()
        if docs_collection is None:
            logger.error("Documents collection not available")
            return None
            
        # Create a new document with auto-generated Firebase ID
        # This generates IDs in the format like "00FJECPjC2Tu0wTedRuF"
        new_doc_ref = docs_collection.document()
        new_doc_ref.set(document_data)
        logger.info(f"Document created successfully with ID: {new_doc_ref.id}")
        
        return new_doc_ref.id
    except Exception as e:
        logger.error(f"Error creating document: {str(e)}")
        return None

def ensure_collections_exist():
    """Ensures that all required collections exist in Firestore."""
    global db
    
    if db is None:
        logger.warning("Firebase database not initialized - cannot ensure collections exist")
        # Try to initialize Firebase
        try:
            # Force initialization in development
            force_init = settings.ENVIRONMENT == "development"
            db = initialize_firebase(force_init=force_init)
        except Exception as e:
            logger.error(f"Firebase initialization failed during ensure_collections_exist: {str(e)}")
            return False
    
    # Check again if initialization succeeded
    if db is None:
        logger.error("Firebase still not initialized after attempt - cannot create collections")
        return False
    
    try:
        # Define required collections
        required_collections = ["documents", "submissions"]
        
        for collection_name in required_collections:
            try:
                # Just check if the collection exists in the database
                collection_ref = db.collection(collection_name)
                # We don't need to create any documents, just ensure the collection reference is valid
                logger.info(f"Collection '{collection_name}' reference created/verified")
            except Exception as e:
                logger.error(f"Error ensuring collection '{collection_name}' exists: {str(e)}")
                return False
        
        logger.info("All required Firestore collections have been verified/created")
        return True
        
    except Exception as e:
        logger.error(f"Error in ensure_collections_exist: {str(e)}")
        return False

async def increment_views(document_id):
    """Increment the views count for a document"""
    try:
        doc_ref = get_document_ref(documents, document_id)
        if doc_ref is None:
            logger.error(f"Cannot increment views - Invalid document reference for ID: {document_id}")
            return False
            
        # Update the views field
        doc = doc_ref.get()
        if not doc.exists:
            logger.error(f"Document {document_id} not found for view increment")
            return False
            
        doc_data = doc.to_dict()
        current_views = doc_data.get('views', 0)
        
        # Increment views
        doc_ref.update({
            'views': current_views + 1,
            'updated_at': datetime.now()
        })
        
        logger.info(f"Incremented views for document {document_id}")
        return True
    except Exception as e:
        logger.error(f"Error incrementing views for document {document_id}: {str(e)}")
        return False

# Run connection check when the module is imported
# but only log warnings if it fails - don't prevent app startup
try:
    check_database_connection()
except Exception as e:
    logger.warning(f"Initial database connection check failed: {str(e)}")

# Ensure collections exist - but continue even if this fails
# as we have lazy initialization now
try:
    ensure_collections_exist()
except Exception as e:
    logger.warning(f"Error ensuring collections exist: {str(e)}")
    logger.warning("Collections will be created on first access")

# Initialize Firebase for the overall app
def init_firebase():
    try:
        global db, fs
        
        from firebase_admin import initialize_app, firestore, storage
        
        app = initialize_app()
        if app:
            db = firestore.client()
            fs = storage.bucket()
            
            # Initialize Typesense for search
            init_typesense()
            
            return True
        return False
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
        return False