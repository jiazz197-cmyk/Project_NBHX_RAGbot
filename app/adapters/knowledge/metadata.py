"""Knowledge metadata adapter: file-name based chunk lookup and removal (PGVector)."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.adapters.knowledge.collection_tables import is_missing_table, physical_table
from app.adapters.retrieval_cache import get_retrieval_cache
from app.ports.dto.knowledge_upload import KnowledgeFileConflict
from app.ports.outbound.knowledge_metadata import KnowledgeMetadataPort

logger = get_logger("knowledge.metadata")


class VectorMetadataAdapter(KnowledgeMetadataPort):
    """按 file_name 查询 / 删除 PGVector chunk（原生 SQL，照 KnowledgePersistence 模式）。

    PGVector 表为懒建表（首次 upsert 才创建），表不存在时视为「无冲突 / 无旧块」。
    """

    async def find_conflict(
        self, collection: str, file_name: str
    ) -> Optional[KnowledgeFileConflict]:
        table = physical_table(collection)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    text(
                        f"SELECT COALESCE(metadata_->>'file_name', metadata_->>'source', '') AS file_name,"
                        f" COALESCE(metadata_->>'uploader', '') AS uploader,"
                        f" COALESCE(metadata_->>'upload_time', '') AS upload_time,"
                        f" COUNT(*) AS chunk_count"
                        f" FROM {table}"
                        f" WHERE COALESCE(metadata_->>'file_name', metadata_->>'source', '') = :file_name"
                        f" GROUP BY COALESCE(metadata_->>'file_name', metadata_->>'source', ''),"
                        f" metadata_->>'uploader', metadata_->>'upload_time'"
                        f" ORDER BY MAX(id) DESC LIMIT 1"
                    ),
                    {"file_name": file_name},
                )
                row = result.first()
                if not row or int(row.chunk_count) == 0:
                    return None
                return KnowledgeFileConflict(
                    file_name=row.file_name or file_name,
                    uploader=row.uploader or "",
                    upload_time=row.upload_time or "",
                    chunk_count=int(row.chunk_count),
                )
        except ProgrammingError as exc:
            if not is_missing_table(exc):
                raise
            logger.debug("同名预检：集合表尚未创建（懒建表） table=%s", table)
            return None

    async def delete_chunks_by_file_name(self, collection: str, file_name: str) -> int:
        table = physical_table(collection)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    text(
                        f"DELETE FROM {table} WHERE"
                        f" COALESCE(metadata_->>'file_name', metadata_->>'source', '') = :file_name"
                    ),
                    {"file_name": file_name},
                )
                await db.commit()
                deleted = result.rowcount or 0
        except ProgrammingError as exc:
            if not is_missing_table(exc):
                raise
            logger.debug("删除旧块：集合表尚未创建 table=%s", table)
            return 0

        if deleted:
            # issue #21：集合内容变了 → 该集合的检索缓存立即失效（版本号自增）。
            # 只有真正删掉行才 bump；失败只告警（TTL 兜底），不影响删除结果。
            await get_retrieval_cache().invalidate_collection(collection)
        return deleted
