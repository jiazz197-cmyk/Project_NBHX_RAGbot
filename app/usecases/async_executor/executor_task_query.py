"""Poll, fetch result, cancel, and stats for executor-backed tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from app.core.exceptions import APIException, PermissionDeniedError
from app.core.logging import get_logger
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER
from app.ports.contracts.executor_async import ExecutorAsyncTaskPort
from app.usecases.async_executor.task_access import (
    ensure_executor_task_exists,
    ensure_task_owner_or_superuser,
)

logger = get_logger("executor_task_query")


@dataclass
class ExecutorTaskCommandBase:
    task_id: str
    current_user: CurrentUserPort


@dataclass
class GetExecutorTaskStatusQuery(ExecutorTaskCommandBase):
    forbidden_detail: str


@dataclass
class GetExecutorTaskResultQuery(ExecutorTaskCommandBase):
    forbidden_detail: str


@dataclass
class CancelExecutorTaskCommand(ExecutorTaskCommandBase):
    forbidden_detail: str
    done_conflict_detail: str


@dataclass
class CancelExecutorTaskResult:
    task_id: str
    message: str
    cancelled: bool


class GetExecutorTaskStatusUseCase:
    def __init__(self, executor: ExecutorAsyncTaskPort):
        self._executor = executor

    def execute(self, query: GetExecutorTaskStatusQuery) -> Dict[str, Any]:
        future = self._executor.get_task_future(query.task_id)
        ensure_executor_task_exists(future)
        owner_id = self._executor.get_task_owner(query.task_id)
        ensure_task_owner_or_superuser(
            query.current_user,
            owner_id,
            detail=query.forbidden_detail,
        )
        if not future.done():
            return {
                "task_id": query.task_id,
                "status": "running",
                "message": "任务正在执行中",
            }
        try:
            result = future.result(timeout=0.1)
            if isinstance(result, dict) and result.get("status") == "cancelled":
                return {
                    "task_id": query.task_id,
                    "status": "cancelled",
                    "message": result.get("message", "任务已取消"),
                }
            if isinstance(result, dict) and result.get("status") == "error":
                return {
                    "task_id": query.task_id,
                    "status": "failed",
                    "message": result.get("message", "任务执行失败"),
                    "error": result.get("error"),
                }
            return {
                "task_id": query.task_id,
                "status": "completed",
                "message": "任务完成",
                "result": result,
            }
        except Exception as e:
            logger.error("任务 %s 执行失败: %s", query.task_id, e)
            return {
                "task_id": query.task_id,
                "status": "failed",
                "message": "任务执行失败",
                "error": str(e),
            }


class GetExecutorTaskResultUseCase:
    def __init__(self, executor: ExecutorAsyncTaskPort):
        self._executor = executor

    def execute(self, query: GetExecutorTaskResultQuery) -> Dict[str, Any]:
        future = self._executor.get_task_future(query.task_id)
        ensure_executor_task_exists(future)
        owner_id = self._executor.get_task_owner(query.task_id)
        ensure_task_owner_or_superuser(
            query.current_user,
            owner_id,
            detail=query.forbidden_detail,
        )
        if not future.done():
            raise APIException(
                "任务尚未完成，请稍后再试",
                status_code=400,
                error_code="TASK_NOT_READY",
            )
        try:
            result = future.result(timeout=0.1)
            if isinstance(result, dict):
                if result.get("status") == "success":
                    return {
                        "task_id": query.task_id,
                        "status": "success",
                        "result": result,
                    }
                if result.get("status") == "cancelled":
                    raise APIException("任务已取消", status_code=400, error_code="TASK_CANCELLED")
                if result.get("status") == "error":
                    raise APIException(
                        f"任务执行失败: {result.get('message', '未知错误')}",
                        status_code=500,
                        error_code="TASK_FAILED",
                    )
            return {
                "task_id": query.task_id,
                "result": result,
            }
        except APIException:
            raise
        except Exception as e:
            logger.error("任务 %s 执行失败: %s", query.task_id, e)
            raise APIException("任务执行失败", status_code=500, error_code="TASK_FAILED") from e


class CancelExecutorTaskUseCase:
    def __init__(self, executor: ExecutorAsyncTaskPort):
        self._executor = executor

    def execute(self, cmd: CancelExecutorTaskCommand) -> CancelExecutorTaskResult:
        future = self._executor.get_task_future(cmd.task_id)
        ensure_executor_task_exists(future)
        owner_id = self._executor.get_task_owner(cmd.task_id)
        ensure_task_owner_or_superuser(
            cmd.current_user,
            owner_id,
            detail=cmd.forbidden_detail,
        )
        if future.done():
            raise APIException(
                cmd.done_conflict_detail,
                status_code=400,
                error_code="TASK_ALREADY_DONE",
            )
        success = self._executor.cancel_task(cmd.task_id)
        if success:
            return CancelExecutorTaskResult(
                task_id=cmd.task_id,
                message="任务取消请求已发送（任务需主动检查取消标志）",
                cancelled=True,
            )
        raise APIException("取消任务失败", status_code=500, error_code="CANCEL_FAILED")


@dataclass
class ExecutorTaskStatsQuery:
    current_user: CurrentUserPort


@dataclass
class ExecutorTaskStatsResult:
    total_tasks: int
    running_tasks: int
    completed_tasks: int
    message: str


class GetExecutorTaskStatsUseCase:
    def __init__(self, executor: ExecutorAsyncTaskPort):
        self._executor = executor

    def execute(self, query: ExecutorTaskStatsQuery) -> ExecutorTaskStatsResult:
        if not query.current_user.is_superuser():
            raise PermissionDeniedError("仅超级管理员可查看任务统计")
        active_count = self._executor.get_active_task_count()
        running_count = self._executor.get_running_task_count()
        return ExecutorTaskStatsResult(
            total_tasks=active_count,
            running_tasks=running_count,
            completed_tasks=active_count - running_count,
            message="ExecutorManager 简化模式：仅提供统计信息，不支持详细列表",
        )
