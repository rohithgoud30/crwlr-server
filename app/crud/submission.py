from typing import Dict, Any, Optional, List, Union
import logging
from app.crud.firebase_base import FirebaseCRUDBase
from datetime import datetime

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
            return result
        except Exception as e:
            logger.error(f"Error creating submission: {str(e)}")
            return None

# Create an instance
submission_crud = SubmissionCRUD() 