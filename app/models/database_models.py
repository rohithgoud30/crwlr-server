from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Float, JSON
from sqlalchemy.orm import relationship

from app.core.database import Base

class User(Base):
    """User model for managing API access"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(128), unique=True, index=True)
    email = Column(String(255), unique=True, index=True)
    api_key = Column(String(255), unique=True, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CrawlerQueue(Base):
    """Queue for URLs to be processed"""
    __tablename__ = "crawler_queue"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(2048), index=True)
    status = Column(String(50), default="pending", index=True)  # pending, processing, completed, failed
    priority = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    documents = relationship("Document", back_populates="queue_item")

class Document(Base):
    """Document model for storing extracted document information"""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    queue_id = Column(Integer, ForeignKey("crawler_queue.id"), index=True)
    url = Column(String(2048), index=True)
    title = Column(String(1024))
    document_type = Column(String(50), index=True)  # tos, pp, etc.
    status = Column(String(50), default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    queue_item = relationship("CrawlerQueue", back_populates="documents")
    contents = relationship("ExtractedContent", back_populates="document")
    summaries = relationship("Summary", back_populates="document")
    text_analyses = relationship("TextAnalysis", back_populates="document")
    word_frequencies = relationship("WordFrequency", back_populates="document")

class ExtractedContent(Base):
    """Extracted content from documents"""
    __tablename__ = "extracted_contents"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    content = Column(Text)
    extraction_method = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    document = relationship("Document", back_populates="contents")

class Summary(Base):
    """Document summary model"""
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    summary = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    document = relationship("Document", back_populates="summaries")

class TextAnalysis(Base):
    """Text mining and analysis model"""
    __tablename__ = "text_analyses"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    word_count = Column(Integer)
    avg_word_length = Column(Float)
    sentence_count = Column(Integer)
    avg_sentence_length = Column(Float)
    readability_score = Column(Float)
    unique_word_ratio = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Additional metrics
    readability_interpretation = Column(String(255))
    capital_letter_freq = Column(Float)
    punctuation_density = Column(Float)
    question_frequency = Column(Float)
    paragraph_count = Column(Integer)
    common_word_percentage = Column(Float)
    
    # Relationships
    document = relationship("Document", back_populates="text_analyses")

class WordFrequency(Base):
    """Word frequency analysis model"""
    __tablename__ = "word_frequencies"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    frequencies = Column(JSON)  # Storing word frequency data as JSON
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    document = relationship("Document", back_populates="word_frequencies") 