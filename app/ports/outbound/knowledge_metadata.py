"""Knowledge metadata outbound port: file-name based chunk lookup and removal."""

from __future__ import annotations

from typing import Optional, Protocol

from app.ports.dto.knowledge_upload import KnowledgeFileConflict


class KnowledgeMetadataPort(Protocol):
    """按 file_name 查询 / 删除 collection 内 chunk 元数据（无业务逻辑）。

    collection 传逻辑集合名（如 ``knowledge_chunks``），
    实现方负责映射到物理表 ``data_<collection>``。
    """

    async def find_conflict(
        self, collection: str, file_name: str
    ) -> Optional[KnowledgeFileConflict]:
        """返回同名文件的冲突摘要；无同名返回 None。"""

    async def delete_chunks_by_file_name(self, collection: str, file_name: str) -> int:
        """删除该 file_name 的旧 chunk，返回删除行数。"""
