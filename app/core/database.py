import os
import logging
import warnings
from typing import Optional, Tuple, Callable
from sqlalchemy import create_engine, MetaData, Column, Table, String, DateTime, Text, ForeignKey, Enum, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import text, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine

# Filter out CryptographyDeprecationWarning related to not_valid_after
warnings.filterwarnings('ignore', category=Warning, module='google.cloud.sql.connector.instance')

from app.core.config import settings

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure database connection based on environment
def get_connection_string() -> Tuple[str, str, Optional[Callable]]:
    """
    Returns database connection string and optional creator function based on environment variables
    """
    # Check environment variables and log them
    logger.info(f"Database Configuration:")
    logger.info(f"DB_USER: {'Set' if settings.DB_USER else 'Not Set'}")
    logger.info(f"DB_PASS: {'Set' if settings.DB_PASS else 'Not Set'}")
    logger.info(f"DB_NAME: {'Set' if settings.DB_NAME else 'Not Set'}")
    logger.info(f"DB_HOST: {'Set' if settings.DB_HOST else 'Not Set'}")
    logger.info(f"DB_PORT: {'Set' if settings.DB_PORT else 'Not Set'}")
    logger.info(f"INSTANCE_CONNECTION_NAME: {'Set' if settings.INSTANCE_CONNECTION_NAME else 'Not Set'}")
    logger.info(f"NO_PROXY: {'True' if os.environ.get('NO_PROXY', '').lower() == 'true' else 'False'}")
    logger.info(f"ENVIRONMENT: {settings.ENVIRONMENT}")
    
    # Check if NO_PROXY environment variable is set (for Cloud Run without proxy)
    if os.environ.get("NO_PROXY", "").lower() == "true":
        logger.info("NO_PROXY mode: Using direct connection to Cloud SQL without proxy")
        # Direct connection to Cloud SQL using public IP
        host = settings.DB_HOST or "127.0.0.1"  # Default to localhost if not set
        port = settings.DB_PORT or "5432"
        user = settings.DB_USER or "postgres"
        password = settings.DB_PASS or ""
        dbname = settings.DB_NAME or "postgres"
        
        connection_string = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
        async_connection_string = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"
        logger.info(f"Using direct PostgreSQL connection to {host}:{port}")
        return connection_string, async_connection_string, None
    
    # Check if running on Google Cloud with Cloud SQL Proxy
    elif settings.INSTANCE_CONNECTION_NAME:
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
            
            # Create connection string for PostgreSQL with Cloud SQL Proxy
            connection_string = f"postgresql+pg8000://"
            
            # For local development, use direct connection with asyncpg
            if settings.ENVIRONMENT == "development":
                host = "127.0.0.1"  # Local proxy address
                port = "5432"       # Default PostgreSQL port
                user = settings.DB_USER or "postgres"
                password = settings.DB_PASS or ""
                dbname = settings.DB_NAME or "postgres"
                async_connection_string = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"
                logger.info(f"Using asyncpg for local development with Cloud SQL proxy at {host}:{port}")
            else:
                # For cloud environments we still use asyncpg but with cloud credentials
                # This is not optimal but will work around the limitations for now
                host = "127.0.0.1"  # Will be replaced by proxy in cloud
                port = "5432"
                user = settings.DB_USER or "postgres" 
                password = settings.DB_PASS or ""
                dbname = settings.DB_NAME or "postgres"
                async_connection_string = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"
            
            logger.info(f"Connected to Cloud SQL instance: {settings.INSTANCE_CONNECTION_NAME}")
            return connection_string, async_connection_string, getconn
        except ImportError:
            logger.warning("Cloud SQL libraries not available, falling back to direct PostgreSQL connection")
    
    # Fall back to direct PostgreSQL connection
    # For local development with Cloud SQL proxy, use 127.0.0.1 as host
    if settings.ENVIRONMENT == "development" and os.environ.get("USE_CLOUD_SQL_PROXY", "").lower() == "true":
        # When using Cloud SQL proxy locally, it forwards to localhost:5432
        host = "127.0.0.1"
        port = "5432"
        logger.info(f"Using Cloud SQL Proxy for local development (localhost:{port})")
    else:
        host = settings.DB_HOST or "127.0.0.1"
        port = settings.DB_PORT or "5432"
    
    user = settings.DB_USER or "postgres"
    password = settings.DB_PASS or ""
    dbname = settings.DB_NAME or "postgres"
    
    connection_string = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    async_connection_string = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"
    logger.info(f"Using direct PostgreSQL connection to {host}:{port}")
    return connection_string, async_connection_string, None

# Create SQLAlchemy engines
try:
    connection_string, async_connection_string, creator_func = get_connection_string()
    
    # Create sync engine for schema operations
    if creator_func:
        # If we got a creator function for Cloud SQL connector
        engine = create_engine(
            connection_string,
            creator=creator_func,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Check connection before use
            pool_recycle=3600,   # Recycle connections hourly
        )
    else:
        # Standard direct connection
        engine = create_engine(
            connection_string,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Check connection before use
            pool_recycle=3600,   # Recycle connections hourly
        )
    
    # Create async engine (always using asyncpg)
    async_engine = create_async_engine(
        async_connection_string,
        echo=False,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
    )
    
    logger.info("Database engines created successfully")
    
except Exception as e:
    logger.error(f"Error creating database engine: {str(e)}")
    # Instead of raising, we'll continue without a database and handle it in the application
    engine = None
    async_engine = None
    logger.warning("Continuing without database connection")

# Define metadata object
metadata = MetaData()

# Define tables
users = Table(
    "users",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("clerk_user_id", String(255), unique=True, nullable=False),
    Column("email", String(255), nullable=False),
    Column("name", String(255), nullable=True),
    Column("role", String(20), nullable=False, server_default="user"),
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
    Column("url", Text, nullable=False),
    Column("document_type", Enum("tos", "pp", name="document_type", create_type=False), nullable=False),
    Column("retrieved_url", Text, nullable=False),
    Column("company_name", Text, nullable=True),
    Column("logo_url", Text, nullable=True),
    Column("views", BigInteger, nullable=False, server_default="0"),
    Column("raw_text", Text, nullable=False),
    Column("one_sentence_summary", Text, nullable=True),
    Column("hundred_word_summary", Text, nullable=True),
    Column("word_frequencies", JSONB, nullable=True),
    Column("text_mining_metrics", JSONB, nullable=True),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

submissions = Table(
    "submissions",
    metadata,
    Column("id", UUID, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("user_id", UUID, ForeignKey("users.id"), nullable=True),
    Column("document_id", UUID, ForeignKey("documents.id"), nullable=True),
    Column("requested_url", Text, nullable=False),
    Column("document_type", Enum("tos", "pp", name="document_type", create_type=False), nullable=False),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("error_message", Text, nullable=True),
    Column("created_at", DateTime, server_default=func.now()),
    Column("updated_at", DateTime, server_default=func.now(), onupdate=func.now()),
)

def create_tables():
    """
    Creates all database tables if they don't exist.
    """
    if engine is None:
        logger.error("Cannot create tables - database engine is not initialized")
        return
        
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