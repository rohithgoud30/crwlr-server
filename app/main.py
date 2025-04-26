from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
import os
import logging
import sys  # Import sys
import asyncio # Import asyncio
# import git # Removed GitPython import

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

# ---> REVERTED: Get branch name from environment variable
branch_name = os.environ.get("BRANCH_NAME", "local") # Default to 'local' if not set
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
    return JSONResponse(content={"status": "running"})

# Root endpoint
@app.get("/", include_in_schema=False)
def root():
    """
    Root endpoint, redirects to API documentation.
    """
    return RedirectResponse(url="/docs")

# Simple API endpoint
@app.get("/api/v1/status")
async def status():
    """
    Return status information.
    """
    env_vars = {
        "PROJECT_ID": os.environ.get("PROJECT_ID", "Not set"),
        "ENVIRONMENT": os.environ.get("ENVIRONMENT", "Not set"),
        "API_KEY": "Set" if os.environ.get("API_KEY") else "Not set",
        "GEMINI_API_KEY": "Set" if os.environ.get("GEMINI_API_KEY") else "Not set",
        "DB_USER": "Set" if os.environ.get("DB_USER") else "Not set",
        "DB_NAME": "Set" if os.environ.get("DB_NAME") else "Not set",
        "DB_HOST": "Set" if os.environ.get("DB_HOST") else "Not set",
        "USE_CLOUD_SQL_PROXY": os.environ.get("USE_CLOUD_SQL_PROXY", "Not set"),
        "INSTANCE_CONNECTION_NAME": os.environ.get("INSTANCE_CONNECTION_NAME", "Not set"),
        "DB_IP_ADDRESS": os.environ.get("DB_IP_ADDRESS", "Not set")
    }
    
    return {
        "status": "running",
        "mode": os.environ.get("ENVIRONMENT", "development"),
        "environment_variables_status": env_vars
    }

# Log API load
logger.info("CRWLR API loaded successfully")
