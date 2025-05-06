from typing import Any, Dict, List, Optional, TypeVar, Generic, Union
from uuid import UUID, uuid4
import logging
from google.cloud.firestore_v1.base_query import FieldFilter
from firebase_admin import firestore
from app.core.firebase import db

# Setup logging
logger = logging.getLogger(__name__)

ModelType = TypeVar("ModelType")

class FirebaseCRUDBase:
    """Base class for Firebase CRUD operations."""
    
    def __init__(self, collection_name: str):
        """Initialize with a collection name."""
        self.collection_name = collection_name
        self.collection = db.collection(collection_name) if db else None
    
    async def get(self, id: Union[str, UUID]) -> Optional[Dict[str, Any]]:
        """Get a document by ID."""
        if not self.collection:
            logger.error("Firebase not initialized")
            return None
            
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict()
                data['id'] = doc.id  # Add the document ID to the returned data
                return data
            return None
        except Exception as e:
            logger.error(f"Error getting document from {self.collection_name}: {str(e)}")
            return None
    
    async def get_multi(
        self, 
        skip: int = 0, 
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get multiple documents with optional filtering."""
        if not self.collection:
            logger.error("Firebase not initialized")
            return []
            
        try:
            query = self.collection
            
            # Apply filters if provided
            if filters:
                for field, value in filters.items():
                    query = query.where(filter=FieldFilter(field, "==", value))
            
            # Get all results first (Firestore doesn't support skip directly)
            docs = list(query.stream())
            
            # Apply pagination manually
            paginated_docs = docs[skip:skip+limit]
            
            # Convert to list of dicts
            result = []
            for doc in paginated_docs:
                data = doc.to_dict()
                data['id'] = doc.id  # Add the document ID
                result.append(data)
                
            return result
        except Exception as e:
            logger.error(f"Error getting documents from {self.collection_name}: {str(e)}")
            return []
    
    async def create(self, obj_in: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new document."""
        if not self.collection:
            logger.error("Firebase not initialized")
            return {}
            
        try:
            # If no ID provided, generate one
            if 'id' not in obj_in:
                doc_id = str(uuid4())
            else:
                doc_id = str(obj_in.pop('id'))  # Extract and convert ID to string
                
            doc_ref = self.collection.document(doc_id)
            doc_ref.set(obj_in)
            
            # Return the created document with its ID
            result = obj_in.copy()
            result['id'] = doc_id
            return result
        except Exception as e:
            logger.error(f"Error creating document in {self.collection_name}: {str(e)}")
            return {}
    
    async def update(self, id: Union[str, UUID], obj_in: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a document by ID."""
        if not self.collection:
            logger.error("Firebase not initialized")
            return None
            
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
            # Check if document exists
            doc = doc_ref.get()
            if not doc.exists:
                return None
                
            # Remove ID if present in update data
            update_data = obj_in.copy()
            if 'id' in update_data:
                del update_data['id']
                
            # Update the document
            doc_ref.update(update_data)
            
            # Get and return the updated document
            updated_doc = doc_ref.get()
            result = updated_doc.to_dict()
            result['id'] = doc_id
            return result
        except Exception as e:
            logger.error(f"Error updating document in {self.collection_name}: {str(e)}")
            return None
    
    async def remove(self, id: Union[str, UUID]) -> Optional[Dict[str, Any]]:
        """Delete a document by ID."""
        if not self.collection:
            logger.error("Firebase not initialized")
            return None
            
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
            # Get document before deletion
            doc = doc_ref.get()
            if not doc.exists:
                return None
                
            # Store document data
            doc_data = doc.to_dict()
            doc_data['id'] = doc_id
            
            # Delete the document
            doc_ref.delete()
            
            return doc_data
        except Exception as e:
            logger.error(f"Error deleting document from {self.collection_name}: {str(e)}")
            return None
    
    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count documents with optional filtering."""
        if not self.collection:
            logger.error("Firebase not initialized")
            return 0
            
        try:
            query = self.collection
            
            # Apply filters if provided
            if filters:
                for field, value in filters.items():
                    query = query.where(filter=FieldFilter(field, "==", value))
            
            # Get all documents and count
            # Note: Firestore doesn't have a native count operation, so we have to fetch all documents
            docs = list(query.stream())
            return len(docs)
        except Exception as e:
            logger.error(f"Error counting documents in {self.collection_name}: {str(e)}")
            return 0
            
    async def paginate(
        self,
        page: int = 1,
        per_page: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        order_direction: str = "desc",
        valid_per_page: List[int] = [6, 9, 12, 15, 20]
    ) -> Dict[str, Any]:
        """
        Common pagination method for all models
        
        Parameters:
        - page: Page number (1-based)
        - per_page: Number of items per page
        - filters: Dict of field:value pairs for filtering
        - order_by: Field name to sort by
        - order_direction: "asc" or "desc"
        - valid_per_page: List of valid page sizes
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        if not self.collection:
            logger.error("Firebase not initialized")
            return {
                "items": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False
            }
            
        try:
            # Validate and normalize parameters
            if per_page not in valid_per_page:
                per_page = valid_per_page[0]
                
            if page < 1:
                page = 1
                
            # Calculate offset
            offset = (page - 1) * per_page
            
            # Start with base query
            query = self.collection
            
            # Apply filters if any
            if filters:
                for field, value in filters.items():
                    query = query.where(filter=FieldFilter(field, "==", value))
            
            # Apply ordering if field exists
            if order_by:
                direction = firestore.Query.ASCENDING if order_direction.lower() == "asc" else firestore.Query.DESCENDING
                query = query.order_by(order_by, direction=direction)
            elif "created_at" in self.collection.document().get().to_dict() if self.collection.document().get().exists else False:
                # Default ordering by created_at if it exists
                query = query.order_by("created_at", direction=firestore.Query.DESCENDING)
            
            # Execute query to get all matching documents
            # (Firestore doesn't support direct pagination, so we have to fetch all and then slice)
            all_docs = list(query.stream())
            
            # Get total count for pagination
            total_count = len(all_docs)
            
            # Apply pagination manually
            paged_docs = all_docs[offset:offset+per_page]
            
            # Convert to list of dicts
            items = []
            for doc in paged_docs:
                data = doc.to_dict()
                data['id'] = doc.id  # Add the document ID
                items.append(data)
            
            # Calculate pagination info
            total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 0
            
            return {
                "items": items,
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        except Exception as e:
            logger.error(f"Error paginating documents in {self.collection_name}: {str(e)}")
            return {
                "items": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False
            } 