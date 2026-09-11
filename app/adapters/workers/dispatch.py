"""Quotation task dispatch adapter (driving side).

Wraps the background worker dispatch entrypoints behind ``TaskDispatchPort`` so
that driven adapters (e.g. persistence) can trigger phase2 / owner-queue
dispatch without importing the workers package directly. Lives alongside the
workers it drives (driving side), keeping the driving/driven split clean.
"""

from __future__ import annotations

from app.ports.contracts.tasking import TaskDispatchPort

from .quotation_generation.quotation_task_workers import (
    dispatch_quotation_phase2,
    dispatch_quotation_queue_for_owner,
)


class QuotationDispatchAdapter(TaskDispatchPort):
    def dispatch_owner_queue(self, owner_id: str) -> None:
        dispatch_quotation_queue_for_owner(owner_id)

    def dispatch_phase2(self, task_id: str, owner_id: str) -> None:
        dispatch_quotation_phase2(task_id, owner_id)
