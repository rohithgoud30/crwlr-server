from typing import Dict, Any, Optional, List, Union
import logging
from app.crud.firebase_base import FirebaseCRUDBase
from app.core.database import db
from datetime import datetime
from google.cloud import firestore
from app.crud.stats import stats_crud
from app.core.algolia import index_document, search_documents as algolia_search

# Setup logging
logger = logging.getLogger(__name__)

class DocumentCRUD(FirebaseCRUDBase):
    """CRUD for document management."""
    
    def __init__(self):
        """Initialize with documents collection."""
        super().__init__("documents")
        # Set a flag to track if Firebase is working
        self.firebase_operational = self.collection is not None
        if not self.firebase_operational:
            logger.error("DocumentCRUD initialized with non-functional Firebase connection")
    
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
    
    async def increment_views(self, id: str) -> Optional[Dict[str, Any]]:
        """Increment the view count for a document."""
        if not self.collection or not id:
            return None
            
        try:
            # Get document reference
            doc_ref = self.collection.document(str(id))
            
            # Use Firestore transaction to atomically increment views
            transaction = db.transaction()
            
            @firestore.transactional
            def increment_in_transaction(transaction, doc_ref):
                doc = doc_ref.get(transaction=transaction)
                if not doc.exists:
                    return None
                    
                doc_data = doc.to_dict()
                current_views = doc_data.get('views', 0)
                transaction.update(doc_ref, {
                    'views': current_views + 1,
                    'updated_at': datetime.now()
                })
                
                # Return updated document
                updated_data = doc_data.copy()
                updated_data['views'] = current_views + 1
                updated_data['id'] = doc.id
                return updated_data
                
            result = increment_in_transaction(transaction, doc_ref)
            if result:
                logger.info(f"Incremented views for document {id}")
                
                # Also update the document in Algolia
                try:
                    await index_document(result)
                except Exception as algolia_err:
                    logger.warning(f"Failed to update Algolia after incrementing views: {algolia_err}")
                
                return result
            else:
                logger.warning(f"Document {id} not found - could not increment views")
                return None
                
        except Exception as e:
            logger.error(f"Error incrementing views for document {id}: {str(e)}")
            return None
    
    async def search_documents(
        self,
        query: str,
        document_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
        sort_by: Optional[str] = "updated_at",
        sort_order: Optional[str] = "desc"
    ) -> Dict[str, Any]:
        """
        Search for documents based on provided query.
        
        Attempts to use Algolia first for faster search.
        Falls back to Firebase if Algolia is not available.
        
        Args:
            query: The search query
            document_type: Optional filter by document_type (e.g. 'tos' or 'pp')
            page: Page number for pagination (1-indexed)
            per_page: Number of results per page
            sort_by: Field to sort by (e.g., "updated_at", "views", "company_name", "url")
            sort_order: Sort direction ("asc" or "desc")
        
        Returns:
            A dictionary containing search results and pagination information
        """
        try:
            # Try Algolia search first
            algolia_results = None
            try:
                # Convert pagination parameters for Algolia
                algolia_page = page - 1  # Algolia uses 0-indexed pagination
                
                # Create filters if document_type is specified
                filters = f"document_type:{document_type}" if document_type else None
                
                # Attempt Algolia search
                algolia_results = await algolia_search(
                    query=query,
                    document_type=document_type,
                    filters=filters
                )
                
                # Check if Algolia search was successful
                if algolia_results and "hits" in algolia_results:
                    logger.info(f"Algolia search successful for query: {query}")
                    
                    # Extract results from Algolia's response format
                    total = algolia_results.get("nbHits", 0)
                    total_pages = (total + per_page - 1) // per_page if total > 0 else 0
                    
                    # Convert Algolia hits to our format
                    hits = algolia_results.get("hits", [])
                    items = []
                    for hit in hits:
                        # Algolia hits have objectID that maps to our document ID
                        item = hit.copy()
                        item["id"] = hit.get("objectID", "")
                        items.append(item)
                    
                    # Apply pagination manually since we're getting all results from Algolia
                    start_idx = (page - 1) * per_page
                    end_idx = min(start_idx + per_page, len(items))
                    items_page = items[start_idx:end_idx] if start_idx < len(items) else []
                    
                    return {
                        "items": items_page,
                        "total": total,
                        "page": page,
                        "per_page": per_page,
                        "total_pages": total_pages,
                        "has_next": page < total_pages,
                        "has_prev": page > 1,
                        "search_provider": "algolia"
                    }
            except Exception as algolia_err:
                logger.warning(f"Algolia search failed for query '{query}', falling back to Firebase: {algolia_err}")
                # Fall back to Firebase search if Algolia fails

            # Use .lower() for case-insensitive search
            query_lower = query.lower()
            
            # Since we're using Firestore which doesn't have built-in full-text search,
            # we need to fetch all documents and filter them manually.
            
            filters = {}
            if document_type:
                filters["document_type"] = document_type
                
            all_docs_query = self.collection
            if document_type:
                all_docs_query = all_docs_query.where(filter=firestore.FieldFilter("document_type", "==", document_type))

            all_docs_stream = all_docs_query.stream() # Stream all (or filtered by type)
            
            matching_docs = []
            for doc_snapshot in all_docs_stream:
                doc = doc_snapshot.to_dict()
                doc['id'] = doc_snapshot.id # Ensure ID is part of the dict
                
                # Search in multiple fields including summaries for better results
                company_name = str(doc.get("company_name", "")).lower()
                url = str(doc.get("url", "")).lower()
                retrieved_url = str(doc.get("retrieved_url", "")).lower()
                one_sentence_summary = str(doc.get("one_sentence_summary", "")).lower()
                hundred_word_summary = str(doc.get("hundred_word_summary", "")).lower()
                
                # Match if query is found in any of these fields
                if (query_lower in company_name or 
                    query_lower in url or 
                    query_lower in retrieved_url or
                    query_lower in one_sentence_summary or
                    query_lower in hundred_word_summary):
                    matching_docs.append(doc)

            # Client-side sorting after filtering
            if sort_by:
                # Handle missing keys or None values gracefully for sorting
                def get_sort_key(item):
                    val = item.get(sort_by)
                    if val is None:
                        if sort_by == "views": return 0 # Treat None views as 0 for sorting
                        # For other fields, decide a consistent way to handle None
                        # For string fields, an empty string is fine. For dates, a very old/new date.
                        return "" if isinstance(item.get(sort_by, ""), str) else (datetime.min if isinstance(item.get(sort_by, datetime.min), datetime) else 0)
                    if sort_by == "updated_at" and isinstance(val, str):
                        try:
                            return datetime.fromisoformat(val.replace("Z", "+00:00"))
                        except ValueError: # If parsing fails, return a default sortable value
                           return datetime.min
                    return val

                try:
                    matching_docs.sort(key=get_sort_key, reverse=(sort_order.lower() == "desc"))
                except TypeError as te:
                    logger.error(f"TypeError during sorting by {sort_by}: {te}. Documents might not be sorted correctly.")
            
            # Calculate pagination
            total = len(matching_docs)
            total_pages = (total + per_page - 1) // per_page if total > 0 else 0
            
            # Ensure page is within bounds
            if page < 1:
                page = 1
            if page > total_pages and total_pages > 0:
                page = total_pages
            
            # Get the slice for the current page
            start_idx = (page - 1) * per_page
            end_idx = min(start_idx + per_page, total)
            
            # Slice the results for the current page
            items = matching_docs[start_idx:end_idx] if start_idx < total else []
            
            # Return paginated results
            return {
                "items": items,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1,
                "search_provider": "firebase"
            }
        except Exception as e:
            logger.error(f"Error in search_documents: {str(e)}")
            
            # Return empty results on error
            return {
                "items": [],
                "total": 0,
                "page": page,
                "per_page": per_page,
                "total_pages": 0,
                "has_next": False,
                "has_prev": False,
                "error": str(e),
                "search_provider": "error"
            }
    
    async def get_popular_documents(self, limit: int = 5, document_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get most viewed documents."""
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
            
    async def delete_document(self, id: str) -> bool:
        """Delete a document by ID and update stats."""
        if not self.collection:
            logger.error("Firebase database not initialized")
            return False
            
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
            # Check if document exists and get its type
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning(f"Document {id} not found - cannot delete")
                return False
            
            # Get document type for stats update
            doc_data = doc.to_dict()
            document_type = doc_data.get("document_type")
                
            # Delete the document
            doc_ref.delete()
            logger.info(f"Document {id} deleted successfully")
            
            # Update stats
            if document_type:
                await stats_crud.decrement_document_count(document_type)
            
            return True
        except Exception as e:
            logger.error(f"Error deleting document {id}: {str(e)}")
            return False
    
    async def get_document_counts(self) -> Dict[str, int]:
        """Get counts of documents by type from the stats table."""
        return await stats_crud.get_document_counts()

    async def update_document_analysis(self, id: str, analysis_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update document analysis data."""
        if not self.collection or not id:
            logger.error("Cannot update document analysis: missing collection or ID")
            return None
            
        try:
            # Get document reference
            doc_ref = self.collection.document(str(id))
            
            # Get current document to preserve fields not included in update
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning(f"Document {id} not found - cannot update analysis")
                return None
                
            # Update the document with the new analysis data
            doc_ref.update({
                **analysis_data,
                'updated_at': datetime.now()
            })
            
            # Get the updated document to return
            updated_doc = doc_ref.get()
            result = updated_doc.to_dict()
            result['id'] = updated_doc.id
            
            # Update stats collection
            await stats_crud.update_last_updated()
            
            # Also update the document in Algolia
            try:
                await index_document(result)
            except Exception as algolia_err:
                logger.warning(f"Failed to update Algolia after updating document analysis: {algolia_err}")
            
            logger.info(f"Successfully updated analysis for document {id}")
            return result
            
        except Exception as e:
            logger.error(f"Error updating document analysis for {id}: {str(e)}")
            return None
            
    async def update_company_name(self, id: str, company_name: str) -> Optional[Dict[str, Any]]:
        """
        Update a document's company name.
        
        Args:
            id: The document ID to update
            company_name: The new company name
            
        Returns:
            Updated document or None if update fails
        """
        if not self.collection or not id:
            logger.error("Cannot update company name: invalid collection or ID")
            return None
        
        try:
            # Get document reference
            doc_ref = self.collection.document(str(id))
            
            # Check if document exists
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning(f"Document {id} not found for company name update")
                return None
            
            # Update document with new company name and updated timestamp
            update_data = {
                "company_name": company_name,
                "updated_at": datetime.now()
            }
            
            # Update document
            doc_ref.update(update_data)
            
            # Get updated document
            updated_doc = doc_ref.get()
            updated_data = updated_doc.to_dict()
            updated_data['id'] = updated_doc.id
            
            logger.info(f"Successfully updated company name for document {id}")
            return updated_data
        except Exception as e:
            logger.error(f"Error updating document company name: {str(e)}")
            return None

    async def create(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new document."""
        if not self.collection:
            logger.error("Cannot create document: missing collection")
            return None
            
        try:
            # Validate that required fields are present
            required_fields = ['url', 'document_type']
            for field in required_fields:
                if field not in data or not data[field]:
                    logger.error(f"Cannot create document: missing required field '{field}'")
                    return None
            
            # Set created_at and updated_at
            now = datetime.now()
            data['created_at'] = now
            data['updated_at'] = now
            
            # Add the document
            doc_ref = self.collection.document()
            doc_ref.set(data)
            
            # Get the created document
            created_doc = doc_ref.get()
            if created_doc.exists:
                result = created_doc.to_dict()
                result['id'] = created_doc.id
                
                # Update stats collection
                await stats_crud.update_last_updated()
                
                # Also index the document in Algolia
                try:
                    await index_document(result)
                except Exception as algolia_err:
                    logger.warning(f"Failed to index new document in Algolia: {algolia_err}")
                
                logger.info(f"Successfully created document with ID {created_doc.id}")
                return result
            else:
                logger.error("Document creation failed - document does not exist after creation")
                return None
                
        except Exception as e:
            logger.error(f"Error creating document: {str(e)}")
            return None

# Create a global instance for reuse
document_crud = DocumentCRUD() 