"""Knowledge use cases — owns business logic and orchestration."""

from __future__ import annotations

from app.core.exceptions import APIException, NotFoundError
from app.core.logging import get_logger
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.knowledge import KnowledgePersistencePort
from app.usecases.knowledge.results import (
    KnowledgeRecordDeleteResult,
    KnowledgeRecordListResult,
)

logger = get_logger("knowledge.usecase")


class ListKnowledgeRecordsUseCase:
    def __init__(self, persistence: KnowledgePersistencePort):
        self._persistence = persistence

    async def execute(self) -> KnowledgeRecordListResult:
        rows = await self._persistence.list_knowledge_records()
        return KnowledgeRecordListResult(success=True, total=len(rows), records=list(rows))


class DeleteKnowledgeRecordUseCase:
    def __init__(self, persistence: KnowledgePersistencePort):
        self._persistence = persistence

    async def execute(
        self, record_id: int, current_user: CurrentUserPort
    ) -> KnowledgeRecordDeleteResult:
        if not await self._persistence.check_knowledge_record_exists(record_id):
            raise NotFoundError("记录不存在")
        rowcount = await self._persistence.delete_knowledge_record(record_id)
        if rowcount <= 0:
            raise APIException("删除失败，请稍后重试", status_code=500)
        logger.info("删除知识库记录: record_id=%s, deleted_by=%s", record_id, current_user.username)
        return KnowledgeRecordDeleteResult(success=True, message="删除成功", deleted_id=str(record_id))
