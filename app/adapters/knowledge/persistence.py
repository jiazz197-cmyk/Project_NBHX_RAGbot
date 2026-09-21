"""Knowledge record persistence: raw SQL operations behind a port-free interface."""

from __future__ import annotations

from typing import List

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.adapters.knowledge.constants import KNOWLEDGE_CHUNKS_TABLE, KNOWLEDGE_COLLECTION_NAME
from app.adapters.retrieval_cache import get_retrieval_cache

logger = get_logger("knowledge.persistence")


class KnowledgePersistence:

    async def list_knowledge_records(self) -> List[dict]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    f"SELECT id, text,"
                    f" COALESCE(metadata_->>'file_name', metadata_->>'source', metadata_->>'title') AS file_name,"
                    f" metadata_->>'upload_time' AS upload_time,"
                    f" metadata_->>'uploader'   AS uploader"
                    f" FROM {KNOWLEDGE_CHUNKS_TABLE}"
                    f" ORDER BY metadata_->>'upload_time' DESC NULLS LAST, id DESC"
                )
            )
            rows = result.fetchall()
            return [
                {
                    "id": str(row.id),
                    "text": row.text or "",
                    "file_name": getattr(row, "file_name", None),
                    "upload_time": row.upload_time,
                    "uploader": row.uploader or "",
                    "status": "approved",
                }
                for row in rows
            ]

    async def check_knowledge_record_exists(self, record_id: int) -> bool:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"SELECT id FROM {KNOWLEDGE_CHUNKS_TABLE} WHERE id = :id"),
                {"id": record_id},
            )
            return result.first() is not None

    async def delete_knowledge_record(self, record_id: int) -> int:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"DELETE FROM {KNOWLEDGE_CHUNKS_TABLE} WHERE id = :id"),
                {"id": record_id},
            )
            await db.commit()
            deleted = result.rowcount or 0

        if deleted:
            # issue #21：删除的是文档集合的 chunk → 该集合检索缓存失效。
            # 只有真正删掉行才 bump；失败只告警（TTL 兜底），不影响删除结果。
            await get_retrieval_cache().invalidate_collection(KNOWLEDGE_COLLECTION_NAME)
        return deleted
