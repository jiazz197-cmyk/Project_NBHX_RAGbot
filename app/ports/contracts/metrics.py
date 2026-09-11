"""Request-level monitoring metrics port."""

from __future__ import annotations

from typing import Protocol


class RequestMetricsPort(Protocol):
    def record_request(self, method: str, endpoint: str, status_code: int, duration: float) -> None:
        ...
