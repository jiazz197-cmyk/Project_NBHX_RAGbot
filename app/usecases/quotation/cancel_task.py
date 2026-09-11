"""Usecase: cancel quotation generation task."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import APIException
from app.core.time_utils import utcnow_naive
from app.ports.contracts.tasking import TaskDispatchPort, TaskExecutionPort, TaskStatePort
from app.ports.outbound.quotation import QuotationTaskRepoPort


@dataclass
class CancelQuotationTaskCommand:
    task_id: str


@dataclass
class CancelQuotationTaskResult:
    success: bool
    message: str
    task_id: str


class CancelQuotationTaskUseCase:
    def __init__(
        self,
        task_repo: QuotationTaskRepoPort,
        task_state: TaskStatePort,
        task_execution: TaskExecutionPort,
        task_dispatch: TaskDispatchPort,
    ):
        self._task_repo = task_repo
        self._task_state = task_state
        self._task_execution = task_execution
        self._task_dispatch = task_dispatch

    async def execute(self, cmd: CancelQuotationTaskCommand) -> CancelQuotationTaskResult:
        task = await self._task_repo.get_task(cmd.task_id)
        if task is None:
            raise APIException("任务不存在", status_code=404, error_code="NOT_FOUND")

        if task.status in {"completed", "failed", "cancelled"}:
            return CancelQuotationTaskResult(
                success=False,
                message="任务已结束，无法取消",
                task_id=cmd.task_id,
            )

        if task.status == "queued":
            await self._task_repo.patch_task(
                cmd.task_id,
                {
                    "status": "cancelled",
                    "message": "任务已取消",
                    "error": "用户取消",
                    "completed_at": utcnow_naive(),
                },
            )
            await self._task_state.update_status(cmd.task_id, "cancelled", "任务已取消")
            self._task_dispatch.dispatch_owner_queue(task.owner_id)
            return CancelQuotationTaskResult(True, "排队任务已取消", cmd.task_id)

        if task.status == "awaiting_approval":
            cleanup_result = await self._task_repo.cleanup_task_files(cmd.task_id)
            payload = dict(task.result_payload or {})
            payload["cleanup"] = cleanup_result
            await self._task_repo.patch_task(
                cmd.task_id,
                {
                    "status": "cancelled",
                    "message": "任务已取消（审核阶段）",
                    "error": "用户取消",
                    "completed_at": utcnow_naive(),
                    "awaiting_approval_at": None,
                    "result_payload": payload,
                },
            )
            await self._task_state.update_status(cmd.task_id, "cancelled", "任务已取消")
            self._task_dispatch.dispatch_owner_queue(task.owner_id)
            return CancelQuotationTaskResult(True, "审核阶段任务已取消", cmd.task_id)

        cancelled = self._task_execution.cancel_task(cmd.task_id)
        if not cancelled:
            await self._task_repo.patch_task(
                cmd.task_id,
                {
                    "status": "cancelled",
                    "message": "任务已取消（执行器未持有该任务）",
                    "error": "用户取消",
                    "completed_at": utcnow_naive(),
                },
            )
            await self._task_state.update_status(cmd.task_id, "cancelled", "任务已取消")
            self._task_dispatch.dispatch_owner_queue(task.owner_id)
            return CancelQuotationTaskResult(True, "任务已标记取消", cmd.task_id)

        await self._task_repo.patch_task(cmd.task_id, {"message": "任务正在取消"})
        return CancelQuotationTaskResult(True, "取消请求已发送", cmd.task_id)
