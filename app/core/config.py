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
    ENVIRONMENT: str = "development"
    
    # Database settings
    DB_USER: Optional[str] = None
    DB_PASS: Optional[str] = None
    DB_NAME: Optional[str] = None
    DB_HOST: Optional[str] = None
    DB_PORT: Optional[str] = "5432"
    INSTANCE_CONNECTION_NAME: Optional[str] = None
    
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

# Try to load database settings directly from environment
if not settings.DB_USER and os.environ.get("DB_USER"):
    settings.DB_USER = os.environ.get("DB_USER")
    logger.info("Loaded DB_USER from environment variables")

if not settings.DB_PASS and os.environ.get("DB_PASS"):
    settings.DB_PASS = os.environ.get("DB_PASS")
    logger.info("Loaded DB_PASS from environment variables")

if not settings.DB_NAME and os.environ.get("DB_NAME"):
    settings.DB_NAME = os.environ.get("DB_NAME")
    logger.info("Loaded DB_NAME from environment variables")

if not settings.INSTANCE_CONNECTION_NAME and os.environ.get("INSTANCE_CONNECTION_NAME"):
    settings.INSTANCE_CONNECTION_NAME = os.environ.get("INSTANCE_CONNECTION_NAME")
    logger.info("Loaded INSTANCE_CONNECTION_NAME from environment variables")

if not settings.DB_HOST and os.environ.get("DB_HOST"):
    settings.DB_HOST = os.environ.get("DB_HOST")
    logger.info("Loaded DB_HOST from environment variables")

if not settings.DB_PORT and os.environ.get("DB_PORT"):
    settings.DB_PORT = os.environ.get("DB_PORT")
    logger.info("Loaded DB_PORT from environment variables")

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
logger.info(f"Environment: {settings.ENVIRONMENT}") 