from typing import Dict, Any, Optional, List
import logging
from app.crud.firebase_base import FirebaseCRUDBase
from datetime import datetime

# Setup logging
logger = logging.getLogger(__name__)

class UserCRUD(FirebaseCRUDBase):
    """CRUD for user management."""
    
    def __init__(self):
        """Initialize with Users collection."""
        super().__init__("users")
    
    async def get_by_clerk_id(self, clerk_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by Clerk user ID."""
        try:
            # Query for user with matching clerk_user_id
            query = self.collection.where("clerk_user_id", "==", clerk_id).limit(1)
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

    async def create_user(self, clerk_user_id: str, email: str, name: Optional[str] = None, role: str = "user") -> Optional[Dict[str, Any]]:
        """Create a new user from Clerk user data."""
        try:
            # Check if user already exists
            existing_user = await self.get_by_clerk_id(clerk_user_id)
            if existing_user:
                logger.info(f"User with clerk ID {clerk_user_id} already exists")
                return existing_user
            
            # Create new user
            now = datetime.now()
            user_data = {
                "clerk_user_id": clerk_user_id,
                "email": email,
                "name": name or "",
                "role": role,
                "created_at": now,
                "updated_at": now
            }
            
            # Add to Firestore users collection
            user_ref = self.collection.document()
            user_id = user_ref.id
            user_ref.set(user_data)
            
            # Return user data with ID
            user_data["id"] = user_id
            return user_data
            
        except Exception as e:
            logger.error(f"Error creating user: {str(e)}")
            return None

# Create an instance
user_crud = UserCRUD() 