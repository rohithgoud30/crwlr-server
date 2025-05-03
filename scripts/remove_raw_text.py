#!/usr/bin/env python3
"""
Script to remove raw text from all documents in the database.
This script sets the raw_text field to an empty string for all documents
while preserving all other data.
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
from app.core.database import async_engine, documents

async def remove_raw_text():
    """
    Removes raw text from all documents in the database by setting it to an empty string.
    """
    try:
        logger.info("Starting to remove raw text from documents...")
        
        # Use a direct SQL update to set raw_text to empty string
        async with async_engine.begin() as conn:
            # First, get the count of documents that will be updated
            count_query = text("SELECT COUNT(*) FROM documents WHERE LENGTH(raw_text) > 0")
            result = await conn.execute(count_query)
            doc_count = result.scalar()
            
            if doc_count == 0:
                logger.info("No documents with raw text found.")
                return
            
            logger.info(f"Found {doc_count} documents with raw text content.")
            
            # Perform the update
            update_query = text("""
                UPDATE documents 
                SET raw_text = '', 
                    updated_at = updated_at  -- Keep original updated_at timestamp
                WHERE LENGTH(raw_text) > 0
                RETURNING id
            """)
            
            result = await conn.execute(update_query)
            updated_ids = result.fetchall()
            
            logger.info(f"Successfully removed raw text from {len(updated_ids)} documents.")
            
        # Return success
        return True
        
    except Exception as e:
        logger.error(f"Error removing raw text from documents: {str(e)}")
        # Print full exception trace for debugging
        import traceback
        logger.error(traceback.format_exc())
        return False

async def main():
    """Main function to run the script."""
    try:
        logger.info("Starting raw text removal script...")
        
        # Remove raw text from all documents
        success = await remove_raw_text()
        
        if success:
            logger.info("Raw text removal completed successfully!")
        else:
            logger.error("Failed to remove raw text from documents.")
            
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(main()) 