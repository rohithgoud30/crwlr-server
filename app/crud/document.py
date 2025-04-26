from typing import Optional, Dict, Any, List, Literal
from uuid import UUID
from sqlalchemy import desc, asc, text, or_

from app.crud.base import CRUDBase
from app.core.database import documents, engine


class CRUDDocument(CRUDBase):
    """CRUD operations for documents."""
    
    async def get_by_url(self, url: str, document_type: Literal["tos", "pp"]) -> Optional[Dict[str, Any]]:
        """Get a document by URL and type."""
        conn = await engine.connect()
        try:
            query = self.table.select().where(
                (self.table.c.url == url) & 
                (self.table.c.document_type == document_type)
            )
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            await conn.close()
    
    async def get_by_retrieved_url(self, retrieved_url: str, document_type: Literal["tos", "pp"]) -> Optional[Dict[str, Any]]:
        """Get a document by retrieved URL and type."""
        conn = await engine.connect()
        try:
            query = self.table.select().where(
                (self.table.c.retrieved_url == retrieved_url) & 
                (self.table.c.document_type == document_type)
            )
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            await conn.close()
    
    async def increment_views(self, id: UUID) -> Optional[Dict[str, Any]]:
        """Increment the view counter for a document."""
        conn = await engine.connect()
        try:
            query = (
                self.table.update()
                .where(self.table.c.id == id)
                .values(views=self.table.c.views + 1)
                .returning(*self.table.columns)
            )
            result = await conn.execute(query)
            await conn.commit()
            row = result.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            await conn.close()
    
    async def search_documents(
        self, 
        search_text: str, 
        document_type: Optional[Literal["tos", "pp"]] = None,
        page: int = 1,
        per_page: int = 20
    ) -> Dict[str, Any]:
        """
        Search documents by text content with pagination.
        
        Parameters:
        - search_text: Text to search for
        - document_type: Optional filter by document type
        - page: Page number (1-based)
        - per_page: Number of items per page
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        conn = await engine.connect()
        try:
            # Calculate offset
            offset = (page - 1) * per_page
            
            # Build the base query
            base_query = self.table.select().order_by(self.table.c.created_at.desc())
            
            # Add search condition
            search_condition = or_(
                self.table.c.raw_text.ilike(f'%{search_text}%'),
                self.table.c.one_sentence_summary.ilike(f'%{search_text}%'),
                self.table.c.hundred_word_summary.ilike(f'%{search_text}%'),
                self.table.c.url.ilike(f'%{search_text}%'),
                self.table.c.company_name.ilike(f'%{search_text}%')
            )
            
            # Add document type filter if provided
            if document_type:
                base_query = base_query.where(
                    (self.table.c.document_type == document_type) & 
                    search_condition
                )
            else:
                base_query = base_query.where(search_condition)
            
            # Count query for pagination
            count_query = self.table.count().where(
                search_condition if not document_type else
                (self.table.c.document_type == document_type) & search_condition
            )
            
            # Get total count
            total_count = await conn.scalar(count_query)
            
            # Apply pagination
            query = base_query.offset(offset).limit(per_page)
            
            # Execute query
            result = await conn.execute(query)
            items = [dict(row) for row in result.fetchall()]
            
            # Calculate pagination info
            total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 0
            
            return {
                "items": items,
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        finally:
            await conn.close()
    
    async def get_documents(
        self,
        document_type: Optional[Literal["tos", "pp"]] = None,
        sort_by: str = "most_recent",
        page: int = 1,
        per_page: int = 6,
        search_text: str = ""
    ) -> Dict[str, Any]:
        """
        Get documents with sorting, filtering, and pagination
        
        Parameters:
        - document_type: Filter by document type (tos or pp)
        - sort_by: Sorting option (most_recent, oldest_first, a_to_z, z_to_a, most_viewed)
        - page: Page number (1-based)
        - per_page: Number of items per page (6, 9, 12, or 15)
        - search_text: Optional search text for filtering
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        conn = await engine.connect()
        try:
            # Validate parameters
            valid_per_page = [6, 9, 12, 15]
            if per_page not in valid_per_page:
                per_page = 6
                
            if page < 1:
                page = 1
                
            # Calculate offset
            offset = (page - 1) * per_page
            
            # Base query for filtering
            base_query = self.table.select()
            
            # Add filters
            filters = []
            
            if document_type:
                filters.append(self.table.c.document_type == document_type)
            
            if search_text:
                search_condition = or_(
                    self.table.c.url.ilike(f'%{search_text}%'),
                    self.table.c.raw_text.ilike(f'%{search_text}%'),
                    self.table.c.one_sentence_summary.ilike(f'%{search_text}%'),
                    self.table.c.hundred_word_summary.ilike(f'%{search_text}%'),
                    self.table.c.company_name.ilike(f'%{search_text}%')
                )
                filters.append(search_condition)
            
            if filters:
                for f in filters:
                    base_query = base_query.where(f)
            
            # Map sort_by values to column names
            sort_mapping = {
                "most_recent": self.table.c.created_at.desc(),
                "oldest_first": self.table.c.created_at.asc(),
                "a_to_z": self.table.c.url.asc(),
                "z_to_a": self.table.c.url.desc(),
                "most_viewed": self.table.c.views.desc(),
            }
            
            # Apply sorting
            if sort_by in sort_mapping:
                base_query = base_query.order_by(sort_mapping[sort_by])
            else:  # Default to most_recent
                base_query = base_query.order_by(self.table.c.created_at.desc())
            
            # Get total count for pagination
            count_query = self.table.count()
            if filters:
                for f in filters:
                    count_query = count_query.where(f)
            
            total_count = await conn.scalar(count_query)
            
            # Apply pagination
            query = base_query.offset(offset).limit(per_page)
            
            # Execute query
            result = await conn.execute(query)
            items = [dict(row) for row in result.fetchall()]
            
            # Calculate pagination info
            total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 0
            
            return {
                "items": items,
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages,
                "has_next": page < total_pages,
                "has_prev": page > 1
            }
        finally:
            await conn.close()


# Create an instance of CRUDDocument for use throughout the application
document_crud = CRUDDocument(documents) 