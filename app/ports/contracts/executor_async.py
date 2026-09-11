"""Generic thread-pool task introspection (polling, cancel, stats)."""

from __future__ import annotations

from typing import Any, Optional, Protocol


class ExecutorAsyncTaskPort(Protocol):
    def get_task_future(self, task_id: str) -> Optional[Any]:
        ...

    def get_task_owner(self, task_id: str) -> str:
        ...

    def cancel_task(self, task_id: str) -> bool:
        ...

    def get_active_task_count(self) -> int:
        ...

    def get_running_task_count(self) -> int:
        ...
