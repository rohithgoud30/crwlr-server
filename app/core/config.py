from typing import List, Union, Any

from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True
    )

    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "CRWLR API"
    ENVIRONMENT: str = "development"
    
    # API Keys
    GEMINI_API_KEY: str = ""
    API_KEY: str = ""
    
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


settings = Settings() 