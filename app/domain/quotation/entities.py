"""Quotation domain entities."""

from __future__ import annotations

from enum import Enum


class QuotationTaskStatus(str, Enum):
    """Domain enumeration for quotation task lifecycle statuses."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    awaiting_approval = "awaiting_approval"
