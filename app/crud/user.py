from typing import Optional, Dict, Any, List
from uuid import UUID

from app.crud.base import CRUDBase
from app.core.database import users, async_engine


class CRUDUser(CRUDBase):
    """CRUD operations for users."""
    
    async def get_by_clerk_id(self, clerk_user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by Clerk user ID."""
        async with async_engine.connect() as conn:
            query = self.table.select().where(self.table.c.clerk_user_id == clerk_user_id)
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by email address."""
        async with async_engine.connect() as conn:
            query = self.table.select().where(self.table.c.email == email)
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None


# Create CRUD instance
user_crud = CRUDUser(users) 