from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union
from uuid import UUID
from sqlalchemy import Table, select, insert, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import async_engine

ModelType = TypeVar("ModelType")


class CRUDBase:
    """Base class for CRUD operations."""
    
    def __init__(self, table: Table):
        """Initialize with a table."""
        self.table = table
    
    async def get(self, id: UUID) -> Optional[Dict[str, Any]]:
        """Get a record by ID."""
        async with async_engine.connect() as conn:
            query = select(self.table).where(self.table.c.id == id)
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
    async def get_multi(
        self, 
        skip: int = 0, 
        limit: int = 100,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get multiple records with optional filtering."""
        async with async_engine.connect() as conn:
            query = select(self.table).offset(skip).limit(limit)
            
            # Apply filters if provided
            if filters:
                for field, value in filters.items():
                    if hasattr(self.table.c, field):
                        query = query.where(getattr(self.table.c, field) == value)
                        
            result = await conn.execute(query)
            return [dict(row) for row in result.fetchall()]
    
    async def create(self, obj_in: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new record."""
        async with async_engine.begin() as conn:
            query = insert(self.table).values(**obj_in).returning(*self.table.columns)
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return {}
    
    async def update(self, id: UUID, obj_in: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a record by ID."""
        async with async_engine.begin() as conn:
            query = update(self.table).where(self.table.c.id == id).values(**obj_in).returning(*self.table.columns)
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
    async def remove(self, id: UUID) -> Optional[Dict[str, Any]]:
        """Delete a record by ID."""
        async with async_engine.begin() as conn:
            query = delete(self.table).where(self.table.c.id == id).returning(*self.table.columns)
            result = await conn.execute(query)
            row = result.fetchone()
            if row:
                return dict(row)
            return None
    
    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records with optional filtering."""
        async with async_engine.connect() as conn:
            query = select(func.count()).select_from(self.table)
            
            # Apply filters if provided
            if filters:
                for field, value in filters.items():
                    if hasattr(self.table.c, field):
                        query = query.where(getattr(self.table.c, field) == value)
                        
            result = await conn.execute(query)
            return result.scalar() or 0
            
    async def paginate(
        self,
        page: int = 1,
        per_page: int = 20,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        order_direction: str = "desc",
        valid_per_page: List[int] = [6, 9, 12, 15, 20]
    ) -> Dict[str, Any]:
        """
        Common pagination method for all models
        
        Parameters:
        - page: Page number (1-based)
        - per_page: Number of items per page
        - filters: Dict of field:value pairs for filtering
        - order_by: Column name to sort by
        - order_direction: "asc" or "desc"
        - valid_per_page: List of valid page sizes
        
        Returns:
        - Dictionary with items, total count, and pagination info
        """
        async with async_engine.connect() as conn:
            # Validate and normalize parameters
            if per_page not in valid_per_page:
                per_page = valid_per_page[0]
                
            if page < 1:
                page = 1
                
            # Calculate offset
            offset = (page - 1) * per_page
            
            # Base query
            base_query = select(self.table)
            count_query = select(func.count()).select_from(self.table)
            
            # Apply filters if any
            if filters:
                for field, value in filters.items():
                    if hasattr(self.table.c, field):
                        condition = getattr(self.table.c, field) == value
                        base_query = base_query.where(condition)
                        count_query = count_query.where(condition)
            
            # Apply ordering
            if order_by and hasattr(self.table.c, order_by):
                column = getattr(self.table.c, order_by)
                if order_direction.lower() == "asc":
                    base_query = base_query.order_by(column.asc())
                else:
                    base_query = base_query.order_by(column.desc())
            else:
                # Default ordering by created_at if it exists
                if hasattr(self.table.c, "created_at"):
                    base_query = base_query.order_by(self.table.c.created_at.desc())
            
            # Get total count for pagination
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