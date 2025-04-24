from fastapi import APIRouter
import logging
from sqlalchemy.sql import text

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    Returns the status of the API and confirms database connectivity.
    """
    try:
        # Import here to avoid circular imports
        from app.core.database import engine
        
        # Verify the database connection is working with a simple query
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            db_status = "connected" if result.scalar() == 1 else "error"
    except Exception as e:
        logger.error(f"Database connection error in health check: {str(e)}")
        db_status = "error"
    
    return {
        "status": "healthy",
        "database": db_status,
        "api_version": "1.0.0"
    } 