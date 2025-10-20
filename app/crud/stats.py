import logging
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_engine, documents, stats

logger = logging.getLogger(__name__)


class StatsCRUD:
    """Utility helpers for aggregating document statistics from Neon."""

    async def get_document_counts(self) -> Dict[str, Any]:
        return await self._aggregate_and_cache_counts()

    async def force_recount_stats(self) -> Dict[str, Any]:
        return await self._aggregate_and_cache_counts(force_refresh=True)

    async def _aggregate_and_cache_counts(self, force_refresh: bool = False) -> Dict[str, Any]:
        async with async_engine.begin() as conn:
            if not force_refresh:
                existing = await conn.execute(
                    select(stats).where(stats.c.id == "global_stats").limit(1)
                )
                row = existing.fetchone()
                if row:
                    return dict(row._mapping)

            aggregation = await conn.execute(
                select(
                    func.sum(
                        case((documents.c.document_type == "tos", 1), else_=0)
                    ).label("tos_count"),
                    func.sum(
                        case((documents.c.document_type == "pp", 1), else_=0)
                    ).label("pp_count"),
                    func.count().label("total_count"),
                )
            )
            totals = aggregation.fetchone()
            tos_count = totals.tos_count or 0
            pp_count = totals.pp_count or 0
            total_count = totals.total_count or 0
            now = datetime.now(timezone.utc)

            upsert = (
                insert(stats)
                .values(
                    id="global_stats",
                    tos_count=tos_count,
                    pp_count=pp_count,
                    total_count=total_count,
                    last_updated=now,
                )
                .on_conflict_do_update(
                    index_elements=[stats.c.id],
                    set_={
                        "tos_count": tos_count,
                        "pp_count": pp_count,
                        "total_count": total_count,
                        "last_updated": now,
                    },
                )
            )
            await conn.execute(upsert)

        return {
            "id": "global_stats",
            "tos_count": tos_count,
            "pp_count": pp_count,
            "total_count": total_count,
            "last_updated": now,
        }


stats_crud = StatsCRUD()
