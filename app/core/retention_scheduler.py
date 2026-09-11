"""Background scheduler for quotation task retention."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("quotation.retention_scheduler")

_stop_event = asyncio.Event()
_loop_task: Optional[asyncio.Task] = None

# Throttle the (relatively expensive) MinIO orphan sweep — it scans bucket
# prefixes, so we don't want it on every retention tick. Monotonic clock is
# safe to use here (not wall-clock / Date.now).
_last_reconcile_ts: float = 0.0


async def run_retention_once() -> None:
    from app.adapters.quotation.purge import QuotationTaskPurgeAdapter
    from app.adapters.quotation.retention import QuotationTaskRetentionAdapter
    from app.core.minio_reconcile import reconcile_orphans

    purge_adapter = QuotationTaskPurgeAdapter()
    retention_adapter = QuotationTaskRetentionAdapter(purge_adapter)
    try:
        await retention_adapter.reclaim_stale_running_tasks(
            timeout_sec=settings.QUOTATION_RUNNING_TIMEOUT_SEC,
        )
        await retention_adapter.expire_awaiting_approval_tasks(
            ttl_hours=settings.QUOTATION_AWAITING_APPROVAL_TTL_HOURS,
        )
        await retention_adapter.purge_old_terminal_tasks_global(
            max_total=settings.QUOTATION_RETENTION_MAX_TOTAL,
            target=settings.QUOTATION_RETENTION_TARGET,
        )
    except Exception:
        logger.exception("Quotation retention run failed")

    # Orphan reconciliation runs independently — a failure in retention above must
    # not skip the safety-net sweep. Throttled to MINIO_RECONCILE_INTERVAL_SEC so
    # the bucket scan does not run on every retention tick.
    global _last_reconcile_ts
    now_ts = time.monotonic()
    if now_ts - _last_reconcile_ts >= settings.MINIO_RECONCILE_INTERVAL_SEC:
        _last_reconcile_ts = now_ts
        try:
            await reconcile_orphans(grace_sec=settings.MINIO_RECONCILE_GRACE_SEC)
        except Exception:
            logger.exception("MinIO reconcile run failed")


async def _retention_loop(interval_sec: int) -> None:
    while not _stop_event.is_set():
        await run_retention_once()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass


def start_retention_scheduler() -> asyncio.Task:
    global _loop_task
    _stop_event.clear()
    interval = settings.QUOTATION_RETENTION_INTERVAL_SEC
    _loop_task = asyncio.create_task(_retention_loop(interval))
    logger.info("Quotation retention scheduler started (interval=%ss)", interval)
    return _loop_task


async def stop_retention_scheduler() -> None:
    global _loop_task
    _stop_event.set()
    if _loop_task is not None:
        try:
            await asyncio.wait_for(_loop_task, timeout=5.0)
        except asyncio.TimeoutError:
            _loop_task.cancel()
        _loop_task = None
    logger.info("Quotation retention scheduler stopped")
