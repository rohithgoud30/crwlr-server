from fastapi import Security, HTTPException, Depends, status
from fastapi.security.api_key import APIKeyHeader
import logging
import os

from app.core.config import settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY_NAME = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

# Get environment
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

async def get_api_key(api_key_header: str = Security(api_key_header)):
    """
    Validate the API key.
    """
    configured_api_key = settings.API_KEY
    masked_key = f"{configured_api_key[:3]}{'*' * (len(configured_api_key) - 3)}" if configured_api_key else "NOT SET"
    logger.info(f"Received API key: {'PROVIDED' if api_key_header else 'NOT PROVIDED'}")
    logger.info(f"Configured API key: {masked_key}")
    
    # In development mode, if no API key is configured, accept "test" as API key for debugging
    if ENVIRONMENT == "development" and not configured_api_key:
        logger.warning("No API key configured. Running in development mode with relaxed security.")
        if api_key_header == "test":
            return api_key_header
    
    # For testing purposes, also accept "test_api_key" in development mode
    if ENVIRONMENT == "development" and api_key_header == "test_api_key":
        logger.warning("Using test API key for development mode")
        return api_key_header
        
    if api_key_header == configured_api_key:
        return api_key_header
        
    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is required. Add X-API-Key header."
        )
        
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key"
    ) 