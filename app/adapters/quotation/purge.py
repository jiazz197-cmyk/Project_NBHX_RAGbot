"""Quotation task purge adapter: encapsulates SQLAlchemy queries."""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.executor import executor_manager
from app.core.task_manager import task_manager
from app.core.task_owner_registry import task_owner_registry
from app.core.quotation_task_cleanup import safe_cleanup_quotation_task_files_async
from app.models.orm.quotation_task import QuotationTask
from app.ports.outbound.quotation import QuotationTaskPurgePort

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_RETENTION_PURGE_STATUSES = _TERMINAL_STATUSES | {"awaiting_approval", "running"}


class QuotationTaskPurgeAdapter(QuotationTaskPurgePort):
    """Encapsulates SQLAlchemy queries for quotation task purging."""

    async def _purge_with_session(
        self, session: AsyncSession, task_id: str, *, allow_non_terminal: bool
    ) -> Dict[str, Any]:
        result = await session.execute(
            select(QuotationTask).where(QuotationTask.task_id == task_id)
        )
        task = result.scalars().first()
        if task is None:
            return {"purged": False, "reason": "not_found", "task_id": task_id}

        allowed = _RETENTION_PURGE_STATUSES if allow_non_terminal else _TERMINAL_STATUSES
        if task.status not in allowed:
            return {
                "purged": False,
                "reason": "status_not_allowed",
                "task_id": task_id,
                "status": task.status,
            }

        cleanup_result = await safe_cleanup_quotation_task_files_async(session, task, task_id)
        task_owner_registry.forget(task_id)
        executor_manager.forget_task(task_id)

        try:
            import asyncio
            fut = asyncio.ensure_future(task_manager.delete_task(task_id))

            def _log_delete_failure(f):
                try:
                    exc = f.exception()
                except asyncio.CancelledError:
                    return
                if exc is not None:
                    logger.warning(f"清理 TaskManager 任务失败 task_id={task_id}: {exc}")

            fut.add_done_callback(_log_delete_failure)
        except Exception as exc:
            logger.warning(f"调度 TaskManager 任务清理失败 task_id={task_id}: {exc}")

        await session.delete(task)
        await session.commit()
        return {
            "purged": True,
            "task_id": task_id,
            "cleanup": cleanup_result,
        }

    async def purge_task(self, task_id: str, *, allow_non_terminal: bool = False) -> Dict[str, Any]:
        async with AsyncSessionLocal() as db:
            try:
                return await self._purge_with_session(
                    db, task_id, allow_non_terminal=allow_non_terminal
                )
            except Exception:
                await db.rollback()
                raise
