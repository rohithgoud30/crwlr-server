from typing import Optional, Dict, Any, List, Literal
from uuid import UUID
from sqlalchemy import desc, asc, text, or_, select, func

from app.crud.base import CRUDBase
from app.core.database import documents, async_engine


class CRUDDocument(CRUDBase):
    """CRUD operations for documents."""
    
    async def get_by_url(self, url: str, document_type: Literal["tos", "pp"]) -> Optional[Dict[str, Any]]:
        """Get a document by URL and type."""
        async with async_engine.connect() as conn:
            query = self.table.select().where(
                (self.table.c.url == url) & 
                (self.table.c.document_type == document_type)
            )
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
    async def get_by_retrieved_url(self, retrieved_url: str, document_type: Literal["tos", "pp"]) -> Optional[Dict[str, Any]]:
        """Get a document by retrieved URL and type."""
        async with async_engine.connect() as conn:
            query = self.table.select().where(
                (self.table.c.retrieved_url == retrieved_url) & 
                (self.table.c.document_type == document_type)
            )
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
    async def increment_views(self, id: UUID) -> Optional[Dict[str, Any]]:
        """Increment the view counter for a document."""
        async with async_engine.begin() as conn:
            query = (
                self.table.update()
                .where(self.table.c.id == id)
                .values(views=self.table.c.views + 1)
                .returning(*self.table.columns)
            )
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
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
        async with async_engine.connect() as conn:
            # Validate and normalize parameters
            if page < 1:
                page = 1
                
            # Calculate offset
            offset = (page - 1) * per_page
            
            # Base query
            if document_type:
                # Construct the WHERE clause for document_type and full-text search
                base_query = text(f"""
                    SELECT * FROM documents 
                    WHERE document_type = :doc_type
                    AND (
                        raw_text ILIKE :search_text
                        OR company_name ILIKE :search_text
                        OR url ILIKE :search_text
                        OR one_sentence_summary ILIKE :search_text
                        OR hundred_word_summary ILIKE :search_text
                    )
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """)
                count_query = text(f"""
                    SELECT COUNT(*) FROM documents 
                    WHERE document_type = :doc_type
                    AND (
                        raw_text ILIKE :search_text
                        OR company_name ILIKE :search_text
                        OR url ILIKE :search_text
                        OR one_sentence_summary ILIKE :search_text
                        OR hundred_word_summary ILIKE :search_text
                    )
                """)
                params = {
                    "doc_type": document_type,
                    "search_text": f"%{search_text}%",
                    "limit": per_page,
                    "offset": offset
                }
            else:
                # Search across all document types
                base_query = text(f"""
                    SELECT * FROM documents 
                    WHERE 
                        raw_text ILIKE :search_text
                        OR company_name ILIKE :search_text
                        OR url ILIKE :search_text
                        OR one_sentence_summary ILIKE :search_text
                        OR hundred_word_summary ILIKE :search_text
                    ORDER BY created_at DESC
                    LIMIT :limit OFFSET :offset
                """)
                count_query = text(f"""
                    SELECT COUNT(*) FROM documents 
                    WHERE 
                        raw_text ILIKE :search_text
                        OR company_name ILIKE :search_text
                        OR url ILIKE :search_text
                        OR one_sentence_summary ILIKE :search_text
                        OR hundred_word_summary ILIKE :search_text
                """)
                params = {
                    "search_text": f"%{search_text}%",
                    "limit": per_page,
                    "offset": offset
                }
            
            # Get total count for pagination
            count_result = await conn.execute(count_query, params)
            total_count = count_result.scalar() or 0
            
            # Execute query
            result = await conn.execute(base_query, params)
            rows = result.fetchall()
            items = [dict(row) for row in rows]
            
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
    
    async def get_documents(
        self,
        page: int = 1,
        per_page: int = 20,
        document_type: Optional[Literal["tos", "pp"]] = None,
        order_by: str = "created_at"
    ) -> Dict[str, Any]:
        """
        Get documents with pagination and optional filtering.
        
        Parameters:
        - page: Page number (1-based)
        - per_page: Number of items per page
        - document_type: Optional filter by document type
        - order_by: Column name to sort by
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        async with async_engine.connect() as conn:
            # Validate parameters
            if page < 1:
                page = 1
                
            # Calculate offset
            offset = (page - 1) * per_page
            
            # Build the base query
            base_query = self.table.select().order_by(self.table.c.created_at.desc())
            count_query = select(func.count()).select_from(self.table)
            
            # Add document type filter if provided
            if document_type:
                base_query = base_query.where(self.table.c.document_type == document_type)
                count_query = count_query.where(self.table.c.document_type == document_type)
            
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


# Create an instance of CRUDDocument for use throughout the application
document_crud = CRUDDocument(documents) 