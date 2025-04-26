from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
import os
import logging
import sys  # Import sys
import asyncio # Import asyncio
import subprocess # For git command execution

# Import the API routers
from app.api.v1.api import api_router, test_router
# ---> ADDED: Import the Playwright Manager
from app.api.v1.endpoints.extract import auth_manager 

# Import settings
from app.core.config import settings

# Setup logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
# )

# ---> MODIFIED: More explicit logging configuration for Cloud Run
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Get the root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Remove existing handlers if any (to avoid duplicates)
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

# Add a stream handler to stdout
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(log_formatter)
root_logger.addHandler(stream_handler)


logger = logging.getLogger(__name__)

# ---> ADDED: Log after configuration to confirm setup
logger.info("Logging configured to stream to stdout.")

# ---> Improved branch name detection without external dependencies
def get_branch_name():
    """Get the current Git branch name using environment variable or git command"""
    # First try environment variable (will be set in Cloud Run)
    branch_from_env = os.environ.get("BRANCH_NAME")
    if branch_from_env and branch_from_env != "unknown":
        logger.info(f"Using branch name from environment: {branch_from_env}")
        return branch_from_env
    
    # Try to detect using git command directly
    try:
        # Use subprocess to execute git command
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False  # Don't raise exception
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                logger.info(f"Detected git branch: {branch}")
                return branch
    except Exception as e:
        logger.warning(f"Error executing git command: {str(e)}")
    
    # Default fallback
    logger.warning("Using default branch name: local")
    return "local"

branch_name = get_branch_name()
app_title = f"{settings.PROJECT_NAME} ({branch_name})"

# Create the FastAPI app
app = FastAPI(
    title=app_title,
    description="CRWLR API for processing website terms and privacy policies.",
    version="1.0.0",
)

# ---> ADDED: Startup and Shutdown Events for Playwright
@app.on_event("startup")
async def startup_event():
    logger.info("Application startup: Initializing Playwright...")
    try:
        # Start Playwright with a timeout
        await asyncio.wait_for(auth_manager.startup(), timeout=90.0) # 90 second timeout
        logger.info("Playwright startup successful.")
    except asyncio.TimeoutError:
        logger.error("Playwright startup timed out after 90 seconds.")
        # Optionally prevent app startup or set a flag
        auth_manager.startup_failure = "Startup timed out"
    except Exception as e:
        logger.error(f"Playwright startup failed during app startup: {str(e)}", exc_info=True)
        # Ensure startup_failure reflects the error
        if not auth_manager.startup_failure:
            auth_manager.startup_failure = str(e)

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown: Shutting down Playwright...")
    await auth_manager.shutdown()
    logger.info("Playwright shut down complete.")

# Set up CORS middleware with explicit origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the API routers
app.include_router(api_router, prefix="/api/v1")
app.include_router(test_router, prefix="/api/test")

# Add a health check endpoint
@app.get("/health", include_in_schema=False)
async def health_check():
    """
    Simple health check endpoint.
    """
    return JSONResponse(content={"status": "running", "branch": branch_name})

# Root endpoint
@app.get("/", include_in_schema=False)
def root():
    """
    Root endpoint, redirects to API documentation.
    """
    return RedirectResponse(url="/docs")

# Log API load
logger.info(f"CRWLR API ({branch_name}) loaded successfully")
