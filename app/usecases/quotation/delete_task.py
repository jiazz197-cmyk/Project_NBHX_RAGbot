"""Usecase: delete terminal quotation task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.core.exceptions import APIException
from app.ports.outbound.quotation import QuotationTaskPurgePort, QuotationTaskRepoPort
from app.usecases.quotation.purge import purge_quotation_task

_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass
class DeleteQuotationTaskCommand:
    task_id: str


@dataclass
class DeleteQuotationTaskResult:
    success: bool
    message: str
    task_id: str
    cleanup: Dict[str, Any]
    task_record_removed: bool


class DeleteQuotationTaskUseCase:
    def __init__(self, task_repo: QuotationTaskRepoPort, purge_port: QuotationTaskPurgePort):
        self._task_repo = task_repo
        self._purge = purge_port

    async def execute(self, cmd: DeleteQuotationTaskCommand) -> DeleteQuotationTaskResult:
        task = await self._task_repo.get_task(cmd.task_id)
        if task is None:
            raise APIException("任务不存在", status_code=404, error_code="NOT_FOUND")

        if task.status not in _TERMINAL_STATUSES:
            raise APIException(
                f"任务当前状态为 {task.status}，仅允许删除已结束任务",
                status_code=400,
                error_code="INVALID_TASK_STATUS",
            )

        result = await purge_quotation_task(cmd.task_id, purge_port=self._purge, allow_non_terminal=False)
        if not result.get("purged"):
            raise APIException(
                "任务删除失败",
                status_code=500,
                error_code="PURGE_FAILED",
            )

        return DeleteQuotationTaskResult(
            success=True,
            message="任务已删除",
            task_id=cmd.task_id,
            cleanup=result.get("cleanup") or {},
            task_record_removed=True,
        )
