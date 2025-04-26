from typing import Optional, Dict, Any, List, Literal
from uuid import UUID
from sqlalchemy import desc, asc, select
from datetime import datetime, timedelta

from app.crud.base import CRUDBase
from app.core.database import submissions, async_engine


class CRUDSubmission(CRUDBase):
    """CRUD operations for submissions."""
    
    async def get_recent_submissions(
        self, 
        hours: int = 24, 
        document_type: Optional[Literal["tos", "pp"]] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get submissions created within the last specified hours.
        
        Args:
            hours: Number of hours to look back
            document_type: Optional filter by document type
            status: Optional filter by status
            
        Returns:
            List of submissions
        """
        async with async_engine.connect() as conn:
            # Calculate cutoff time
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            
            # Start with base query
            query = self.table.select().where(
                self.table.c.created_at >= cutoff
            )
            
            # Add optional filters
            if document_type:
                query = query.where(self.table.c.document_type == document_type)
                
            if status:
                query = query.where(self.table.c.status == status)
                
            # Order by most recent
            query = query.order_by(self.table.c.created_at.desc())
            
            # Execute query
            result = await conn.execute(query)
            return [dict(row) for row in result.fetchall()]
    
    async def get_user_submissions(
        self, 
        user_id: UUID, 
        document_type: Optional[Literal["tos", "pp"]] = None,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get submissions for a specific user.
        
        Args:
            user_id: User ID to filter by
            document_type: Optional filter by document type
            status: Optional filter by status
            limit: Maximum number of submissions to return
            
        Returns:
            List of submissions
        """
        async with async_engine.connect() as conn:
            # Start with base query
            query = self.table.select().where(
                self.table.c.user_id == user_id
            )
            
            # Add optional filters
            if document_type:
                query = query.where(self.table.c.document_type == document_type)
                
            if status:
                query = query.where(self.table.c.status == status)
                
            # Order by most recent and limit
            query = query.order_by(self.table.c.created_at.desc()).limit(limit)
            
            # Execute query
            result = await conn.execute(query)
            return [dict(row) for row in result.fetchall()]
    
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
        async with async_engine.connect() as conn:
            query = self.table.select().where(
                (self.table.c.requested_url == requested_url) &
                (self.table.c.document_type == document_type)
            ).order_by(self.table.c.created_at.desc()).limit(1)
            
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None


# Create CRUD instance
submission_crud = CRUDSubmission(submissions) 