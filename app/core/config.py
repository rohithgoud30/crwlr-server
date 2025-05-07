from typing import List, Union, Any, Optional
import os
import logging

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, 
        extra="ignore"  # Ignore extra fields from environment variables
    )

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "CRWLR API"
    ENVIRONMENT: str = "production"
    
    # Firebase settings
    FIREBASE_PROJECT_ID: Optional[str] = None
    FIREBASE_SERVICE_ACCOUNT_PATH: Optional[str] = None
    FIREBASE_CLIENT_EMAIL: Optional[str] = None
    FIREBASE_PRIVATE_KEY: Optional[str] = None
    
    # Algolia settings
    ALGOLIA_APP_ID: Optional[str] = None
    ALGOLIA_API_KEY: Optional[str] = None
    
    # API Keys - try to get them directly from os.environ first, then from .env
    GEMINI_API_KEY: Optional[str] = None
    API_KEY: Optional[str] = None
    
    # BACKEND_CORS_ORIGINS is a comma-separated list of origins
    BACKEND_CORS_ORIGINS: Union[List[str], str] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            try:
                # First try comma-separated format which is safer
                if "," in v:
                    return [i.strip() for i in v.split(",") if i.strip()]
                
                # Then try JSON format
                if v.startswith("[") and v.endswith("]"):
                    import json
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return parsed
            except Exception:
                # If all parsing fails, return as single item
                return [v] if v else []
            
            # If string but no comma or brackets, treat as single origin
            return [v]
            
        # If it's already a list, use it
        if isinstance(v, list):
            return v
            
        # Fallback to empty list
        return []


# Create settings instance
settings = Settings()

# Try to load Firebase settings directly from environment
if not settings.FIREBASE_PROJECT_ID and os.environ.get("FIREBASE_PROJECT_ID"):
    settings.FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID")
    logger.info("Loaded FIREBASE_PROJECT_ID from environment variables")

if not settings.FIREBASE_SERVICE_ACCOUNT_PATH and os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH"):
    settings.FIREBASE_SERVICE_ACCOUNT_PATH = os.environ.get("FIREBASE_SERVICE_ACCOUNT_PATH")
    logger.info("Loaded FIREBASE_SERVICE_ACCOUNT_PATH from environment variables")

if not settings.FIREBASE_CLIENT_EMAIL and os.environ.get("FIREBASE_CLIENT_EMAIL"):
    settings.FIREBASE_CLIENT_EMAIL = os.environ.get("FIREBASE_CLIENT_EMAIL")
    logger.info("Loaded FIREBASE_CLIENT_EMAIL from environment variables")

if not settings.FIREBASE_PRIVATE_KEY and os.environ.get("FIREBASE_PRIVATE_KEY"):
    settings.FIREBASE_PRIVATE_KEY = os.environ.get("FIREBASE_PRIVATE_KEY")
    logger.info("Loaded FIREBASE_PRIVATE_KEY from environment variables")

# Try to load Algolia settings directly from environment
if not settings.ALGOLIA_APP_ID and os.environ.get("ALGOLIA_APP_ID"):
    settings.ALGOLIA_APP_ID = os.environ.get("ALGOLIA_APP_ID")
    logger.info("Loaded ALGOLIA_APP_ID from environment variables")

if not settings.ALGOLIA_API_KEY and os.environ.get("ALGOLIA_API_KEY"):
    settings.ALGOLIA_API_KEY = os.environ.get("ALGOLIA_API_KEY")
    logger.info("Loaded ALGOLIA_API_KEY from environment variables")

# Try to load API keys directly from environment if they're not in settings
if not settings.GEMINI_API_KEY and os.environ.get("GEMINI_API_KEY"):
    settings.GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    logger.info("Loaded GEMINI_API_KEY from environment variables")

if not settings.API_KEY and os.environ.get("API_KEY"):
    settings.API_KEY = os.environ.get("API_KEY")
    logger.info("Loaded API_KEY from environment variables")

# Log whether API keys are set (without printing them)
logger.info(f"GEMINI_API_KEY is {'SET' if settings.GEMINI_API_KEY else 'NOT SET'}")
logger.info(f"API_KEY is {'SET' if settings.API_KEY else 'NOT SET'}")
logger.info(f"ALGOLIA_APP_ID is {'SET' if settings.ALGOLIA_APP_ID else 'NOT SET'}")
logger.info(f"ALGOLIA_API_KEY is {'SET' if settings.ALGOLIA_API_KEY else 'NOT SET'}")
logger.info(f"Environment: {settings.ENVIRONMENT}")
logger.info(f"Firebase Project ID: {settings.FIREBASE_PROJECT_ID if settings.FIREBASE_PROJECT_ID else 'NOT SET'}")