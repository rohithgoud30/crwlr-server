import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    TIMESTAMP,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = ("tos", "pp")


def _build_async_database_url() -> str:
    """
    Construct an async SQLAlchemy database URL suitable for psycopg3.
    """
    url = settings.NEON_DATABASE_URL or os.getenv("NEON_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "NEON_DATABASE_URL is not set. Please configure your Neon connection string."
        )

    # Normalise legacy postgres:// schemes and ensure we use the async psycopg driver.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    if url.startswith("postgresql+psycopg_async://"):
        return url

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg_async://", 1)

    raise RuntimeError(
        f"Unsupported NEON_DATABASE_URL scheme '{url}'. Expected postgres/postgresql."
    )


try:
    ASYNC_DATABASE_URL = _build_async_database_url()
    async_engine: AsyncEngine = create_async_engine(
        ASYNC_DATABASE_URL,
        future=True,
        echo=False,
        pool_pre_ping=True,
    )
    async_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    logger.info("Neon database engine initialised successfully.")
except Exception as exc:  # pragma: no cover - executed during startup only
    async_engine = None  # type: ignore[assignment]
    async_session_factory = None  # type: ignore[assignment]
    logger.error("Failed to initialise Neon database engine: %s", exc)
    raise


metadata = MetaData()

documents = Table(
    "documents",
    metadata,
    Column("id", String(length=64), primary_key=True),
    Column("url", Text, nullable=False),
    Column("document_type", String(length=16), nullable=False),
    Column("retrieved_url", Text, nullable=False),
    Column("company_name", Text),
    Column("logo_url", Text),
    Column("views", Integer, nullable=False, server_default=text("0")),
    Column("raw_text", Text),
    Column("one_sentence_summary", Text),
    Column("hundred_word_summary", Text),
    Column("word_frequencies", JSONB),
    Column("text_mining_metrics", JSONB),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    ),
)

submissions = Table(
    "submissions",
    metadata,
    Column("id", String(length=64), primary_key=True),
    Column("user_email", Text),
    Column("document_id", String(length=64)),
    Column("requested_url", Text),
    Column("document_type", String(length=16)),
    Column("status", String(length=32)),
    Column("error_message", Text),
    Column(
        "created_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    ),
)

stats = Table(
    "stats",
    metadata,
    Column("id", String(length=64), primary_key=True),
    Column("tos_count", Integer, nullable=False),
    Column("pp_count", Integer, nullable=False),
    Column("total_count", Integer, nullable=False),
    Column(
        "last_updated",
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    ),
)


async def ensure_tables_exist() -> None:
    """
    Create tables if they do not yet exist.
    """
    async with async_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def get_document_by_url(
    url: str,
    document_type: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single document by original URL and document type.
    """
    query = (
        select(documents)
        .where(documents.c.url == url)
        .where(documents.c.document_type == document_type)
        .limit(1)
    )
    async with async_engine.connect() as conn:
        result = await conn.execute(query)
        row = result.fetchone()
        return dict(row._mapping) if row else None


async def get_document_by_retrieved_url(
    url: str,
    document_type: str,
) -> Optional[Dict[str, Any]]:
    """
    Fetch a single document by retrieved URL and document type.
    """
    query = (
        select(documents)
        .where(documents.c.retrieved_url == url)
        .where(documents.c.document_type == document_type)
        .limit(1)
    )
    async with async_engine.connect() as conn:
        result = await conn.execute(query)
        row = result.fetchone()
        return dict(row._mapping) if row else None


async def increment_views(document_id: str) -> Optional[Dict[str, Any]]:
    """
    Atomically increment the view counter for a document and return the updated row.
    """
    now = datetime.now(timezone.utc)
    query = (
        update(documents)
        .where(documents.c.id == document_id)
        .values(views=documents.c.views + 1, updated_at=now)
        .returning(*documents.columns)
    )
    async with async_engine.begin() as conn:
        result = await conn.execute(query)
        row = result.fetchone()
        if not row:
            return None
        return dict(row._mapping)
