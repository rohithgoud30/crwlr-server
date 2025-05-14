from typing import Dict, Any, Optional, List, Union
import logging
from app.crud.firebase_base import FirebaseCRUDBase
from datetime import datetime
import time
from app.core.typesense import get_typesense_client

# Setup logging
logger = logging.getLogger(__name__)

class SubmissionCRUD(FirebaseCRUDBase):
    """CRUD for submission management."""
    
    def __init__(self):
        """Initialize the SubmissionCRUD with the 'submissions' collection."""
        super().__init__("submissions")
        
        # Valid submission statuses
        self.valid_statuses = ["initialized", "processing", "analyzing", "success", "failed"]
    
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
            # Create simple schema for submissions collection if it doesn't exist
            SUBMISSIONS_COLLECTION = "submissions"
            try:
                # Check if submissions collection exists
                client.collections[SUBMISSIONS_COLLECTION].retrieve()
            except Exception:
                # Create submissions collection if it doesn't exist
                submissions_schema = {
                    'name': SUBMISSIONS_COLLECTION,
                    'fields': [
                        {'name': 'id', 'type': 'string'},
                        {'name': 'url', 'type': 'string', 'infix': True},
                        {'name': 'document_type', 'type': 'string', 'facet': True},
                        {'name': 'status', 'type': 'string', 'facet': True},
                        {'name': 'user_email', 'type': 'string', 'facet': True},
                        {'name': 'updated_at', 'type': 'int64', 'sort': True},
                        {'name': 'created_at', 'type': 'int64', 'sort': True}
                    ],
                    'default_sorting_field': 'created_at'
                }
                client.collections.create(submissions_schema)
                logger.info(f"Created new Typesense collection: {SUBMISSIONS_COLLECTION}")
            
            # Prepare submission for Typesense
            # Convert updated_at to unix timestamp if it's a datetime
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
            if not user_email and 'user_id' in submission and submission['user_id']:
                # Fallback to user_id if user_email not present
                user_email = submission['user_id']
                logger.info(f"Using user_id as user_email for submission {submission['id']}")
            
            if not user_email:
                logger.warning(f"No user identifier found for submission {submission['id']}. This will affect user-specific searches.")
            
            # Prepare document for Typesense
            typesense_doc = {
                'id': submission['id'],
                'url': submission.get('requested_url', ''),
                'document_type': submission.get('document_type', ''),
                'status': submission.get('status', ''),
                'user_email': user_email,
                'updated_at': typesense_updated_at,
                'created_at': typesense_created_at
            }
            
            # Upsert document in Typesense
            client.collections[SUBMISSIONS_COLLECTION].documents.upsert(typesense_doc)
            logger.info(f"Submission {submission['id']} indexed in Typesense")
            return True
        except Exception as e:
            logger.error(f"Error indexing submission in Typesense: {str(e)}")
            return False

# Create an instance
submission_crud = SubmissionCRUD() 