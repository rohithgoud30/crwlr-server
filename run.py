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
    logger.warning(f"Could not find available port after {max_attempts} attempts. Using {start_port} anyway.")
    return start_port

def initialize_services():
    """Initialize required services before starting the app"""
    try:
        # Initialize Algolia
        from app.core.algolia import init_algolia
        algolia_client = init_algolia()
        if algolia_client:
            logger.info("Algolia service initialized successfully")
        else:
            logger.warning("Failed to initialize Algolia. Search functionality may be limited.")
            
        # Initialize other services as needed
        # ...
        
    except Exception as e:
        logger.error(f"Error initializing services: {e}")
        logger.warning("Application will start, but some features may not work correctly")

if __name__ == "__main__":
    try:
        # Initialize services
        initialize_services()
        
        desired_port = int(os.environ.get("PORT", 8080))
        port = find_available_port(desired_port)
        
        if port != desired_port:
            logger.warning(f"Port {desired_port} is in use. Using port {port} instead.")
        
        logger.info(f"Starting server on port {port}")
        
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            reload=False,
            workers=1,
        )
    except Exception as e:
        logger.error(f"Error starting server: {str(e)}")
        logger.error(f"Traceback:", exc_info=True)
        sys.exit(1)
