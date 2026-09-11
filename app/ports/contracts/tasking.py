"""Generic async task lifecycle ports (TaskManager + executor owner binding)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from app.ports.dto.task_manager import TaskManagerTaskSnapshot


class TaskStatePort(Protocol):
    async def create_task(self, task_type: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        ...

    async def fail_task(self, task_id: str, error: str, message: str = "任务失败") -> bool:
        ...

    async def update_task_progress(self, task_id: str, progress: int, message: str = "") -> bool:
        ...

    async def update_task_message(self, task_id: str, message: str) -> bool:
        ...

    async def update_status(self, task_id: str, status: str, message: str = "") -> bool:
        ...

    async def get_task_snapshot(self, task_id: str) -> Optional[TaskManagerTaskSnapshot]:
        ...

    async def list_task_snapshots(
        self, *, task_type: Optional[str], limit: int
    ) -> List[TaskManagerTaskSnapshot]:
        ...

    async def remove_task_record(self, task_id: str) -> bool:
        ...


class TaskDispatchPort(Protocol):
    def dispatch_owner_queue(self, owner_id: str) -> None:
        ...

    def dispatch_phase2(self, task_id: str, owner_id: str) -> None:
        ...


class TaskExecutionPort(Protocol):
    def set_task_owner(self, task_id: str, owner_id: str) -> None:
        ...

    def cancel_task(self, task_id: str) -> bool:
        ...
