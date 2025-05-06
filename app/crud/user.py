from typing import Dict, Any, Optional, List
from uuid import UUID
import logging

from app.crud.firebase_base import FirebaseCRUDBase

# Setup logging
logger = logging.getLogger(__name__)

class UserCRUD(FirebaseCRUDBase):
    """CRUD for user management."""
    
    def __init__(self):
        """Initialize with Users collection."""
        super().__init__("users")
    
    async def get_by_clerk_id(self, clerk_user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by Clerk user ID."""
        try:
            # Query for user with matching clerk_user_id
            query = self.collection.where("clerk_user_id", "==", clerk_user_id).limit(1)
            users = list(query.stream())
            
            if users:
                user_data = users[0].to_dict()
                user_data['id'] = users[0].id
                return user_data
            return None
        except Exception as e:
            logger.error(f"Error getting user by clerk ID: {str(e)}")
            return None

    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by email address."""
        try:
            # Query for user with matching email
            query = self.collection.where("email", "==", email).limit(1)
            users = list(query.stream())
            
            if users:
                user_data = users[0].to_dict()
                user_data['id'] = users[0].id
                return user_data
            return None
        except Exception as e:
            logger.error(f"Error getting user by email: {str(e)}")
            return None

# Create an instance
user = UserCRUD() 