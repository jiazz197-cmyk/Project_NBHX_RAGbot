"""Shared task manager / thread-pool adapters for TaskStatePort and TaskExecutionPort."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.core.executor import executor_manager
from app.core.task_manager import task_manager
from app.core.task_owner_registry import task_owner_registry
from app.ports.contracts.tasking import TaskExecutionPort, TaskStatePort
from app.ports.dto.task_manager import TaskManagerTaskSnapshot


def _to_snapshot(ts: Any) -> TaskManagerTaskSnapshot:
    meta = ts.metadata if getattr(ts, "metadata", None) is not None else {}
    return TaskManagerTaskSnapshot(
        task_id=ts.task_id,
        task_type=ts.task_type,
        status=ts.status,
        created_at=ts.created_at,
        started_at=ts.started_at,
        completed_at=ts.completed_at,
        progress=ts.progress,
        message=ts.message,
        result=ts.result,
        error=ts.error,
        metadata=dict(meta),
    )


class TaskManagerStateAdapter(TaskStatePort):
    async def create_task(self, task_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        task_id = await task_manager.create_task(task_type=task_type, metadata=metadata)
        await task_manager.update_status(task_id, "queued", "任务已排队")
        return task_id

    async def update_status(self, task_id: str, status: str, message: str = "") -> bool:
        return await task_manager.update_status(task_id, status, message)

    async def fail_task(self, task_id: str, error: str, message: str = "任务失败") -> bool:
        return await task_manager.fail_task(task_id, error, message)

    async def update_task_progress(self, task_id: str, progress: int, message: str = "") -> bool:
        return await task_manager.update_task_progress(task_id, progress, message)

    async def update_task_message(self, task_id: str, message: str) -> bool:
        return await task_manager.update_task_message(task_id, message)

    async def get_task_snapshot(self, task_id: str) -> Optional[TaskManagerTaskSnapshot]:
        ts = await task_manager.get_task_status(task_id)
        if not ts:
            return None
        return _to_snapshot(ts)

    async def list_task_snapshots(
        self, *, task_type: Optional[str], limit: int
    ) -> List[TaskManagerTaskSnapshot]:
        rows = await task_manager.list_tasks(task_type=task_type, limit=limit)
        return [_to_snapshot(t) for t in rows]

    async def remove_task_record(self, task_id: str) -> bool:
        return await task_manager.delete_task(task_id)


class ThreadPoolTaskExecutionAdapter(TaskExecutionPort):
    def set_task_owner(self, task_id: str, owner_id: str) -> None:
        # Owner data is owned by TaskOwnerRegistry. This call only warms the cache;
        # the canonical source is the task's domain table (DB / Redis metadata).
        task_owner_registry.cache(task_id, owner_id)

    def cancel_task(self, task_id: str) -> bool:
        return executor_manager.cancel_task(task_id)
