import os
import logging
from typing import Generator, Any
from sqlalchemy.ext.declarative import declarative_base

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# SQLAlchemy models base class
Base = declarative_base()

# Mock engine for SQLAlchemy
class MockEngine:
    """Mock database engine that doesn't connect to a real database."""
    
    def __init__(self):
        logger.info("Using mock database engine")
    
    def connect(self):
        """Mock connect method."""
        logger.info("Mock engine connect")
        return MockConnection()
    
    def dispose(self):
        """Mock dispose method."""
        logger.info("Mock engine dispose")
        return

class MockConnection:
    """Mock connection for mock engine."""
    
    def __init__(self):
        pass
    
    def close(self):
        """Mock close method."""
        logger.info("Mock connection closed")
        return
    
    def execute(self, *args, **kwargs):
        """Mock execute method."""
        logger.info(f"Mock execute: {args[0] if args else ''}")
        return MockResult()

class MockResult:
    """Mock result for mock connection."""
    
    def __init__(self):
        pass
    
    def fetchall(self):
        """Mock fetchall method."""
        return []
    
    def fetchone(self):
        """Mock fetchone method."""
        return None
    
    def scalar(self):
        """Mock scalar method."""
        return None

class MockSession:
    """Mock database session that doesn't connect to a real database."""
    
    def __init__(self):
        self.mock_data = {}
        logger.info("Using mock database session")
    
    def add(self, obj):
        """Mock add method."""
        logger.info(f"Mock add: {obj}")
        return
    
    def commit(self):
        """Mock commit method."""
        logger.info("Mock commit")
        return
    
    def rollback(self):
        """Mock rollback method."""
        logger.info("Mock rollback")
        return
    
    def close(self):
        """Mock close method."""
        logger.info("Mock session closed")
        return
    
    def query(self, *args, **kwargs):
        """Mock query method."""
        return MockQuery()

class MockQuery:
    """Mock query object for the mock session."""
    
    def __init__(self):
        pass
    
    def filter(self, *args, **kwargs):
        """Mock filter method."""
        return self
    
    def filter_by(self, **kwargs):
        """Mock filter_by method."""
        return self
    
    def first(self):
        """Mock first method."""
        return None
    
    def all(self):
        """Mock all method."""
        return []
    
    def count(self):
        """Mock count method."""
        return 0
    
    def delete(self, *args, **kwargs):
        """Mock delete method."""
        return 0

# Create mock engine and session
logger.info("Setting up mock database - no real database connection will be used")
engine = MockEngine()

# Create mock session function
def get_db() -> Generator[MockSession, None, None]:
    """
    Returns a mock database session without actually connecting to a database.
    All database operations will be logged but not executed.
    """
    db = MockSession()
    try:
        yield db
    finally:
        db.close()

def create_tables():
    """Mock function to create tables."""
    logger.info("Mock create_tables: Tables would be created here in a real database")
    return 