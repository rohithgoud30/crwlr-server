from typing import Dict, Any, Optional, List, Union
from uuid import UUID
import logging
from datetime import datetime
from google.cloud import firestore

from app.crud.firebase_base import FirebaseCRUDBase

# Setup logging
logger = logging.getLogger(__name__)

class DocumentCRUD(FirebaseCRUDBase):
    """CRUD for document management."""
    
    def __init__(self):
        """Initialize with documents collection."""
        super().__init__("documents")
    
    async def get_by_url_and_type(self, url: str, document_type: str) -> Optional[Dict[str, Any]]:
        """Get a document by URL and document type."""
        try:
            # Query for document with matching URL and type
            query = self.collection.where("url", "==", url).where("document_type", "==", document_type).limit(1)
            docs = list(query.stream())
            
            if docs:
                doc_data = docs[0].to_dict()
                doc_data['id'] = docs[0].id
                return doc_data
            return None
        except Exception as e:
            logger.error(f"Error getting document by URL and type: {str(e)}")
            return None
    
    async def get_by_retrieved_url(self, url: str, document_type: str) -> Optional[Dict[str, Any]]:
        """Get a document by retrieved URL and document type."""
        try:
            # Query for document with matching retrieved URL and type
            query = self.collection.where("retrieved_url", "==", url).where("document_type", "==", document_type).limit(1)
            docs = list(query.stream())
            
            if docs:
                doc_data = docs[0].to_dict()
                doc_data['id'] = docs[0].id
                return doc_data
            return None
        except Exception as e:
            logger.error(f"Error getting document by retrieved URL and type: {str(e)}")
            return None
    
    async def increment_views(self, id: Union[str, UUID]) -> Optional[Dict[str, Any]]:
        """Increment the views counter for a document."""
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
            # Check if document exists
            doc = doc_ref.get()
            if not doc.exists:
                return None
            
            # Get current views count
            doc_data = doc.to_dict()
            current_views = doc_data.get("views", 0)
            
            # Increment views
            doc_ref.update({
                "views": current_views + 1,
                "updated_at": datetime.now()
            })
            
            # Get updated document
            updated_doc = doc_ref.get()
            result = updated_doc.to_dict()
            result['id'] = doc_id
            return result
        except Exception as e:
            logger.error(f"Error incrementing views: {str(e)}")
            return None
    
    async def search_documents(
        self, 
        query: str, 
        document_type: Optional[str] = None,
        page: int = 1, 
        per_page: int = 10
    ) -> Dict[str, Any]:
        """
        Search for documents by company name or URL.
        Very basic implementation - in production would use a proper search engine.
        """
        try:
            filters = {}
            if document_type:
                filters["document_type"] = document_type
                
            # Get all documents that match the filters
            all_docs = await self.get_multi(skip=0, limit=1000, filters=filters)
            
            # Filter locally for documents matching the query string
            # This is not efficient for large collections - in production use a proper search engine
            matching_docs = []
            for doc in all_docs:
                company_name = doc.get("company_name", "").lower()
                url = doc.get("url", "").lower()
                query_lower = query.lower()
                
                if query_lower in company_name or query_lower in url:
                    matching_docs.append(doc)
            
            # Manual pagination
            total_count = len(matching_docs)
            offset = (page - 1) * per_page
            paged_docs = matching_docs[offset:offset+per_page]
            
            # Calculate pagination info
            total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 0
            
            return {
                "items": paged_docs,
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        except Exception as e:
            logger.error(f"Error searching documents: {str(e)}")
            return {
                "items": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False
            }
    
    async def get_popular_documents(self, limit: int = 10, document_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get most popular documents by views."""
        try:
            query = self.collection
            
            if document_type:
                query = query.where("document_type", "==", document_type)
                
            # Order by views in descending order
            query = query.order_by("views", direction=firestore.Query.DESCENDING).limit(limit)
            
            # Execute query
            docs = list(query.stream())
            
            # Convert to list of dicts
            result = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                result.append(data)
                
            return result
        except Exception as e:
            logger.error(f"Error getting popular documents: {str(e)}")
            return []
    
    async def get_recent_documents(self, limit: int = 10, document_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get most recently added documents."""
        try:
            query = self.collection
            
            if document_type:
                query = query.where("document_type", "==", document_type)
                
            # Order by created_at in descending order
            query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit)
            
            # Execute query
            docs = list(query.stream())
            
            # Convert to list of dicts
            result = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                result.append(data)
                
            return result
        except Exception as e:
            logger.error(f"Error getting recent documents: {str(e)}")
            return []
            
    async def delete_document(self, id: Union[str, UUID]) -> bool:
        """Delete a document by ID."""
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
            # Check if document exists
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning(f"Document {doc_id} not found for deletion")
                return False
            
            # Delete the document
            doc_ref.delete()
            logger.info(f"Document {doc_id} deleted successfully")
            return True
        except Exception as e:
            logger.error(f"Error deleting document {id}: {str(e)}")
            return False
            
    async def get_document_counts(self) -> Dict[str, int]:
        """Get counts of documents by type."""
        try:
            # Query for ToS documents
            tos_query = self.collection.where("document_type", "==", "tos")
            tos_docs = list(tos_query.stream())
            tos_count = len(tos_docs)
            
            # Query for PP documents
            pp_query = self.collection.where("document_type", "==", "pp")
            pp_docs = list(pp_query.stream())
            pp_count = len(pp_docs)
            
            # Total count
            total_count = tos_count + pp_count
            
            return {
                "tos_count": tos_count,
                "pp_count": pp_count,
                "total_count": total_count
            }
        except Exception as e:
            logger.error(f"Error getting document counts: {str(e)}")
            return {
                "tos_count": 0,
                "pp_count": 0,
                "total_count": 0
            }

# Create an instance
document_crud = DocumentCRUD() 