"""Lightweight circuit breaker for external SQLServer (U8/PDM) calls.

Provides failure isolation: when a backend (e.g. U8 BOM query) starts timing out
(error 20003) or refusing connections, the breaker opens and fast-fails subsequent
calls instead of queuing them on worker threads for the full 120s query timeout.
This prevents cascading stalls across the quotation pipeline.

Design:
- States: CLOSED (normal) -> OPEN (fast-fail) -> HALF_OPEN (probe).
- Counts consecutive failures; on reaching threshold, opens for `open_sec`.
- After `open_sec`, one trial call is allowed (HALF_OPEN); success closes,
  failure re-opens. Concurrent trial calls during HALF_OPEN are fast-failed.
- Thread-safe (worker threads share one breaker per backend).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("circuit_breaker")


class CircuitBreakerOpenError(Exception):
    """Raised when a call is attempted while the breaker is open."""


class CircuitBreaker:
    """Consecutive-failure circuit breaker."""

    def __init__(
        self,
        name: str,
        fail_threshold: Optional[int] = None,
        open_sec: Optional[int] = None,
    ):
        self.name = name
        self._fail_threshold = fail_threshold or settings.SQLSERVER_CB_FAIL_THRESHOLD
        self._open_sec = open_sec or settings.SQLSERVER_CB_OPEN_SEC
        self._failures = 0
        self._state = "closed"  # closed | open | half_open
        self._opened_at: float = 0.0
        self._trial_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            return self._effective_state_locked()

    def _effective_state_locked(self) -> str:
        """Compute current state, transitioning open->half_open after open_sec."""
        if self._state == "open":
            if time.monotonic() - self._opened_at >= self._open_sec:
                self._state = "half_open"
        return self._state

    def before_call(self) -> None:
        """Call before invoking the protected operation. Raises if open."""
        with self._lock:
            state = self._effective_state_locked()
            if state == "open":
                raise CircuitBreakerOpenError(
                    f"熔断器[{self.name}]处于开启状态，快速失败（已连续失败 {self._failures} 次）"
                )
            # half_open: allow exactly one trial call. While the trial is in
            # flight every other caller fast-fails — this guarantees a single
            # probe even when the trial outlasts open_sec (e.g. a slow U8 query
            # whose 120s timeout exceeds the re-open window). The trial resolves
            # via record_success (→ closed) or record_failure (→ open, re-armed).
            if state == "half_open":
                if self._trial_in_flight:
                    raise CircuitBreakerOpenError(
                        f"熔断器[{self.name}]半开探测进行中，快速失败"
                    )
                self._trial_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._trial_in_flight = False
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            was_trial = self._trial_in_flight
            self._trial_in_flight = False
            if was_trial or self._failures >= self._fail_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()
                if self._failures == self._fail_threshold:
                    logger.warning(
                        "熔断器[%s]开启：连续失败 %s 次，将 fast-fail %ss",
                        self.name, self._failures, self._open_sec,
                    )

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._trial_in_flight = False
            self._state = "closed"


# Per-backend singletons (U8 / PDM). Keyed by a stable backend identifier.
_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def get_breaker(name: str) -> CircuitBreaker:
    """Get or create the singleton breaker for a named backend."""
    with _breakers_lock:
        if name not in _breakers:
            _breakers[name] = CircuitBreaker(name)
        return _breakers[name]
