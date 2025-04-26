from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
import os
import logging

# Import the API routers
from app.api.v1.api import api_router, test_router

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create the FastAPI app
app = FastAPI(
    title="CRWLR API",
    description="CRWLR API for processing website terms and privacy policies.",
    version="1.0.0",
)

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
