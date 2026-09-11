"""Shared access checks for executor-backed tasks."""

from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER


def ensure_executor_task_exists(future: Optional[Any], *, not_found_message: str = "任务不存在") -> None:
    if not future:
        raise NotFoundError(not_found_message)


def ensure_task_owner_or_superuser(
    current_user: CurrentUserPort,
    owner_id: str,
    *,
    detail: str,
) -> None:
    if not current_user.is_superuser() and owner_id != current_user.id:
        raise PermissionDeniedError(detail)
