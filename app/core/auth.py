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
    Validate API key from header.
    """
    # Log information about the environment and API keys
    configured_api_key = settings.API_KEY
    masked_key = f"{configured_api_key[:3]}{'*' * (len(configured_api_key) - 3)}" if configured_api_key else "NOT SET"
    
    logger.info(f"Environment: {ENVIRONMENT}")
    logger.info(f"Configured API key (masked): {masked_key}")
    logger.info(f"Received API key: {'PROVIDED' if api_key_header else 'NOT PROVIDED'}")
    
    # In development mode, if API_KEY is empty, allow all requests
    if ENVIRONMENT == "development" and not configured_api_key:
        logger.warning("⚠️ DEVELOPMENT MODE: No API key configured - allowing request")
        return "development_mode"
    
    # Handle proper validation
    if api_key_header == configured_api_key:
        logger.info("✅ API key validation successful")
        return api_key_header
        
    # If no API key was provided in the header
    if not api_key_header:
        logger.warning("❌ No API key provided in request header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is required. Add X-API-Key header.",
        )
    
    # If API key was provided but doesn't match
    logger.warning("❌ Invalid API key provided")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    ) 