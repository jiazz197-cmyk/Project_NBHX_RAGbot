"""Quotation task retention adapter: encapsulates SQLAlchemy queries."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.domain.quotation.entities import QuotationTaskStatus
from app.models.orm.quotation_task import QuotationTask
from app.ports.outbound.quotation import QuotationTaskPurgePort, QuotationTaskRetentionPort

logger = get_logger("quotation.retention")

_TERMINAL_STATUSES = (
    QuotationTaskStatus.completed.value,
    QuotationTaskStatus.failed.value,
    QuotationTaskStatus.cancelled.value,
)


class QuotationTaskRetentionAdapter(QuotationTaskRetentionPort):
    """Encapsulates SQLAlchemy queries for quotation task retention."""

    def __init__(self, purge_port: QuotationTaskPurgePort):
        self._purge = purge_port

    async def purge_old_terminal_tasks_global(self, max_total: int = 100, target: int = 50) -> int:
        async with AsyncSessionLocal() as db:
            total_result = await db.execute(select(func.count()).select_from(QuotationTask))
            total = int(total_result.scalar_one())
            if total <= max_total:
                return 0

            to_delete = total - target
            candidates_result = await db.execute(
                select(QuotationTask.task_id)
                .where(QuotationTask.status.in_(_TERMINAL_STATUSES))
                .order_by(QuotationTask.created_at.asc())
                .limit(to_delete)
            )
            candidates = candidates_result.all()

            purged = 0
            sample_ids: list[str] = []
            for (task_id,) in candidates:
                result = await self._purge.purge_task(task_id, allow_non_terminal=False)
                if result.get("purged"):
                    purged += 1
                    if len(sample_ids) < 5:
                        sample_ids.append(task_id)

            if purged:
                logger.info(
                    "Global terminal retention: purged=%s total_before=%s target=%s sample=%s",
                    purged, total, target, sample_ids,
                )
            return purged

    async def expire_awaiting_approval_tasks(self, ttl_hours: int = 24) -> int:
        async with AsyncSessionLocal() as db:
            cutoff = utcnow_naive() - timedelta(hours=ttl_hours)
            expired_result = await db.execute(
                select(QuotationTask.task_id)
                .where(
                    QuotationTask.status == QuotationTaskStatus.awaiting_approval.value,
                    QuotationTask.awaiting_approval_at.isnot(None),
                    QuotationTask.awaiting_approval_at < cutoff,
                )
            )
            expired = expired_result.all()

            purged = 0
            sample_ids: list[str] = []
            for (task_id,) in expired:
                result = await self._purge.purge_task(task_id, allow_non_terminal=True)
                if result.get("purged"):
                    purged += 1
                    if len(sample_ids) < 5:
                        sample_ids.append(task_id)

            if purged:
                logger.info(
                    "Awaiting approval expiry: purged=%s ttl_hours=%s sample=%s",
                    purged, ttl_hours, sample_ids,
                )
            return purged

    async def reclaim_stale_running_tasks(self, timeout_sec: int = 1800) -> int:
        """Reclaim tasks stuck in running longer than timeout_sec.

        A running task whose started_at is older than the cutoff is assumed to be
        a hung worker (MinIO stall, killed thread, etc.) — purge it (files + DB row)
        so it stops occupying the owner's running slot. purge_task's status guard
        is bypassed via allow_non_terminal=True (mirrors awaiting_approval expiry).
        """
        async with AsyncSessionLocal() as db:
            cutoff = utcnow_naive() - timedelta(seconds=timeout_sec)
            stale_result = await db.execute(
                select(QuotationTask.task_id)
                .where(
                    QuotationTask.status == QuotationTaskStatus.running.value,
                    QuotationTask.started_at.isnot(None),
                    QuotationTask.started_at < cutoff,
                )
            )
            stale = stale_result.all()

            purged = 0
            sample_ids: list[str] = []
            for (task_id,) in stale:
                result = await self._purge.purge_task(task_id, allow_non_terminal=True)
                if result.get("purged"):
                    purged += 1
                    if len(sample_ids) < 5:
                        sample_ids.append(task_id)
                else:
                    logger.warning(
                        "Stale running task not purged: task_id=%s reason=%s status=%s",
                        task_id, result.get("reason"), result.get("status"),
                    )

            if purged:
                logger.warning(
                    "Stale running reclaim: purged=%s timeout_sec=%s sample=%s",
                    purged, timeout_sec, sample_ids,
                )
            return purged
