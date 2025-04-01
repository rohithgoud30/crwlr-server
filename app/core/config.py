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
    
    # BACKEND_CORS_ORIGINS is a comma-separated list of origins
    BACKEND_CORS_ORIGINS: List[str] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            # Handle JSON format
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    return json.loads(v)
                except Exception:
                    pass
            
            # Handle comma-separated format
            return [i.strip() for i in v.split(",") if i.strip()]
        return v or []


settings = Settings() 