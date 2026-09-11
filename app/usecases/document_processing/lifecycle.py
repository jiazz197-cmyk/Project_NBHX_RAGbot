"""Cancel and delete document processing tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.core.exceptions import APIException, NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER
from app.ports.contracts.executor_async import ExecutorAsyncTaskPort
from app.ports.contracts.tasking import TaskStatePort

logger = get_logger("document_processing.uc")


@dataclass
class CancelDocumentTaskCommand:
    task_id: str
    current_user: CurrentUserPort


@dataclass
class DeleteDocumentTaskCommand:
    task_id: str
    current_user: CurrentUserPort
    cancel_if_running: bool


def _owner_ok(metadata: dict, current_user: CurrentUserPort, detail: str) -> None:
    owner_id = str(metadata.get("owner_id", "")).strip()
    if not current_user.is_superuser() and owner_id != current_user.id:
        raise PermissionDeniedError(detail)


class CancelDocumentTaskUseCase:
    def __init__(self, task_state: TaskStatePort, executor: ExecutorAsyncTaskPort):
        self._task_state = task_state
        self._executor = executor

    async def execute(self, cmd: CancelDocumentTaskCommand) -> Dict[str, Any]:
        snap = await self._task_state.get_task_snapshot(cmd.task_id)
        if not snap:
            raise NotFoundError(f"任务 {cmd.task_id} 不存在")
        _owner_ok(snap.metadata, cmd.current_user, "无权取消该任务")
        if snap.status in ["completed", "failed"]:
            return {
                "success": False,
                "message": f"任务已{snap.status}，无法取消",
            }
        success = self._executor.cancel_task(cmd.task_id)
        if success:
            fut = self._executor.get_task_future(cmd.task_id)
            if fut is not None and fut.cancelled():
                await self._task_state.fail_task(
                    cmd.task_id,
                    "用户取消任务",
                    "任务在队列中已取消",
                )
            elif snap.status in ("running", "pending"):
                await self._task_state.update_task_message(cmd.task_id, "正在取消任务...")
            return {"success": True, "message": "任务取消请求已发送"}
        return {"success": False, "message": "任务取消失败"}


class DeleteDocumentTaskUseCase:
    def __init__(self, task_state: TaskStatePort, executor: ExecutorAsyncTaskPort):
        self._task_state = task_state
        self._executor = executor

    async def execute(self, cmd: DeleteDocumentTaskCommand) -> Dict[str, Any]:
        snap = await self._task_state.get_task_snapshot(cmd.task_id)
        if not snap:
            raise NotFoundError(f"任务 {cmd.task_id} 不存在")
        _owner_ok(snap.metadata, cmd.current_user, "无权删除该任务")
        if cmd.cancel_if_running and snap.status in ["pending", "running"]:
            logger.info("任务正在运行，先尝试取消: %s", cmd.task_id)
            self._executor.cancel_task(cmd.task_id)
        ok = await self._task_state.remove_task_record(cmd.task_id)
        if not ok:
            raise APIException("删除任务记录失败", status_code=500, error_code="DELETE_FAILED")
        suffix = "取消并" if cmd.cancel_if_running else ""
        return {
            "success": True,
            "message": f"任务 {cmd.task_id} 已{suffix}删除",
        }
