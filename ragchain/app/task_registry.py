"""进程内任务注册表（.dsh/ragchain-interfaces.md §6）。"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

import asyncio


@dataclass
class TaskEntry:
    task_id: str
    user_id: str
    conversation_id: str
    cancel_event: asyncio.Event
    created_at: float
    finished: bool = False
    result: str = "running"

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()


class TaskRegistry:
    """单进程内存注册表；单 event loop 内使用，不做线程安全。"""

    def __init__(self, ttl_sec: float):
        self.ttl_sec = float(ttl_sec)
        self._tasks: Dict[str, TaskEntry] = {}

    # ------------------------------------------------------------------ utils
    def _now(self) -> float:
        return time.time()

    def _is_expired(self, entry: TaskEntry, now: float | None = None) -> bool:
        if self.ttl_sec <= 0:
            # ttl=0 表示立即过期，但本轮创建/刚写入的任务仍应可被当前请求使用，
            # 因此严格的 0 值按「不小于 0 秒即过期」处理仅在查询历史项时生效。
            return True
        return (now if now is not None else self._now()) - entry.created_at > self.ttl_sec

    def _cleanup(self) -> None:
        if not self._tasks:
            return
        now = self._now()
        expired = [tid for tid, entry in self._tasks.items() if self._is_expired(entry, now)]
        for tid in expired:
            self._tasks.pop(tid, None)

    # ----------------------------------------------------------------- public
    def register(self, user_id: str, conversation_id: str = "") -> TaskEntry:
        self._cleanup()
        entry = TaskEntry(
            task_id=str(uuid.uuid4()),
            user_id=str(user_id),
            conversation_id=conversation_id or "",
            cancel_event=asyncio.Event(),
            created_at=self._now(),
        )
        self._tasks[entry.task_id] = entry
        return entry

    def update_conversation(self, task_id: str, conversation_id: str) -> None:
        entry = self.get(task_id)
        if entry is not None:
            entry.conversation_id = conversation_id or ""

    def get(self, task_id: str) -> Optional[TaskEntry]:
        self._cleanup()
        return self._tasks.get(task_id)

    def request_stop(self, task_id: str, user_id: str) -> Optional[TaskEntry]:
        """他人/过期/不存在返回 None；TTL 内命中（含已结束）返回 entry。"""
        entry = self.get(task_id)
        if entry is None:
            return None
        if entry.user_id != str(user_id):
            return None
        entry.cancel_event.set()
        return entry

    def finish(self, task_id: str, result: str) -> None:
        entry = self._tasks.get(task_id)
        if entry is None:
            return
        entry.finished = True
        entry.result = result
