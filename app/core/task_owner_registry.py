"""Centralized task ownership registry.

Decouples authorization data from executor concurrency lifecycle and from
TaskManager TTL caches. Each task domain registers a `TaskOwnerLookup`
provider that points at its real source of truth:

    quotation_generation_*  -> Postgres `quotation_tasks.owner_id`
    doc_process_*           -> TaskManager Redis metadata
    other (e.g. pdf_convert_*, image_upload_*)
                            -> in-memory cache only (no persistent truth)

The in-memory cache is a write-through optimisation; persistence lookups
are async to avoid blocking the event loop on DB/Redis I/O.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Protocol, Tuple

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.task_manager import task_manager
from app.models.orm.quotation_task import QuotationTask

logger = get_logger("task_owner_registry")


class TaskOwnerLookup(Protocol):
    """Source-of-truth provider for one task domain."""

    def matches(self, task_id: str) -> bool: ...

    async def get_owner_id(self, task_id: str) -> Optional[str]: ...


class _QuotationOwnerLookup:
    """quotation_generation_* tasks live in `quotation_tasks` table."""

    PREFIX = "quotation_generation_"

    def matches(self, task_id: str) -> bool:
        return task_id.startswith(self.PREFIX)

    async def get_owner_id(self, task_id: str) -> Optional[str]:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(QuotationTask.owner_id).where(
                        QuotationTask.task_id == task_id
                    )
                )
                row = result.scalar()
            value = str(row or "").strip()
            return value or None
        except Exception as exc:
            logger.warning(
                "[task_owner_registry] quotation lookup failed: task_id=%s err=%s",
                task_id,
                exc,
            )
            return None


class _DocProcessingOwnerLookup:
    """doc_process_* tasks live in TaskManager Redis metadata."""

    PREFIX = "doc_process_"

    def matches(self, task_id: str) -> bool:
        return task_id.startswith(self.PREFIX)

    async def get_owner_id(self, task_id: str) -> Optional[str]:
        try:
            ts = await task_manager.get_task_status(task_id)
        except Exception as exc:
            logger.warning(
                "[task_owner_registry] doc_processing lookup failed: task_id=%s err=%s",
                task_id,
                exc,
            )
            return None
        if not ts:
            return None
        value = str(ts.metadata.get("owner_id", "")).strip()
        return value or None


class TaskOwnerRegistry:
    """In-memory cache fronted by an ordered chain of source-of-truth providers.

    The cache stores (owner_id, inserted_ts) and is bounded by a TTL and a hard
    size cap so that long-running processes with many short-lived OCR/doc tasks
    do not accumulate entries forever (the forget() path only covers quotation
    tasks purged via retention)."""

    def __init__(self, lookups: List[TaskOwnerLookup]):
        # task_id -> (owner_id, monotonic_inserted_ts)
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._lookups: List[TaskOwnerLookup] = list(lookups)

    def _ttl_sec(self) -> float:
        return float(getattr(settings, "TASK_OWNER_CACHE_TTL_SEC", 86400))

    def _max_entries(self) -> int:
        return int(getattr(settings, "TASK_OWNER_CACHE_MAX", 5000))

    def _evict_expired_locked(self, now_ts: float) -> None:
        """Drop entries older than TTL. Caller holds the lock."""
        ttl = self._ttl_sec()
        if ttl <= 0:
            return
        expired = [tid for tid, (_, ts) in self._cache.items() if now_ts - ts > ttl]
        for tid in expired:
            self._cache.pop(tid, None)

    def _enforce_cap_locked(self) -> None:
        """If over the hard cap, evict the oldest entries by inserted ts."""
        cap = self._max_entries()
        if len(self._cache) <= cap:
            return
        # Sort by inserted ts ascending and drop the oldest surplus.
        surplus = len(self._cache) - cap
        ordered = sorted(self._cache.items(), key=lambda kv: kv[1][1])
        for tid, _ in ordered[:surplus]:
            self._cache.pop(tid, None)

    def cache(self, task_id: str, owner_id: str) -> None:
        """Warm the cache with an owner_id (no-op for empty strings)."""
        normalized = str(owner_id or "").strip()
        if not normalized:
            return
        now_ts = time.monotonic()
        with self._lock:
            self._evict_expired_locked(now_ts)
            self._cache[task_id] = (normalized, now_ts)
            self._enforce_cap_locked()

    def forget(self, task_id: str) -> None:
        with self._lock:
            self._cache.pop(task_id, None)

    def peek_cache(self, task_id: str) -> str:
        """Sync cache-only read. Returns empty string on miss / expired entry."""
        now_ts = time.monotonic()
        ttl = self._ttl_sec()
        with self._lock:
            entry = self._cache.get(task_id)
            if entry is None:
                return ""
            owner_id, ts = entry
            if ttl > 0 and now_ts - ts > ttl:
                # Expired — drop and report miss.
                self._cache.pop(task_id, None)
                return ""
            return owner_id

    async def resolve(self, task_id: str) -> Optional[str]:
        """Cache -> first matching provider. Backfills cache on hit."""
        cached = self.peek_cache(task_id)
        if cached:
            return cached

        for lookup in self._lookups:
            if not lookup.matches(task_id):
                continue
            owner_id = await lookup.get_owner_id(task_id)
            if owner_id:
                self.cache(task_id, owner_id)
                return owner_id
            # First matching provider is authoritative for its domain.
            return None

        return None


task_owner_registry = TaskOwnerRegistry(
    [
        _QuotationOwnerLookup(),
        _DocProcessingOwnerLookup(),
    ]
)


async def resolve_task_owner_id(task_id: str) -> Optional[str]:
    """Convenience for callers (WS authz) that only need the resolved owner."""
    return await task_owner_registry.resolve(task_id)
