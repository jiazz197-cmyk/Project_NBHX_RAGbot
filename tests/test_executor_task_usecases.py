"""Unit tests for executor-backed task use cases (fake ports)."""

from __future__ import annotations

import uuid
from concurrent.futures import Future
from typing import Any, Dict, Optional

import pytest

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.orm.platform.user import UserRole
from app.ports.contracts.identity import CurrentUserDTO
from app.usecases.async_executor.executor_task_query import (
    GetExecutorTaskStatusQuery,
    GetExecutorTaskStatusUseCase,
)
from app.usecases.async_executor.task_access import ensure_task_owner_or_superuser


def _user(*, uid: Optional[uuid.UUID] = None, role: UserRole = UserRole.user) -> Any:
    return CurrentUserDTO(id=str(uid or uuid.uuid4()), role=role.value, username="tester")


class FakeExecutorPort:
    def __init__(self):
        self._futures: Dict[str, Future] = {}
        self._owners: Dict[str, str] = {}

    def set_fake_task(self, task_id: str, owner_id: str, future: Optional[Future] = None) -> None:
        self._owners[task_id] = owner_id
        self._futures[task_id] = future if future is not None else Future()

    def get_task_future(self, task_id: str) -> Optional[Any]:
        return self._futures.get(task_id)

    def get_task_owner(self, task_id: str) -> str:
        return self._owners.get(task_id, "")

    def cancel_task(self, task_id: str) -> bool:
        return True

    def get_active_task_count(self) -> int:
        return 0

    def get_running_task_count(self) -> int:
        return 0


def test_ensure_task_owner_denies_regular_user_other_owner():
    owner = uuid.uuid4()
    victim = uuid.uuid4()
    user = _user(uid=victim)
    with pytest.raises(PermissionDeniedError):
        ensure_task_owner_or_superuser(user, str(owner), detail="no access")


def test_get_executor_task_status_denies_when_not_owner():
    ex = FakeExecutorPort()
    tid = "t1"
    fut: Future = Future()
    fut.set_result({"status": "completed"})
    owner_id = str(uuid.uuid4())
    ex.set_fake_task(tid, owner_id, fut)

    user = _user()
    uc = GetExecutorTaskStatusUseCase(ex)
    with pytest.raises(PermissionDeniedError):
        uc.execute(
            GetExecutorTaskStatusQuery(
                task_id=tid,
                current_user=user,
                forbidden_detail="无权查看该任务",
            )
        )


def test_get_executor_task_status_not_found():
    ex = FakeExecutorPort()
    user = _user()
    uc = GetExecutorTaskStatusUseCase(ex)
    with pytest.raises(NotFoundError):
        uc.execute(
            GetExecutorTaskStatusQuery(
                task_id="missing",
                current_user=user,
                forbidden_detail="无权查看该任务",
            )
        )
