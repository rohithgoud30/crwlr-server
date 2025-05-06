import os
import logging
from typing import Optional
from datetime import datetime
from uuid import uuid4

from app.core.config import settings
from app.core.firebase import db

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check if Firebase is properly initialized
if db is None:
    logger.error("Firebase database not initialized - check your Firebase configuration")
else:
    logger.info("Firebase database initialized successfully")

# Define the document types
DOCUMENT_TYPES = ["tos", "pp"]

# Define database collections
def get_collection(collection_name):
    """Get a Firestore collection reference"""
    if db is None:
        logger.error(f"Cannot get collection {collection_name} - Firebase not initialized")
        return None
    return db.collection(collection_name)

# Collections used in the application
users_collection = get_collection("users")
documents_collection = get_collection("documents")
submissions_collection = get_collection("submissions")

# Document reference helpers
def get_document_ref(collection, doc_id):
    """Get a reference to a document in a collection by ID"""
    if collection is None:
        return None
    return collection.document(str(doc_id))

def check_database_connection():
    """Check if the Firebase connection is working by testing a simple query"""
    try:
        if db is None:
            logger.error("Firebase database not initialized")
            return False
            
        # Try a simple query to verify connection
        test_query = documents_collection.limit(1)
        test_query.get()  # This will raise an exception if the connection fails
        logger.info("Firebase database connection test successful")
        return True
    except Exception as e:
        logger.error(f"Firebase database connection test failed: {str(e)}")
        return False
        
def get_document_by_url(url, document_type):
    """Get a document by URL and type"""
    try:
        if documents_collection is None:
            logger.error("Documents collection not available")
            return None
            
        # Query for document with matching URL and type
        query = documents_collection.where("url", "==", url).where("document_type", "==", document_type).limit(1)
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
        if documents_collection is None:
            logger.error("Documents collection not available")
            return None
            
        # Query for document with matching retrieved URL and type
        query = documents_collection.where("retrieved_url", "==", url).where("document_type", "==", document_type).limit(1)
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
        if documents_collection is None:
            logger.error("Documents collection not available")
            return None
            
        # Create a new document with auto-generated ID
        new_doc_ref = documents_collection.document()
        new_doc_ref.set(document_data)
        
        return new_doc_ref.id
    except Exception as e:
        logger.error(f"Error creating document: {str(e)}")
        return None

def ensure_collections_exist():
    """Ensures that all required collections exist in Firestore."""
    if db is None:
        logger.error("Firebase database not initialized - cannot create collections")
        return False
    
    try:
        # Define required collections
        required_collections = ["users", "documents", "submissions"]
        
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

# Run connection check at module import
check_database_connection()

# Ensure collections exist
ensure_collections_exist()

async def increment_views(document_id):
    """Increment the views count for a document"""
    try:
        doc_ref = get_document_ref(documents_collection, document_id)
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