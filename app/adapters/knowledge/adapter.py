"""Knowledge domain adapters — thin infra wrappers."""

from __future__ import annotations

from app.adapters.knowledge.persistence import KnowledgePersistence
from app.ports.outbound.knowledge import KnowledgePersistencePort


class KnowledgePersistenceAdapter(KnowledgePersistencePort):
    """Thin wrapper around KnowledgePersistence — no business logic."""

    def __init__(self):
        self._p = KnowledgePersistence()

    async def list_knowledge_records(self):
        return await self._p.list_knowledge_records()

    async def check_knowledge_record_exists(self, record_id: int) -> bool:
        return await self._p.check_knowledge_record_exists(record_id)

    async def delete_knowledge_record(self, record_id: int) -> int:
        return await self._p.delete_knowledge_record(record_id)
