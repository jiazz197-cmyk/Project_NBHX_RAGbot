"""SQL Server query outbound ports."""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol

from app.domain.exceptions import QueryCancelledError, U8RootFailureBreakerError  # noqa: F401

CancelChecker = Optional[Callable[[], bool]]


class U8BomInventoryQueryPort(Protocol):
    def run(
        self,
        payload: Any,
        *,
        cancel_checker: CancelChecker = None,
        user_key: Optional[str] = None,
    ) -> Any:
        ...


class PdmBomQueryPort(Protocol):
    def run(self, payload: Any, *, cancel_checker: CancelChecker = None) -> Any:
        ...


class PdmMatchQueryPort(Protocol):
    def run(self, payload: Any, *, cancel_checker: CancelChecker = None) -> Any:
        ...
