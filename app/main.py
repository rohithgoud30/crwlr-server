from fastapi import FastAPI, Request, HTTPException
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

# Import Firebase (new)
from app.core.firebase import initialize_firebase, db

# Import environment validator
from app.core.env_checker import validate_environment

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

# Variables to track initialization status
environment_valid = False
firebase_initialized = False
playwright_initialized = False
startup_errors = []

# ---> ADDED: Startup and Shutdown Events for Playwright and Firebase
@app.on_event("startup")
async def startup_event():
    """Application startup event handler that initializes all required services."""
    global environment_valid, firebase_initialized, playwright_initialized, startup_errors
    
    logger.info("Application startup: Beginning initialization sequence...")
    
    # STEP 1: Validate environment variables first
    logger.info("STEP 1: Validating environment variables...")
    try:
        environment_valid = validate_environment()
        if not environment_valid:
            error_msg = "Environment validation failed - missing required variables"
            logger.error(error_msg)
            startup_errors.append(error_msg)
            # We will continue with initialization but log warnings
    except Exception as e:
        error_msg = f"Environment validation error: {str(e)}"
        logger.error(error_msg)
        startup_errors.append(error_msg)
    
    # STEP 2: Initialize Firebase
    logger.info("STEP 2: Initializing Firebase...")
    try:
        # Only proceed if environment is valid or in dev mode
        if environment_valid or settings.ENVIRONMENT == "development":
            # Force initialization in development mode
            force_init = settings.ENVIRONMENT == "development"
            initialize_firebase(force_init=force_init)
            if db:
                logger.info("Firebase initialization successful.")
                firebase_initialized = True
            else:
                error_msg = "Firebase initialization completed but db is None"
                logger.error(error_msg)
                startup_errors.append(error_msg)
        else:
            error_msg = "Skipping Firebase initialization due to invalid environment"
            logger.warning(error_msg)
            startup_errors.append(error_msg)
    except Exception as e:
        error_msg = f"Firebase initialization failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        startup_errors.append(error_msg)
    
    # STEP 3: Initialize Playwright
    logger.info("STEP 3: Initializing Playwright...")
    try:
        # Start Playwright with a timeout
        await asyncio.wait_for(auth_manager.startup(), timeout=90.0) # 90 second timeout
        logger.info("Playwright startup successful.")
        playwright_initialized = True
    except asyncio.TimeoutError:
        error_msg = "Playwright startup timed out after 90 seconds."
        logger.error(error_msg)
        startup_errors.append(error_msg)
        auth_manager.startup_failure = "Startup timed out"
    except Exception as e:
        error_msg = f"Playwright initialization failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        startup_errors.append(error_msg)
        if not auth_manager.startup_failure:
            auth_manager.startup_failure = str(e)
    
    # Log summary of startup status
    logger.info("===== STARTUP SUMMARY =====")
    logger.info(f"Environment validation: {'✅ PASSED' if environment_valid else '❌ FAILED'}")
    logger.info(f"Firebase initialization: {'✅ PASSED' if firebase_initialized else '❌ FAILED'}")
    logger.info(f"Playwright initialization: {'✅ PASSED' if playwright_initialized else '❌ FAILED'}")
    
    if startup_errors:
        logger.warning(f"Startup completed with {len(startup_errors)} errors/warnings")
    else:
        logger.info("✅ All services initialized successfully")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Application shutdown: Shutting down services...")
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
    Returns status of all initialized services.
    """
    firestore_status = "connected" if db else "disconnected"
    
    status = {
        "status": "running", 
        "branch": branch_name,
        "environment_check": "passed" if environment_valid else "failed",
        "firestore": firestore_status,
        "playwright": "ready" if playwright_initialized else "not_ready"
    }
    
    if startup_errors:
        status["startup_warnings"] = len(startup_errors)
        
    return JSONResponse(content=status)

# Detailed status endpoint for debugging
@app.get("/debug/status", include_in_schema=False)
async def debug_status():
    """
    Detailed status endpoint for debugging.
    Shows full initialization status and errors.
    """
    # Only accessible in development mode for security
    if settings.ENVIRONMENT != "development" and settings.ENVIRONMENT != "local":
        raise HTTPException(status_code=403, detail="Forbidden in production mode")
    
    status = {
        "status": "running", 
        "branch": branch_name,
        "environment": settings.ENVIRONMENT,
        "services": {
            "environment_check": {
                "status": "passed" if environment_valid else "failed"
            },
            "firebase": {
                "status": "initialized" if firebase_initialized else "failed",
                "db_available": db is not None
            },
            "playwright": {
                "status": "ready" if playwright_initialized else "not_ready",
                "startup_failure": auth_manager.startup_failure if hasattr(auth_manager, "startup_failure") else None
            }
        },
        "startup_errors": startup_errors
    }
        
    return JSONResponse(content=status)

# Root endpoint
@app.get("/", include_in_schema=False)
def root():
    """
    Root endpoint, redirects to API documentation.
    """
    return RedirectResponse(url="/docs")

# Log API load
logger.info(f"CRWLR API ({branch_name}) loaded successfully")
