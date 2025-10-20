import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_engine, submissions
from app.crud.base import CRUDBase

logger = logging.getLogger(__name__)


class SubmissionCRUD(CRUDBase):
    """CRUD helper for Neon-backed submissions table."""

    def __init__(self) -> None:
        super().__init__(submissions)
        self.valid_statuses = ["initialized", "processing", "success", "failed", "pending"]

    async def get_submissions_by_user(
        self, user_email: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        if not user_email:
            return []

        query = (
            select(submissions)
            .where(submissions.c.user_email == user_email)
            .order_by(submissions.c.created_at.desc())
            .limit(limit)
        )
        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return [dict(row._mapping) for row in result.fetchall()]

    async def get_pending_submissions(self, limit: int = 10) -> List[Dict[str, Any]]:
        query = (
            select(submissions)
            .where(submissions.c.status == "pending")
            .order_by(submissions.c.created_at.asc())
            .limit(limit)
        )
        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return [dict(row._mapping) for row in result.fetchall()]

    async def update_submission_status(
        self,
        submission_id: str,
        status: str,
        document_id: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if status not in self.valid_statuses:
            logger.warning("Invalid submission status '%s'. Falling back to 'failed'.", status)
            status = "failed"

        update_payload: Dict[str, Any] = {
            "status": status,
            "updated_at": datetime.now(timezone.utc),
        }

        if document_id is not None:
            update_payload["document_id"] = document_id

        if error_message is not None:
            update_payload["error_message"] = error_message
        elif status == "success":
            update_payload["error_message"] = None

        stmt = (
            update(submissions)
            .where(submissions.c.id == submission_id)
            .values(**update_payload)
            .returning(*submissions.columns)
        )

        async with async_engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if not row:
            return None

        return dict(row._mapping)

    async def create_submission(
        self,
        user_email: str,
        document_id: Optional[str] = None,
        requested_url: Optional[str] = None,
        document_type: Optional[str] = None,
        status: str = "initialized",
        error_message: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if status not in self.valid_statuses:
            status = "initialized"

        payload: Dict[str, Any] = {
            "id": str(uuid4()),
            "user_email": user_email,
            "document_id": document_id,
            "requested_url": requested_url,
            "document_type": document_type,
            "status": status,
            "error_message": error_message,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        stmt = (
            insert(submissions)
            .values(**payload)
            .returning(*submissions.columns)
        )

        async with async_engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        if not row:
            return None

        return dict(row._mapping)

    async def delete_submission(self, submission_id: str) -> bool:
        stmt = (
            delete(submissions)
            .where(submissions.c.id == submission_id)
            .returning(submissions.c.id)
        )

        async with async_engine.begin() as conn:
            result = await conn.execute(stmt)
            row = result.fetchone()

        return bool(row)

    async def find_by_requested_url(
        self,
        requested_url: str,
        document_type: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        if not requested_url:
            return []

        query = select(submissions).where(submissions.c.requested_url == requested_url)

        if document_type:
            query = query.where(submissions.c.document_type == document_type)

        if statuses:
            query = query.where(submissions.c.status.in_(statuses))

        query = query.order_by(submissions.c.created_at.desc()).limit(limit)

        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return [dict(row._mapping) for row in result.fetchall()]

    async def has_failed_submission(self, requested_url: str) -> bool:
        query = (
            select(func.count())
            .select_from(submissions)
            .where(submissions.c.requested_url == requested_url)
            .where(submissions.c.status == "failed")
        )
        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return (result.scalar() or 0) > 0

    async def paginate_user_submissions(
        self,
        user_email: str,
        page: int,
        size: int,
        sort_order: str = "desc",
        search_url: Optional[str] = None,
        status: Optional[str] = None,
        document_type: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if sort_order not in {"asc", "desc"}:
            sort_order = "desc"

        offset = max(page - 1, 0) * size

        base_query = select(submissions).where(submissions.c.user_email == user_email)
        count_query = select(func.count()).select_from(submissions).where(
            submissions.c.user_email == user_email
        )

        if search_url:
            pattern = f"%{search_url.lower()}%"
            condition = func.lower(submissions.c.requested_url).like(pattern)
            base_query = base_query.where(condition)
            count_query = count_query.where(condition)

        if status:
            base_query = base_query.where(submissions.c.status == status)
            count_query = count_query.where(submissions.c.status == status)

        if document_type:
            base_query = base_query.where(submissions.c.document_type == document_type)
            count_query = count_query.where(submissions.c.document_type == document_type)

        order_column = submissions.c.created_at.asc()
        if sort_order == "desc":
            order_column = submissions.c.created_at.desc()

        base_query = base_query.order_by(order_column).offset(offset).limit(size)

        async with async_engine.connect() as conn:
            total_result = await conn.execute(count_query)
            total = total_result.scalar() or 0

            data_result = await conn.execute(base_query)
            items = [dict(row._mapping) for row in data_result.fetchall()]

        return items, total


submission_crud = SubmissionCRUD()
