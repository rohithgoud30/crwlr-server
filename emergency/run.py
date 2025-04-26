import os
import uvicorn
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    try:
        # Get port from environment variable
        port = int(os.environ.get("PORT", 8080))
        logger.info(f"Starting server on port {port} in emergency mode")
        
        # Start the server with minimal settings
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=port,
            log_level="info",
            reload=False,
        )
    except Exception as e:
        logger.error(f"Error starting server: {str(e)}")
        # Log the full traceback
        import traceback
        logger.error(traceback.format_exc())
        # Exit with error code
        exit(1)
