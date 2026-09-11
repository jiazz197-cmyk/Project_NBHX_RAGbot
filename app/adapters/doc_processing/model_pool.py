"""有界对象池：用于 PaddleOCR / TagGenerator 等重 GPU 模型的全局复用。

镜像 ``app.adapters.sqlserver.client.PymssqlConnectionPool`` 的语义，泛型化
（factory 注入）。核心约束：

- ``max_size`` 闸住**存活实例总数（idle + 已借出）**，超出则 acquire 阻塞等待。
- checkout 互斥：一个实例同一时刻只被一个线程持有。PaddleOCR 3.x 的 ``ocr()``
  非线程安全，靠这一互斥保证共享实例不崩，同时保留 ``max_size`` 路并行推理。
- ``acquire`` 超时或 ``factory()`` 失败时返回 ``None``，调用方自行降级（OCR 跳过
  该页 / 标签退 CPU），不抛错——重模型是降级路径，不应让任务整体失败。
- 正常归还 → release 回 idle；调用期间抛异常 → discard（保守丢弃，防 CUDA 损坏
  残留进 idle 被后续线程复用）。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Generator, List, Optional


class BoundedInstancePool:
    """Thread-safe, bounded pool of heavyweight model instances.

    A ``BoundedSemaphore`` (``_slots``) gates the total number of live instances
    (idle + checked-out) to ``max_size``. ``acquire`` reserves a slot (blocking up
    to ``timeout``) then reuses an idle instance or creates one via ``factory``;
    the context manager ``release``-es on normal exit and ``discard``-s on any
    exception. Idle instances do NOT hold slots — a slot represents "one
    checked-out instance", so reuse from idle is free.

    Designed as a long-lived GLOBAL shared pool: all worker threads check
    instances out of one instance so the GPU never holds more than ``max_size``
    copies of the model regardless of how many tasks/threads exist.
    """

    def __init__(
        self,
        factory: Callable[[], object],
        max_size: int,
        name: str,
        logger,
    ):
        if max_size < 0:
            raise ValueError(f"{name}: max_size 必须 >= 0，得到 {max_size}")
        self._factory = factory
        self._max_size = max_size
        self._name = name
        self._logger = logger
        self._idle: List[object] = []
        self._lock = threading.Lock()
        self._created_count = 0
        self._closed = False
        # One permit per allowed live instance. Acquired on checkout/create,
        # released on release/discard. Bounded so a stray double-release raises.
        self._slots = threading.BoundedSemaphore(self._max_size) if self._max_size > 0 else None

    @property
    def max_size(self) -> int:
        return self._max_size

    def _acquire_slot(self, timeout: Optional[float]) -> bool:
        """Reserve a live-instance permit. Returns False on timeout/disabled/closed."""
        if self._slots is None:
            # max_size == 0：池禁用，调用方拿 None 走降级。
            return False
        if not self._slots.acquire(timeout=timeout):
            return False
        with self._lock:
            if self._closed:
                self._slots.release()
                return False
        return True

    def _get_or_create(self):
        """Caller already holds a slot. Reuse idle or create new (outside lock).

        Returns ``None`` (and releases the slot) when the factory raises OR
        explicitly returns ``None`` (e.g. PaddleOCR 不可用时 ``_create_paddleocr``
        返回 None)——两种情况都视作“未能产出实例”，必须释放槽位，否则会逐次泄漏
        直到耗尽池。
        """
        with self._lock:
            if self._idle:
                # 复用空闲实例：slot 已持有，created_count 不变。
                return self._idle.pop()
            self._created_count += 1
        # 建实例在锁外执行，避免长时间持锁阻塞其他线程
        instance = None
        try:
            instance = self._factory()
        except Exception:
            self._logger.warning(
                "%s 池: 创建实例失败，已释放槽位，调用方将降级", self._name, exc_info=True
            )
        if instance is None:
            with self._lock:
                if self._created_count > 0:
                    self._created_count -= 1
            if self._slots is not None:
                self._slots.release()
            return None
        return instance

    @contextmanager
    def acquire(self, timeout: Optional[float] = None) -> Generator[Optional[object], None, None]:
        """Check out an instance, blocking up to ``timeout`` seconds.

        Yields the instance, or ``None`` when the pool is disabled / saturated
        past the timeout / the factory failed to build one. Callers MUST handle
        ``None`` by degrading. On ANY exception during the ``with`` body the
        instance is discarded (closed + slot freed); on normal exit it is
        returned to idle.
        """
        if not self._acquire_slot(timeout):
            yield None
            return
        instance = self._get_or_create()
        if instance is None:
            # _get_or_create 失败时已自行释放 slot，这里无需再 release。
            yield None
            return
        try:
            yield instance
        except BaseException:
            # 调用期间抛错（含 BaseException 如取消）→ 丢弃实例，防残留。
            self._discard(instance)
            raise
        else:
            self._release(instance)

    def _release(self, instance: object) -> None:
        """Return a healthy instance to the idle list (frees its slot)."""
        if instance is None:
            return
        closed = False
        with self._lock:
            if self._closed:
                closed = True
                if self._created_count > 0:
                    self._created_count -= 1
            else:
                self._idle.append(instance)
        if closed:
            self._safe_close(instance)
        if self._slots is not None:
            self._slots.release()

    def _discard(self, instance: object) -> None:
        """Drop a possibly-broken instance: close it, decrement counter, free slot."""
        if instance is None:
            return
        with self._lock:
            if self._created_count > 0:
                self._created_count -= 1
        self._safe_close(instance)
        if self._slots is not None:
            self._slots.release()

    def _safe_close(self, instance: object) -> None:
        closer = getattr(instance, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass

    def close(self) -> None:
        """Shutdown: drop all idle instances and mark the pool closed.

        Idle instances do NOT hold slots (their slot was already returned in
        ``_release``), so we must NOT release a slot per idle instance here —
        that would over-release the ``BoundedSemaphore``. Blocked acquires are
        woken by borrower ``_release``/``_discard`` (which still release slots
        post-close) or by their own timeout; either way they see ``_closed``
        and return ``None``.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            idle = self._idle
            self._idle = []
            n_idle = len(idle)
            if self._created_count >= n_idle:
                self._created_count -= n_idle
        for instance in idle:
            self._safe_close(instance)
        self._logger.debug(
            "%s 池已关闭: created_total=%s", self._name, self._created_count
        )

    @property
    def created_count(self) -> int:
        with self._lock:
            return self._created_count

    @property
    def idle_count(self) -> int:
        with self._lock:
            return len(self._idle)
