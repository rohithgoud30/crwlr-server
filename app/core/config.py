import logging
import os
from typing import List, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra fields from environment variables
    )

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "CRWLR API"
    ENVIRONMENT: str = "production"

    # Database settings
    NEON_DATABASE_URL: Optional[str] = None

    # API Keys - try to get them directly from os.environ first, then from .env
    GEMINI_API_KEY: Optional[str] = None
    API_KEY: Optional[str] = None

    SUMMARY_PROVIDER: str = "google"
    SUMMARY_MODEL: Optional[str] = None
    GOOGLE_SUMMARY_MODEL: str = "gemini-2.0-flash-lite"
    ZAI_API_KEY: Optional[str] = None
    ZAI_BASE_URL: str = "https://api.z.ai/api/coding/paas/v4"
    ZAI_MODEL: str = "GLM-4.5-Air"
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

# Load Neon database URL directly from environment if not already set
if not settings.NEON_DATABASE_URL and os.environ.get("NEON_DATABASE_URL"):
    settings.NEON_DATABASE_URL = os.environ.get("NEON_DATABASE_URL")
    logger.info("Loaded NEON_DATABASE_URL from environment variables")

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
logger.info(f"Neon database URL: {'SET' if settings.NEON_DATABASE_URL else 'NOT SET'}")
