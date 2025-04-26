from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
import os
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create the FastAPI app
app = FastAPI(
    title="CRWLR API (Emergency Mode)",
    description="CRWLR API in minimalist mode to establish container connectivity.",
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
    Root endpoint.
    """
    return JSONResponse(content={"message": "CRWLR API is running in emergency mode"})

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
    }
    
    return {
        "status": "running",
        "mode": "emergency",
        "environment": env_vars
    }

# Log that we've loaded the minimal API
logger.info("Minimal emergency API loaded successfully")
