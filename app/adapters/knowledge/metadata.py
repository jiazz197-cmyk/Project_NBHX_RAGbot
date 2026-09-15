"""Knowledge metadata adapter: file-name based chunk lookup and removal (PGVector)."""

from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.database import AsyncSessionLocal
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.ports.dto.knowledge_upload import KnowledgeFileConflict
from app.ports.outbound.knowledge_metadata import KnowledgeMetadataPort

logger = get_logger("knowledge.metadata")

# collection 直接拼进物理表名 data_<collection>，必须白名单式校验防注入
_COLLECTION_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _physical_table(collection: str) -> str:
    if not _COLLECTION_NAME_PATTERN.match(collection or ""):
        raise ValidationError(f"非法集合名: {collection}")
    return f"data_{collection}"


def _is_missing_table(exc: ProgrammingError) -> bool:
    msg = f"{exc.orig} {exc}".lower()
    return "does not exist" in msg or "undefinedtable" in msg


class VectorMetadataAdapter(KnowledgeMetadataPort):
    """按 file_name 查询 / 删除 PGVector chunk（原生 SQL，照 KnowledgePersistence 模式）。

    PGVector 表为懒建表（首次 upsert 才创建），表不存在时视为「无冲突 / 无旧块」。
    """

    async def find_conflict(
        self, collection: str, file_name: str
    ) -> Optional[KnowledgeFileConflict]:
        table = _physical_table(collection)
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
            if not _is_missing_table(exc):
                raise
            logger.debug("同名预检：集合表尚未创建（懒建表） table=%s", table)
            return None

    async def delete_chunks_by_file_name(self, collection: str, file_name: str) -> int:
        table = _physical_table(collection)
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
                return result.rowcount or 0
        except ProgrammingError as exc:
            if not _is_missing_table(exc):
                raise
            logger.debug("删除旧块：集合表尚未创建 table=%s", table)
            return 0
