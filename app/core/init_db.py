import logging
from sqlalchemy.exc import SQLAlchemyError
from app.core.database import engine, Base
from app.models.database_models import User, CrawlerQueue, Document, ExtractedContent, Summary, TextAnalysis, WordFrequency

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    """Initialize the database by creating all tables"""
    try:
        logger.info("Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except SQLAlchemyError as e:
        logger.error(f"Error creating database tables: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise

if __name__ == "__main__":
    init_db() 