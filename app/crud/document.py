from typing import Optional, Dict, Any, List, Literal
from uuid import UUID
from sqlalchemy import desc, asc, text, or_, select, func

from app.crud.base import CRUDBase
from app.core.database import documents, async_engine

# Define the fields to return for document list/search results
DOCUMENT_LIST_FIELDS = [
    documents.c.id,
    documents.c.url,
    documents.c.document_type,
    documents.c.company_name,
    documents.c.logo_url,
    documents.c.views,
    documents.c.updated_at
]


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
                return dict(row._mapping)
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
                return dict(row._mapping)
            return None
    
    async def increment_views(self, id: UUID) -> Optional[Dict[str, Any]]:
        """Increment the view counter for a document."""
        async with async_engine.begin() as conn:
            # Return all fields after incrementing
            query = (
                self.table.update()
                .where(self.table.c.id == id)
                .values(views=self.table.c.views + 1)
                .returning(*self.table.columns)
            )
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row._mapping)
            return None
    
    async def search_by_company_name(
        self,
        company_name: str,
        document_type: Optional[Literal["tos", "pp"]] = None,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        Search documents by company name with pagination and sorting.
        Returns only selected fields for list view.
        """
        async with async_engine.connect() as conn:
            # Validate and normalize parameters
            if page < 1:
                page = 1
                
            valid_columns = [
                "id", "url", "document_type", "retrieved_url", "company_name", 
                "logo_url", "views", "created_at", "updated_at"
            ]
            if sort_by not in valid_columns:
                sort_by = "created_at"
                
            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "desc"
                
            offset = (page - 1) * per_page
            order_clause = f"ORDER BY {sort_by} {sort_order.upper()}"
            
            select_fields_str = ", ".join([f.name for f in DOCUMENT_LIST_FIELDS])
            
            if document_type:
                base_query = text(f"""
                    SELECT {select_fields_str} FROM documents 
                    WHERE document_type = :doc_type
                    AND company_name ILIKE :company_name
                    {order_clause}
                    LIMIT :limit OFFSET :offset
                """)
                count_query = text(f"""
                    SELECT COUNT(*) FROM documents 
                    WHERE document_type = :doc_type
                    AND company_name ILIKE :company_name
                """)
                params = {
                    "doc_type": document_type,
                    "company_name": f"%{company_name}%",
                    "limit": per_page,
                    "offset": offset
                }
            else:
                base_query = text(f"""
                    SELECT {select_fields_str} FROM documents 
                    WHERE company_name ILIKE :company_name
                    {order_clause}
                    LIMIT :limit OFFSET :offset
                """)
                count_query = text(f"""
                    SELECT COUNT(*) FROM documents 
                    WHERE company_name ILIKE :company_name
                """)
                params = {
                    "company_name": f"%{company_name}%",
                    "limit": per_page,
                    "offset": offset
                }
            
            count_result = await conn.execute(count_query, params)
            total_count = count_result.scalar() or 0
            
            result = await conn.execute(base_query, params)
            rows = result.fetchall()
            
            # Map rows to dictionaries using the defined fields - use _mapping if available
            try:
                # First try to use _mapping attribute if available
                items = [dict(row._mapping) for row in rows]
            except (AttributeError, Exception):
                # Fall back to zip method if _mapping isn't available
                items = [dict(zip([col.name for col in DOCUMENT_LIST_FIELDS], row)) for row in rows]
            
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
    
    async def search_documents(
        self, 
        search_text: str, 
        document_type: Optional[Literal["tos", "pp"]] = None,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc"
    ) -> Dict[str, Any]:
        """
        Search documents by text content with pagination and sorting.
        Returns only selected fields for list view.
        """
        async with async_engine.connect() as conn:
            if page < 1:
                page = 1
                
            valid_columns = [
                "id", "url", "document_type", "retrieved_url", "company_name", 
                "logo_url", "views", "created_at", "updated_at"
            ]
            if sort_by not in valid_columns:
                sort_by = "created_at"
                
            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "desc"
                
            offset = (page - 1) * per_page
            order_clause = f"ORDER BY {sort_by} {sort_order.upper()}"
            
            select_fields_str = ", ".join([f.name for f in DOCUMENT_LIST_FIELDS])
            
            # Only match company_name or url - no secondary matching
            search_condition = "(company_name ILIKE :exact_match OR company_name ILIKE :search_text OR " + \
                              "url ILIKE :exact_match OR url ILIKE :search_text)"

            if document_type:
                query = text(f"""
                    SELECT {select_fields_str} FROM documents 
                    WHERE document_type = :doc_type AND {search_condition}
                    {order_clause}
                    LIMIT :limit OFFSET :offset
                """)
                
                count_query = text(f"""
                    SELECT COUNT(*) FROM documents 
                    WHERE document_type = :doc_type AND {search_condition}
                """)
                
                params = {
                    "doc_type": document_type,
                    "exact_match": search_text.strip(),  # Exact match without wildcards
                    "search_text": f"%{search_text}%",   # Contains match with wildcards
                    "limit": per_page,
                    "offset": offset
                }
            else:
                query = text(f"""
                    SELECT {select_fields_str} FROM documents 
                    WHERE {search_condition}
                    {order_clause}
                    LIMIT :limit OFFSET :offset
                """)
                
                count_query = text(f"""
                    SELECT COUNT(*) FROM documents 
                    WHERE {search_condition}
                """)
                
                params = {
                    "exact_match": search_text.strip(),  # Exact match without wildcards
                    "search_text": f"%{search_text}%",   # Contains match with wildcards
                    "limit": per_page,
                    "offset": offset
                }
            
            # Get total count for pagination
            count_result = await conn.execute(count_query, params)
            total_count = count_result.scalar() or 0
            
            # Execute query
            result = await conn.execute(query, params)
            rows = result.fetchall()
            
            # Map rows to dictionaries using the defined fields - use _mapping if available
            try:
                # First try to use _mapping attribute if available
                items = [dict(row._mapping) for row in rows]
            except (AttributeError, Exception):
                # Fall back to zip method if _mapping isn't available
                items = [dict(zip([col.name for col in DOCUMENT_LIST_FIELDS], row)) for row in rows]
            
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
        order_by: str = "created_at",
        order_direction: str = "desc"
    ) -> Dict[str, Any]:
        """
        Get documents with pagination, filtering, and sorting.
        Returns only selected fields for list view.
        """
        async with async_engine.connect() as conn:
            if page < 1:
                page = 1
                
            valid_columns = [
                "id", "url", "document_type", "retrieved_url", "company_name", 
                "logo_url", "views", "created_at", "updated_at"
            ]
            if order_by not in valid_columns:
                order_by = "created_at"
                
            if order_direction.lower() not in ["asc", "desc"]:
                order_direction = "desc"
                
            offset = (page - 1) * per_page
            
            if order_direction.lower() == "asc":
                order_clause = getattr(self.table.c, order_by).asc()
            else:
                order_clause = getattr(self.table.c, order_by).desc()
                
            # Select only the desired fields
            base_query = select(*DOCUMENT_LIST_FIELDS).order_by(order_clause)
            count_query = select(func.count()).select_from(self.table)
            
            if document_type:
                base_query = base_query.where(self.table.c.document_type == document_type)
                count_query = count_query.where(self.table.c.document_type == document_type)
            
            total_count = await conn.scalar(count_query)
            
            query = base_query.offset(offset).limit(per_page)
            
            result = await conn.execute(query)
            rows = result.fetchall()
            
            # Map rows to dictionaries using the defined fields - use _mapping if available
            try:
                # First try to use _mapping attribute if available
                items = [dict(row._mapping) for row in rows]
            except (AttributeError, Exception):
                # Fall back to zip method if _mapping isn't available
                items = [dict(zip([col.name for col in DOCUMENT_LIST_FIELDS], row)) for row in rows]
            
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