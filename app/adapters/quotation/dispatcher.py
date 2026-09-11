"""Quotation task dispatcher: dequeues queued tasks to running state.

Moved from app.core.quotation_dispatcher to app.adapters.quotation.dispatcher
as part of Clean Architecture Phase 1 refactoring.
"""

from __future__ import annotations

import asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.time_utils import utcnow_naive
from app.core.logging import get_logger
from app.domain.quotation.entities import QuotationTaskStatus
from app.models.orm.quotation_task import QuotationTask
from app.ports.outbound.quotation import DispatchCandidate, QuotationDispatchPort

logger = get_logger("quotation_dispatcher")


class QuotationDispatcherAdapter(QuotationDispatchPort):
    """Controls running quotas and dequeues queued tasks.

    Keep owner-based dispatch entrypoints for fairness, and enforce IP quota
    at actual dequeue stage. Internally manages DB sessions.
    """

    def __init__(self, max_running_per_owner: int = 2, max_running_per_ip: int = 2):
        self._max_running_per_owner = max_running_per_owner
        self._max_running_per_ip = max_running_per_ip
        self._lock: asyncio.Lock | None = None

    async def _dequeue_for_owner(self, db: AsyncSession, owner_id: str) -> list[DispatchCandidate]:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            running_result = await db.execute(
                select(func.count())
                .select_from(QuotationTask)
                .where(
                    QuotationTask.owner_id == owner_id,
                    QuotationTask.status == QuotationTaskStatus.running.value,
                )
            )
            running_count = int(running_result.scalar_one())
            owner_available = max(self._max_running_per_owner - running_count, 0)
            if owner_available <= 0:
                return []

            queued_result = await db.execute(
                select(QuotationTask)
                .where(
                    QuotationTask.owner_id == owner_id,
                    QuotationTask.status == QuotationTaskStatus.queued.value,
                )
                .order_by(QuotationTask.created_at.asc())
            )
            queued_tasks = list(queued_result.scalars().all())
            if not queued_tasks:
                return []

            candidate_ips = {
                str(task.owner_ip).strip()
                for task in queued_tasks
                if getattr(task, "owner_ip", None) and str(task.owner_ip).strip()
            }

            running_by_ip: dict[str, int] = {}
            if candidate_ips:
                ip_result = await db.execute(
                    select(QuotationTask.owner_ip, func.count(QuotationTask.id))
                    .where(
                        QuotationTask.status == QuotationTaskStatus.running.value,
                        QuotationTask.owner_ip.in_(candidate_ips),
                    )
                    .group_by(QuotationTask.owner_ip)
                )
                ip_rows = ip_result.all()
                running_by_ip = {
                    str(owner_ip).strip(): int(count)
                    for owner_ip, count in ip_rows
                    if owner_ip and str(owner_ip).strip()
                }

            selected_tasks: list[QuotationTask] = []
            for task in queued_tasks:
                if len(selected_tasks) >= owner_available:
                    break

                owner_ip = str(getattr(task, "owner_ip", "") or "").strip()
                if not owner_ip:
                    logger.warning(
                        "Task missing owner_ip, applying owner quota only: task_id=%s owner_id=%s",
                        task.task_id,
                        task.owner_id,
                    )
                    selected_tasks.append(task)
                    continue

                current_ip_running = running_by_ip.get(owner_ip, 0)
                if current_ip_running >= self._max_running_per_ip:
                    logger.debug(
                        "IP 配额已满，跳过排队任务: task_id=%s owner_id=%s owner_ip=%s running=%s/%s",
                        task.task_id,
                        task.owner_id,
                        owner_ip,
                        current_ip_running,
                        self._max_running_per_ip,
                    )
                    continue

                selected_tasks.append(task)
                running_by_ip[owner_ip] = current_ip_running + 1

            if not selected_tasks:
                return []

            now = utcnow_naive()
            for task in selected_tasks:
                task.status = QuotationTaskStatus.running.value
                task.started_at = now
                task.progress = max(task.progress, 1)
                task.message = "任务已开始处理"

            await db.commit()

            return [DispatchCandidate(task_id=task.task_id, owner_id=task.owner_id) for task in selected_tasks]

    async def dequeue_for_owner(self, owner_id: str) -> list[DispatchCandidate]:
        """Move queued tasks to running state when owner/IP has free slots."""
        async with AsyncSessionLocal() as db:
            return await self._dequeue_for_owner(db, owner_id)

    async def list_queued_owners(self) -> list[str]:
        """Return distinct owner_ids that currently have queued tasks.

        Per-IP quota is a cross-owner shared resource: when a task completes and
        frees an IP slot, every owner sharing that IP must be reconsidered, not
        only the completing task's own owner. The global re-dispatch sweep uses
        this list to avoid starving queued tasks that belong to a different
        owner (which the per-owner done-callback cannot reach).
        """
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(QuotationTask.owner_id)
                .where(QuotationTask.status == QuotationTaskStatus.queued.value)
                .group_by(QuotationTask.owner_id)
            )
            return [
                str(oid).strip()
                for oid in result.scalars().all()
                if oid and str(oid).strip()
            ]


quotation_dispatcher = QuotationDispatcherAdapter(
    max_running_per_owner=settings.QUOTATION_MAX_RUNNING_PER_OWNER,
    max_running_per_ip=settings.QUOTATION_MAX_RUNNING_PER_IP,
)
