from typing import Optional, Dict, Any, List, Literal
from uuid import UUID

from app.crud.base import CRUDBase
from app.core.database import submissions, engine


class CRUDSubmission(CRUDBase):
    """CRUD operations for submissions."""
    
    async def get_by_user(
        self, 
        user_id: UUID, 
        page: int = 1, 
        per_page: int = 6,
        sort_by: str = "most_recent"
    ) -> Dict[str, Any]:
        """
        Get submissions by user ID with pagination.
        
        Parameters:
        - user_id: UUID of the user
        - page: Page number (1-based)
        - per_page: Items per page
        - sort_by: Sorting option (most_recent, oldest_first)
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        # Create filters
        filters = {"user_id": user_id}
        
        # Map sort options
        order_by = "created_at"
        order_direction = "desc"
        
        if sort_by == "oldest_first":
            order_direction = "asc"
        
        # Use base paginate method
        return await self.paginate(
            page=page,
            per_page=per_page,
            filters=filters,
            order_by=order_by,
            order_direction=order_direction,
            valid_per_page=[6, 9, 12, 15]
        )
    
    async def get_pending_submissions(
        self, 
        page: int = 1,
        per_page: int = 10
    ) -> Dict[str, Any]:
        """
        Get submissions with 'pending' status with pagination.
        
        Parameters:
        - page: Page number (1-based)
        - per_page: Items per page
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        # Use base paginate method with status filter
        return await self.paginate(
            page=page,
            per_page=per_page,
            filters={"status": "pending"},
            order_by="created_at",
            order_direction="asc"
        )
    
    async def get_submissions_by_status(
        self, 
        status: str,
        page: int = 1,
        per_page: int = 10
    ) -> Dict[str, Any]:
        """
        Get submissions by status with pagination.
        
        Parameters:
        - status: Status to filter by (pending, in_progress, done, error)
        - page: Page number (1-based)
        - per_page: Items per page
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        # Use base paginate method with status filter
        return await self.paginate(
            page=page,
            per_page=per_page,
            filters={"status": status},
            order_by="created_at",
            order_direction="desc"
        )
    
    async def update_status(
        self, 
        id: UUID, 
        status: str, 
        document_id: Optional[UUID] = None,
        error_message: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update submission status and optionally set document_id and error_message."""
        update_data = {"status": status}
        
        if document_id:
            update_data["document_id"] = document_id
            
        if error_message:
            update_data["error_message"] = error_message
            
        return await self.update(id, update_data)
    
    async def get_by_url(
        self, 
        requested_url: str, 
        document_type: Literal["tos", "pp"]
    ) -> Optional[Dict[str, Any]]:
        """Get most recent submission by URL and document type."""
        async with engine.connect() as conn:
            query = self.table.select().where(
                (self.table.c.requested_url == requested_url) &
                (self.table.c.document_type == document_type)
            ).order_by(self.table.c.created_at.desc()).limit(1)
            
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None


# Create an instance of CRUDSubmission for use throughout the application
submission_crud = CRUDSubmission(submissions) 