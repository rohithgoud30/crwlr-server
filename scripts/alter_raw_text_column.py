#!/usr/bin/env python3
"""
Script to modify the documents table to make the raw_text column nullable.
This allows existing documents to have null or empty raw_text values.
"""

import asyncio
import logging
from sqlalchemy import text

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import database connection
from app.core.database import async_engine

async def alter_raw_text_column():
    """
    Alters the raw_text column in the documents table to allow null values
    and sets a default empty string.
    """
    try:
        logger.info("Starting to alter raw_text column in documents table...")
        
        # Use a direct SQL ALTER TABLE statement
        alter_query = text("""
            ALTER TABLE documents 
            ALTER COLUMN raw_text DROP NOT NULL,
            ALTER COLUMN raw_text SET DEFAULT '';
        """)
        
        async with async_engine.begin() as conn:
            await conn.execute(alter_query)
            logger.info("Successfully altered raw_text column in documents table.")
        
        return True
    except Exception as e:
        logger.error(f"Error altering raw_text column: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return False

async def main():
    """Main function to run the script."""
    try:
        logger.info("Starting database schema update script...")
        
        # Alter the raw_text column
        success = await alter_raw_text_column()
        
        if success:
            logger.info("Database schema updated successfully!")
        else:
            logger.error("Failed to update database schema.")
            
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(main()) 