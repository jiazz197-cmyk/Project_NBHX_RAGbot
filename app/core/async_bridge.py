"""Run coroutines from sync code when no event loop is active."""

from __future__ import annotations

import asyncio
from typing import TypeVar

T = TypeVar("T")


def run_async(coro) -> T:
    """Execute *coro*; use asyncio.run when no loop is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "run_async() cannot be used inside a running event loop; await the coroutine instead"
    )
