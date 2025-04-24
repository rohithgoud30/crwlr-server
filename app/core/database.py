import os
import logging
import warnings
from typing import Optional
from sqlalchemy import create_engine, MetaData, Column, Table, String, DateTime, Text, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import text, func
from sqlalchemy.exc import SQLAlchemyError

# Filter out CryptographyDeprecationWarning related to not_valid_after
warnings.filterwarnings('ignore', category=Warning, module='google.cloud.sql.connector.instance')

from app.core.config import settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure database connection based on environment
def get_connection_string() -> str:
    """
    Returns database connection string based on environment variables
    """
    # Check if running on Google Cloud with Cloud SQL Proxy
    if hasattr(settings, 'INSTANCE_CONNECTION_NAME') and settings.INSTANCE_CONNECTION_NAME:
        try:
            # Import cloud SQL connector - only needed in cloud environments
            from google.cloud.sql.connector import Connector, IPTypes
            
            # Initialize Cloud SQL Python Connector
            connector = Connector()
            
            def getconn():
                conn = connector.connect(
                    settings.INSTANCE_CONNECTION_NAME,
                    "pg8000",
                    user=settings.DB_USER,
                    password=settings.DB_PASS,
                    db=settings.DB_NAME,
                    ip_type=IPTypes.PUBLIC
                )
                return conn
            
            # Create connection pool with cloud SQL connector
            engine = create_engine(
                "postgresql+pg8000://",
                creator=getconn,
            )
            logger.info(f"Connected to Cloud SQL instance: {settings.INSTANCE_CONNECTION_NAME}")
            return engine
        except ImportError:
            logger.warning("Cloud SQL libraries not available, falling back to direct PostgreSQL connection")
    
    # Fall back to direct PostgreSQL connection
    host = settings.DB_HOST
    port = settings.DB_PORT
    user = settings.DB_USER
    password = settings.DB_PASS
    dbname = settings.DB_NAME
    
    connection_string = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    logger.info(f"Using direct PostgreSQL connection to {host}:{port}")
    return connection_string

# Create SQLAlchemy engine
try:
    connection_string = get_connection_string()
    if isinstance(connection_string, str):
        engine = create_engine(connection_string)
    else:
        # If we got an engine from Cloud SQL connector
        engine = connection_string
    logger.info("Database engine created successfully")
except Exception as e:
    logger.error(f"Error creating database engine: {str(e)}")
    raise

# Define metadata object
metadata = MetaData()

# Define tables
users = Table(
    "users",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("clerk_user_id", String(255), unique=True, nullable=True),
    Column("email", String(255), unique=True, nullable=False),
    Column("name", String(255), nullable=True),
    Column("role", String(50), nullable=False, server_default="user"),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

# Define document_type enum safely with a check if it already exists
document_type_enum_check = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_type') THEN
        CREATE TYPE document_type AS ENUM ('tos', 'pp');
    END IF;
END
$$;
"""

documents = Table(
    "documents",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("url", String(2048), nullable=False),
    Column("document_type", Enum("tos", "pp", name="document_type", create_type=False), nullable=False),
    Column("content", Text, nullable=True),
    Column("analysis", Text, nullable=True),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

submissions = Table(
    "submissions",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=True),
    Column("document_id", UUID, ForeignKey("documents.id"), nullable=True),
    Column("url", String(2048), nullable=False),
    Column("status", String(50), nullable=False, server_default="pending"),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

def create_tables():
    """
    Creates all database tables if they don't exist.
    """
    try:
        # Create pgcrypto extension for UUID generation
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            logger.info("Enabled pgcrypto extension for UUID generation")
            
            # Create DocumentType enum if it doesn't exist
            conn.execute(text(document_type_enum_check))
            logger.info("Created DocumentType enum if it didn't exist")
            
            # Commit these changes
            conn.commit()
        
        # Create all tables
        metadata.create_all(engine)
        logger.info("Created all database tables successfully")
        
    except SQLAlchemyError as e:
        logger.error(f"Database error creating tables: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error creating tables: {str(e)}")
        raise