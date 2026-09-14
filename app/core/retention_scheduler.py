"""Background scheduler for MinIO orphan reconciliation."""

from __future__ import annotations

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("minio.reconcile_scheduler")

_stop_event = asyncio.Event()
_loop_task: Optional[asyncio.Task] = None


async def run_reconcile_once() -> None:
    from app.core.minio_reconcile import reconcile_orphans

    try:
        await reconcile_orphans(grace_sec=settings.MINIO_RECONCILE_GRACE_SEC)
    except Exception:
        logger.exception("MinIO reconcile run failed")


async def _reconcile_loop(interval_sec: int) -> None:
    while not _stop_event.is_set():
        await run_reconcile_once()
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass


def start_reconcile_scheduler() -> asyncio.Task:
    global _loop_task
    _stop_event.clear()
    interval = settings.MINIO_RECONCILE_INTERVAL_SEC
    _loop_task = asyncio.create_task(_reconcile_loop(interval))
    logger.info("MinIO reconcile scheduler started (interval=%ss)", interval)
    return _loop_task


async def stop_reconcile_scheduler() -> None:
    global _loop_task
    _stop_event.set()
    if _loop_task is not None:
        try:
            await asyncio.wait_for(_loop_task, timeout=5.0)
        except asyncio.TimeoutError:
            _loop_task.cancel()
        _loop_task = None
    logger.info("MinIO reconcile scheduler stopped")
