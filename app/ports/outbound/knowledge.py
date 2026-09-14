"""Knowledge domain outbound ports."""

from __future__ import annotations

from typing import Protocol


class KnowledgePersistencePort(Protocol):
    """Atomic persistence operations for knowledge records (no business logic)."""

    async def list_knowledge_records(self) -> list[dict]:
        ...

    async def check_knowledge_record_exists(self, record_id: int) -> bool:
        ...

    async def delete_knowledge_record(self, record_id: int) -> int:
        ...
