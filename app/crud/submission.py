from typing import Dict, Any, Optional, List, Union
from uuid import UUID
import logging
from datetime import datetime

from app.crud.firebase_base import FirebaseCRUDBase

# Setup logging
logger = logging.getLogger(__name__)

class SubmissionCRUD(FirebaseCRUDBase):
    """CRUD for submission management."""
    
    def __init__(self):
        """Initialize with submissions collection."""
        super().__init__("submissions")
    
    async def get_submissions_by_user(self, user_id: Union[str, UUID], limit: int = 20) -> List[Dict[str, Any]]:
        """Get submissions by user ID."""
        try:
            user_id_str = str(user_id)
            query = self.collection.where("user_id", "==", user_id_str).limit(limit)
            submissions = list(query.stream())
            
            result = []
            for sub in submissions:
                data = sub.to_dict()
                data['id'] = sub.id
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
        id: Union[str, UUID], 
        status: str, 
        document_id: Optional[Union[str, UUID]] = None,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update a submission status."""
        try:
            doc_id = str(id)
            doc_ref = self.collection.document(doc_id)
            
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
                
            # Update the document
            doc_ref.update(update_data)
            
            # Get updated document
            updated_doc = doc_ref.get()
            result = updated_doc.to_dict()
            result['id'] = doc_id
            return result
        except Exception as e:
            logger.error(f"Error updating submission status: {str(e)}")
            return None

# Create an instance
submission_crud = SubmissionCRUD() 