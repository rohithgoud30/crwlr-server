from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
import subprocess
import os
import re
import logging
import asyncio

from app.api.v1.api import api_router, test_router
from app.core.config import settings
from app.core.database import create_tables
# Import auth_manager for Playwright initialization
from app.api.v1.endpoints.extract import auth_manager

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reduce logging for specific libraries
logging.getLogger("httpx").setLevel(logging.WARNING)  # Hide HTTP client logs
logging.getLogger("httpcore").setLevel(logging.WARNING)  # Hide HTTP core logs

# Background task to periodically clean up browser tabs
async def cleanup_browser_tabs():
    """Background task to clean up any stale browser tabs."""
    try:
        while True:
            await asyncio.sleep(300)  # Run every 5 minutes
            if hasattr(auth_manager, 'cleanup_stale_pages'):
                try:
                    logger.info("Running scheduled browser tab cleanup")
                    await auth_manager.cleanup_stale_pages()
                except Exception as e:
                    logger.error(f"Error in scheduled browser tab cleanup: {e}")
    except asyncio.CancelledError:
        logger.info("Browser tab cleanup task cancelled")
    except Exception as e:
        logger.error(f"Unexpected error in browser tab cleanup task: {e}")

# Get current branch name for API documentation
def get_branch_name():
    try:
        # First check if the environment variable is set (typically in production)
        branch = os.environ.get("BRANCH_NAME")
        if branch:
            return branch
        
        # Check if we're in GitHub Actions
        github_ref = os.environ.get("GITHUB_REF")
        if github_ref:
            # Extract branch name from GITHUB_REF (refs/heads/main → main)
            match = re.search(r'refs/heads/(.+)', github_ref)
            if match:
                return match.group(1)
            return github_ref.split('/')[-1]  # Fallback extraction
            
        # Otherwise try to get it from git (for local development)
        result = subprocess.run(
            ["git", "branch", "--show-current"], 
            capture_output=True, 
            text=True, 
            check=False
        )
        branch = result.stdout.strip()
        return branch if branch else "main"  # Default to "main" if unknown
    except:
        return "main"  # Default to "main" if any error occurs

# Define lifespan context manager for app startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Context manager for FastAPI app lifespan.
    Handles startup and shutdown events.
    """
    # Startup: Initialize database
    cleanup_task = None
    try:
        create_tables()
        logger.info("Database tables setup complete")
        
        # Initialize Playwright browser
        try:
            await auth_manager.startup()
            logger.info("Playwright browser started successfully")
            
            # Start background task for tab cleanup
            cleanup_task = asyncio.create_task(cleanup_browser_tabs())
            logger.info("Started background task for browser tab cleanup")
        except Exception as e:
            logger.error(f"Error starting Playwright browser: {e}")
    except Exception as e:
        logger.error(f"Error setting up database tables: {e}")
    
    yield  # This is where the app runs
    
    # Shutdown: Add any cleanup here if needed
    # Code after the yield will be executed on shutdown
    logger.info("Shutting down application")
    
    # Cancel the background cleanup task
    if cleanup_task:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
    
    # Shutdown Playwright browser
    try:
        await auth_manager.shutdown()
        logger.info("Playwright browser shut down successfully")
    except Exception as e:
        logger.error(f"Error shutting down Playwright browser: {e}")

branch_name = get_branch_name()
# Ensure we don't have raw template strings in the version
if "${" in branch_name or "}" in branch_name:
    branch_name = "main"  # Default to main if we detect template variables
    
version_suffix = f" ({branch_name})" if branch_name else ""

app = FastAPI(
    title=f"{settings.PROJECT_NAME}{version_suffix}",
    description=f"API Documentation for CRWLR Server. Current branch: **{branch_name}**\n\n"
                f"### Authentication\n"
                f"This API is protected with API Key authentication.\n"
                f"Include the `X-API-Key` header with your API key in all requests.",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    version="1.0.0",
    lifespan=lifespan,  # Add lifespan context manager
)

# Add middleware to log requests and headers
@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Log the request
    logger.info(f"Request: {request.method} {request.url}")
    
    # Log all headers
    headers_list = []
    for header_name, header_value in request.headers.items():
        # Mask API key for security
        if header_name.lower() == "x-api-key":
            header_value = "*****" if header_value else "Not provided"
        headers_list.append(f"{header_name}: {header_value}")
    
    if headers_list:
        logger.info("Headers: \n" + "\n".join(headers_list))
    
    # Process the request
    response = await call_next(request)
    return response

# Set up CORS middleware with explicit origins
origins = settings.BACKEND_CORS_ORIGINS  # Use settings from config

# If CORS origins are empty or we're not in development, add some defaults
if not origins or settings.ENVIRONMENT != "development":
    origins = [
        "http://localhost:3000",  # React app
        "http://127.0.0.1:3000", 
        "http://localhost:8000",  # API docs
        "http://127.0.0.1:8000",
        "*",  # Allow all origins in non-development environments
    ]
    # In production we want to allow all origins since this is an API service
    if settings.ENVIRONMENT == "production":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# Include API router with authentication
app.include_router(api_router, prefix=settings.API_V1_STR)

# Include test router without authentication
app.include_router(test_router, prefix=settings.API_V1_STR)

@app.get("/", include_in_schema=False)
def root():
    """
    Redirect root to API docs.
    """
    return RedirectResponse(url="/docs") 