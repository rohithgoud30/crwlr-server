from typing import Dict, Any, Optional, List, Union, Tuple
import logging
from app.crud.firebase_base import FirebaseCRUDBase
from datetime import datetime
import time
from app.core.typesense import get_typesense_client, SUBMISSIONS_COLLECTION_NAME

# Setup logging
logger = logging.getLogger(__name__)

class SubmissionCRUD(FirebaseCRUDBase):
    """CRUD for submission management."""
    
    def __init__(self):
        """Initialize the SubmissionCRUD with the 'submissions' collection."""
        super().__init__("submissions")
        
        # Valid submission statuses
        self.valid_statuses = ["initialized", "processing", "success", "failed"]
    
    async def get_submissions_by_user(self, user_email: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get submissions by user email."""
        try:
            query = self.collection.where("user_email", "==", str(user_email)).order_by("created_at", direction="desc").limit(limit)
            docs = list(query.stream())
            
            # Convert to list of dicts
            result = []
            for doc in docs:
                data = doc.to_dict()
                data['id'] = doc.id
                result.append(data)
                
            return result
        except Exception as e:
            logger.error(f"Error getting submissions by user: {str(e)}")
            return []
    
    async def get_pending_submissions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get pending submissions."""
        try:
            query = self.collection.where("status", "==", "pending").limit(limit)
            submissions = list(query.stream())
            
            result = []
            for sub in submissions:
                data = sub.to_dict()
                data['id'] = sub.id
                result.append(data)
                
            return result
        except Exception as e:
            logger.error(f"Error getting pending submissions: {str(e)}")
            return []
    
    async def update_submission_status(
        self, 
        id: str, 
        status: str, 
        document_id: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update a submission status."""
        try:
            # Validate status
            if status not in self.valid_statuses:
                logger.warning(f"Invalid submission status: {status}. Using 'failed' instead.")
                status = "failed"
                
            doc_ref = self.collection.document(str(id))
            
            # Check if document exists
            doc = doc_ref.get()
            if not doc.exists:
                return None
                
            # Create update data
            update_data = {
                "status": status,
                "updated_at": datetime.now()
            }
            
            if document_id:
                update_data["document_id"] = str(document_id)
                
            if error_message:
                update_data["error_message"] = error_message
            elif status == "success" and "error_message" in doc.to_dict():
                # Clear error message on success
                update_data["error_message"] = None
                
            # Update the document
            doc_ref.update(update_data)
            
            # Get updated document
            updated_doc = doc_ref.get()
            result = updated_doc.to_dict()
            result['id'] = id
            
            # Index in Typesense
            await self._index_in_typesense(result)
            
            return result
        except Exception as e:
            logger.error(f"Error updating submission status: {str(e)}")
            return None

    async def create_submission(
        self,
        user_email: str,
        document_id: Optional[str] = None,
        requested_url: Optional[str] = None,
        document_type: Optional[str] = None,
        status: str = "initialized",
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a new submission record."""
        try:
            # Validate status
            if status not in self.valid_statuses:
                logger.warning(f"Invalid submission status: {status}. Using 'initialized' instead.")
                status = "initialized"
                
            submission_data = {
                "user_email": str(user_email),
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
                "status": status
            }
            
            # Add optional fields if provided
            if document_id:
                submission_data["document_id"] = str(document_id)
            if requested_url:
                submission_data["requested_url"] = requested_url
            if document_type:
                submission_data["document_type"] = document_type
            if error_message:
                submission_data["error_message"] = error_message
                
            # Create submission in Firestore
            result = await self.create(submission_data)
            
            # Index in Typesense
            if result:
                await self._index_in_typesense(result)
                
            return result
        except Exception as e:
            logger.error(f"Error creating submission: {str(e)}")
            return None
            
    async def _index_in_typesense(self, submission: Dict[str, Any]) -> bool:
        """
        Index or update a submission in Typesense.
        
        Ensures the user_email field is properly indexed for user-specific searches.
        """
        client = get_typesense_client()
        if not client:
            logger.warning("Typesense client not available. Skipping submission indexing.")
            return False
        
        try:
            # Convert timestamps to Unix timestamps for Typesense
            updated_at = submission.get('updated_at')
            if isinstance(updated_at, datetime):
                typesense_updated_at = int(time.mktime(updated_at.timetuple()))
            else:
                typesense_updated_at = int(time.time())  # Current time as fallback
            
            created_at = submission.get('created_at')
            if isinstance(created_at, datetime):
                typesense_created_at = int(time.mktime(created_at.timetuple()))
            else:
                typesense_created_at = int(time.time())  # Current time as fallback
            
            # Ensure user_email is set correctly
            user_email = submission.get('user_email', '')
            if not user_email:
                logger.warning(f"No user email found for submission {submission['id']}. This will affect user-specific searches.")
                return False
            
            # Prepare document for Typesense
            typesense_doc = {
                'id': submission['id'],
                'url': submission.get('requested_url', '').lower(),  # Store URL in lowercase for case-insensitive search
                'document_type': submission.get('document_type', ''),
                'status': submission.get('status', ''),
                'user_email': user_email,
                'document_id': submission.get('document_id', ''),  # Empty string if None
                'error_message': submission.get('error_message', ''),  # Empty string if None
                'updated_at': typesense_updated_at,
                'created_at': typesense_created_at
            }
            
            # First try to delete any existing document to avoid duplicates
            try:
                client.collections[SUBMISSIONS_COLLECTION_NAME].documents[submission['id']].delete()
                logger.debug(f"Deleted existing submission {submission['id']} from Typesense")
            except Exception as del_e:
                if "Not Found" not in str(del_e):
                    logger.warning(f"Error deleting existing submission from Typesense: {str(del_e)}")
            
            # Create new document in Typesense
            client.collections[SUBMISSIONS_COLLECTION_NAME].documents.create(typesense_doc)
            logger.info(f"Successfully indexed submission {submission['id']} in Typesense")
            return True
        except Exception as e:
            logger.error(f"Error indexing submission in Typesense: {str(e)}")
            # Log more details about the document that failed
            logger.error(f"Failed document details: {submission}")
            return False

    async def sync_to_typesense(self, batch_size: int = 100) -> Tuple[int, int, List[str]]:
        """
        Sync all submissions to Typesense.
        
        Args:
            batch_size: Number of submissions to process in each batch
            
        Returns:
            Tuple containing:
            - Total number of submissions processed
            - Number of submissions successfully indexed
            - List of IDs that failed to index
        """
        client = get_typesense_client()
        if not client:
            logger.warning("Typesense client not available. Cannot sync submissions.")
            return 0, 0, []
            
        try:
            # Get all submissions from Firestore
            submissions = self.collection.stream()
            
            total_processed = 0
            success_count = 0
            failed_ids = []
            current_batch = []
            
            for doc in submissions:
                submission = doc.to_dict()
                submission['id'] = doc.id
                current_batch.append(submission)
                
                # Process batch when it reaches the size limit
                if len(current_batch) >= batch_size:
                    success, failed = await self._process_typesense_batch(current_batch)
                    total_processed += len(current_batch)
                    success_count += success
                    failed_ids.extend(failed)
                    current_batch = []
                    
            # Process remaining submissions
            if current_batch:
                success, failed = await self._process_typesense_batch(current_batch)
                total_processed += len(current_batch)
                success_count += success
                failed_ids.extend(failed)
            
            logger.info(f"Typesense sync completed: {success_count}/{total_processed} submissions indexed successfully")
            if failed_ids:
                logger.warning(f"{len(failed_ids)} submissions failed to index: {failed_ids}")
            
            return total_processed, success_count, failed_ids
            
        except Exception as e:
            logger.error(f"Error syncing submissions to Typesense: {str(e)}")
            return 0, 0, []
            
    async def _process_typesense_batch(self, submissions: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
        """
        Process a batch of submissions for Typesense indexing.
        
        Args:
            submissions: List of submission documents to process
            
        Returns:
            Tuple containing:
            - Number of successfully indexed submissions
            - List of IDs that failed to index
        """
        success_count = 0
        failed_ids = []
        
        for submission in submissions:
            try:
                if await self._index_in_typesense(submission):
                    success_count += 1
                else:
                    failed_ids.append(submission['id'])
            except Exception as e:
                logger.error(f"Error processing submission {submission.get('id')}: {str(e)}")
                failed_ids.append(submission.get('id'))
                
        return success_count, failed_ids
        
    async def _delete_from_typesense(self, id: str) -> bool:
        """
        Delete a submission from Typesense index.
        
        Args:
            id: ID of the submission to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        client = get_typesense_client()
        if not client:
            logger.warning("Typesense client not available. Skipping deletion from index.")
            return False
        
        try:
            client.collections[SUBMISSIONS_COLLECTION_NAME].documents[id].delete()
            logger.info(f"Submission {id} deleted from Typesense index")
            return True
        except Exception as e:
            if "Not Found" in str(e):
                # If the document wasn't in Typesense, consider deletion successful
                logger.info(f"Submission {id} not found in Typesense index")
                return True
            logger.error(f"Error deleting submission from Typesense: {str(e)}")
            return False
            
    async def delete_submission(self, id: str) -> bool:
        """
        Delete a submission by ID from both Firebase and Typesense.
        
        Args:
            id: ID of the submission to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        if not self.collection:
            logger.error("Firebase database not initialized")
            return False
            
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
            # Check if submission exists
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning(f"Submission {id} not found - cannot delete")
                return False
                
            # Delete from Firebase
            doc_ref.delete()
            logger.info(f"Submission {id} deleted from Firebase")
            
            # Remove from Typesense index
            await self._delete_from_typesense(id)
            
            return True
        except Exception as e:
            logger.error(f"Error deleting submission {id}: {str(e)}")
            return False

# Create an instance
submission_crud = SubmissionCRUD() 