import logging
import os
import socket
import sys
import time
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def is_port_in_use(port):
    """Check if a port is already in use"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def find_available_port(start_port=8080, max_attempts=10):
    """Find an available port starting from start_port"""
    port = start_port
    attempts = 0
    
    while attempts < max_attempts:
        if not is_port_in_use(port):
            return port
        port += 1
        attempts += 1
    
    # If we can't find an available port, return the original
    logger.warning(f"Could not find available port after {max_attempts} attempts. Using {start_port}")
    return start_port

def init_services():
    """Initialize all required services"""
    try:
        # Initialize Firebase (this is done in app/__init__.py)
        from app.core.database import db
        
        # Initialize Algolia for search (if configured)
        from app.core.algolia import init_algolia
        algolia_client = init_algolia()
        if algolia_client:
            logger.info("Algolia search service initialized successfully")
        else:
            logger.warning("Algolia search service not initialized - some search features may be limited")
        
        return True
    except Exception as e:
        logger.error(f"Error initializing services: {str(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False

if __name__ == "__main__":
    # Add current directory to path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    
    # Initialize services
    services_ok = init_services()
    if not services_ok:
        logger.warning("Application will start, but some features may not work correctly")
    
    # Find an available port
    port = int(os.environ.get("PORT", "8080"))
    
    logger.info(f"Starting server on port {port}")
    
    # Configure Pydantic to allow arbitrary types
    import pydantic
    pydantic.config.ConfigDict.update_forward_refs(arbitrary_types_allowed=True) 
    
    # Override configuration if running locally
    if os.environ.get("ENVIRONMENT") != "production":
        # For local development, use a fresh port if the default is in use
        if is_port_in_use(port):
            port = find_available_port(port)
            logger.info(f"Port {os.environ.get('PORT', '8080')} is in use, using port {port} instead")
        
        # Run with auto-reload for development
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            reload=True,
            reload_dirs=["app"],
            log_level="info"
        )
    else:
        # Production settings
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            reload=False,
            log_level="info"
        )
