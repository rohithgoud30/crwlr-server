#!/usr/bin/env python3
"""
Database Connection Test Script for CRWLR

This script tests the database connection using the configuration 
from app/core/config.py and database setup from app/core/database.py.
"""

import logging
import sys
from sqlalchemy.sql import text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("db-test")

def test_database_connection():
    """Test the database connection and run a simple query."""
    try:
        # Import the database module
        from app.core.database import engine
        from app.core.config import settings
        
        logger.info("Testing database connection...")
        logger.info(f"DB Host: {settings.DB_HOST}")
        logger.info(f"DB Port: {settings.DB_PORT}")
        logger.info(f"DB Name: {settings.DB_NAME}")
        
        # Try to connect and execute a simple query
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            if result.scalar() == 1:
                logger.info("✅ Database connection successful!")
                return True
            else:
                logger.error("❌ Database connection failed - unexpected query result")
                return False
    
    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        logger.error("Make sure you're running this script from the project root directory")
        return False
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return False

if __name__ == "__main__":
    logger.info("CRWLR Database Connection Test")
    logger.info("==============================")
    
    success = test_database_connection()
    
    if success:
        logger.info("All tests passed!")
        sys.exit(0)
    else:
        logger.error("Tests failed. Check the logs for details.")
        sys.exit(1) 