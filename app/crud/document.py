import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import (
    DOCUMENT_TYPES,
    async_engine,
    documents,
    get_document_by_retrieved_url as _get_document_by_retrieved_url,
    get_document_by_url as _get_document_by_url,
    increment_views as _increment_views,
    stats,
)
from app.crud.base import CRUDBase

logger = logging.getLogger(__name__)


def _build_ts_vector() -> Any:
    """Helper expression for full-text search."""
    return func.to_tsvector(
        'english',
        func.concat_ws(
            ' ',
            func.coalesce(documents.c.company_name, ''),
            documents.c.url,
            func.coalesce(documents.c.retrieved_url, ''),
            func.coalesce(documents.c.raw_text, ''),
        ),
    )


class DocumentCRUD(CRUDBase):
    """CRUD helper for Neon-backed documents table."""

    def __init__(self) -> None:
        super().__init__(documents)

    async def get_by_url_and_type(
        self,
        url: str,
        document_type: str,
    ) -> Optional[Dict[str, Any]]:
        if not url or not document_type:
            return None
        if document_type not in DOCUMENT_TYPES:
            logger.warning("Unsupported document_type '%s' requested", document_type)
            return None
        return await _get_document_by_url(url, document_type)

    async def get_by_retrieved_url(
        self,
        url: str,
        document_type: str,
    ) -> Optional[Dict[str, Any]]:
        if not url or not document_type:
            return None
        if document_type not in DOCUMENT_TYPES:
            return None
        return await _get_document_by_retrieved_url(url, document_type)

    async def increment_views(self, doc_id: str) -> Optional[Dict[str, Any]]:
        return await _increment_views(doc_id)

    async def search_documents(
        self,
        query: str,
        document_type: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
        sort_by: Optional[str] = 'relevance',
        sort_order: Optional[str] = 'desc',
    ) -> Dict[str, Any]:
        return await self._search_documents_postgres(
            query=query,
            document_type=document_type,
            page=page,
            per_page=per_page,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def _search_documents_postgres(
        self,
        query: str,
        document_type: Optional[str],
        page: int,
        per_page: int,
        sort_by: Optional[str],
        sort_order: Optional[str],
    ) -> Dict[str, Any]:
        filters = []
        ts_query = None
        ts_vector = _build_ts_vector()

        if document_type:
            filters.append(documents.c.document_type == document_type)

        if query:
            ts_query = func.websearch_to_tsquery('english', query)
            filters.append(ts_vector.op('@@')(ts_query))

        order_direction = (sort_order or 'desc').lower()
        order_expression = documents.c.updated_at.desc()

        if sort_by == 'relevance' and ts_query is not None:
            ts_rank_expr = func.ts_rank(ts_vector, ts_query)
            order_expression = ts_rank_expr.desc() if order_direction != 'asc' else ts_rank_expr.asc()
        else:
            mapping = {
                'updated_at': documents.c.updated_at,
                'created_at': documents.c.created_at if hasattr(documents.c, 'created_at') else documents.c.updated_at,
                'views': documents.c.views,
                'company_name': documents.c.company_name,
                'url': documents.c.url,
            }
            column = mapping.get(sort_by or 'updated_at', documents.c.updated_at)
            order_expression = column.desc() if order_direction != 'asc' else column.asc()

        offset = max(page - 1, 0) * per_page

        count_query = select(func.count()).select_from(documents)
        data_query = select(documents)

        for condition in filters:
            count_query = count_query.where(condition)
            data_query = data_query.where(condition)

        data_query = data_query.order_by(order_expression).offset(offset).limit(per_page)

        async with async_engine.connect() as conn:
            total_result = await conn.execute(count_query)
            total = total_result.scalar_one()

            data_result = await conn.execute(data_query)
            rows = [dict(row._mapping) for row in data_result.fetchall()]

        total_pages = (total + per_page - 1) // per_page if total else 0
        return {
            'items': rows,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'has_next': page < total_pages,
            'has_prev': page > 1,
        }

    async def get_popular_documents(
        self, limit: int = 5, document_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = select(documents).order_by(documents.c.views.desc()).limit(limit)
        if document_type:
            query = query.where(documents.c.document_type == document_type)

        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return [dict(row._mapping) for row in result.fetchall()]

    async def get_recent_documents(
        self, limit: int = 10, document_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        query = select(documents).order_by(documents.c.updated_at.desc()).limit(limit)
        if document_type:
            query = query.where(documents.c.document_type == document_type)

        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return [dict(row._mapping) for row in result.fetchall()]

    async def delete_document(self, doc_id: str) -> bool:
        delete_stmt = (
            delete(documents).where(documents.c.id == doc_id).returning(documents.c.id)
        )
        async with async_engine.begin() as conn:
            result = await conn.execute(delete_stmt)
            deleted = result.fetchone()
        return bool(deleted)

    async def get_document_counts(self) -> Dict[str, Any]:
        async with async_engine.begin() as conn:
            stats_query = select(stats).where(stats.c.id == 'global_stats').limit(1)
            stats_result = await conn.execute(stats_query)
            existing = stats_result.fetchone()
            if existing:
                return dict(existing._mapping)

            aggregate_query = select(
                func.sum(
                    case((documents.c.document_type == 'tos', 1), else_=0)
                ).label('tos_count'),
                func.sum(
                    case((documents.c.document_type == 'pp', 1), else_=0)
                ).label('pp_count'),
                func.count().label('total_count'),
            )
            agg_result = await conn.execute(aggregate_query)
            totals = agg_result.fetchone()
            tos_count = totals.tos_count or 0
            pp_count = totals.pp_count or 0
            total_count = totals.total_count or 0
            now = datetime.now(timezone.utc)

            upsert_stmt = (
                insert(stats)
                .values(
                    id='global_stats',
                    tos_count=tos_count,
                    pp_count=pp_count,
                    total_count=total_count,
                    last_updated=now,
                )
                .on_conflict_do_update(
                    index_elements=[stats.c.id],
                    set_={
                        'tos_count': tos_count,
                        'pp_count': pp_count,
                        'total_count': total_count,
                        'last_updated': now,
                    },
                )
            )
            await conn.execute(upsert_stmt)
            return {
                'id': 'global_stats',
                'tos_count': tos_count,
                'pp_count': pp_count,
                'total_count': total_count,
                'last_updated': now,
            }

    async def update_document_analysis(
        self, doc_id: str, analysis_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not analysis_data:
            return await self.get(doc_id)  # type: ignore[arg-type]

        prepared: Dict[str, Any] = {}
        for key in [
            'raw_text',
            'one_sentence_summary',
            'hundred_word_summary',
            'word_frequencies',
            'text_mining_metrics',
            'company_name',
            'logo_url',
        ]:
            if key in analysis_data:
                prepared[key] = analysis_data[key]

        if not prepared:
            return await self.get(doc_id)  # type: ignore[arg-type]

        prepared['updated_at'] = datetime.now(timezone.utc)

        update_stmt = (
            update(documents)
            .where(documents.c.id == doc_id)
            .values(**prepared)
            .returning(*documents.columns)
        )
        async with async_engine.begin() as conn:
            result = await conn.execute(update_stmt)
            row = result.fetchone()

        if not row:
            return None

        return dict(row._mapping)

    async def update_company_name(
        self, doc_id: str, company_name: str
    ) -> Optional[Dict[str, Any]]:
        update_stmt = (
            update(documents)
            .where(documents.c.id == doc_id)
            .values(
                company_name=company_name,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(*documents.columns)
        )
        async with async_engine.begin() as conn:
            result = await conn.execute(update_stmt)
            row = result.fetchone()

        if not row:
            return None

        return dict(row._mapping)

    async def create(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:  # type: ignore[override]
        payload = data.copy()
        payload.setdefault('id', str(uuid4()))
        payload.setdefault('views', 0)
        payload.setdefault('created_at', datetime.now(timezone.utc))
        payload.setdefault('updated_at', datetime.now(timezone.utc))

        for json_field in ('word_frequencies', 'text_mining_metrics'):
            value = payload.get(json_field)
            if isinstance(value, str):
                try:
                    payload[json_field] = json.loads(value)
                except json.JSONDecodeError:
                    logger.warning('Ignoring invalid JSON payload for %s', json_field)
                    payload[json_field] = None

        insert_stmt = (
            insert(documents)
            .values(**payload)
            .returning(*documents.columns)
        )
        async with async_engine.begin() as conn:
            result = await conn.execute(insert_stmt)
            row = result.fetchone()

        if not row:
            return None

        return dict(row._mapping)

    async def find_documents_by_domain(
        self, domain: str, document_type: Optional[str] = None, limit: int = 5
    ) -> List[Dict[str, Any]]:
        if not domain:
            return []

        pattern = f"%{domain.lower()}%"
        query = select(documents).where(
            or_(
                func.lower(documents.c.url).like(pattern),
                func.lower(func.coalesce(documents.c.retrieved_url, '')).like(pattern),
            )
        )

        if document_type:
            query = query.where(documents.c.document_type == document_type)

        query = query.limit(limit)

        async with async_engine.connect() as conn:
            result = await conn.execute(query)
            return [dict(row._mapping) for row in result.fetchall()]


document_crud = DocumentCRUD()
