"""File naming domain logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_unique_filename(original_filename: str) -> str:
    """Return unique name: timestamp + uuid + original base name."""
    unique_id = uuid.uuid4().hex
    timestamp = _now_utc_naive().strftime("%Y%m%d_%H%M%S")
    return f"{timestamp}_{unique_id}{original_filename}"
