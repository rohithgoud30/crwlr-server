from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import subprocess
import os

from app.api.v1.api import api_router
from app.core.config import settings

# Get current branch name for API documentation
def get_branch_name():
    try:
        # First check if the environment variable is set (typically in production)
        branch = os.environ.get("BRANCH_NAME")
        if branch:
            return branch
            
        # Otherwise try to get it from git (for local development)
        result = subprocess.run(
            ["git", "branch", "--show-current"], 
            capture_output=True, 
            text=True, 
            check=False
        )
        branch = result.stdout.strip()
        return branch if branch else "unknown"
    except:
        return "unknown"

branch_name = get_branch_name()
version_suffix = f" ({branch_name})" if branch_name else ""

app = FastAPI(
    title=f"{settings.PROJECT_NAME}{version_suffix}",
    description=f"API Documentation for CRWLR Server. Current branch: **{branch_name}**",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    version="1.0.0",
)

# Set up CORS middleware with explicit origins
origins = [
    "http://localhost:3000",  # React app
    "http://127.0.0.1:3000",
    "http://localhost:8000",  # API docs
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/", include_in_schema=False)
def root():
    """
    Redirect root to API docs.
    """
    return RedirectResponse(url="/docs") 