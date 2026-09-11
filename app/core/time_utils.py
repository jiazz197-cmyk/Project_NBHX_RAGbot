"""Centralized UTC time utilities.

All datetime generation in the project should go through this module so that
every timestamp is timezone-aware UTC (or explicitly naive UTC for DB compat).
This prevents the silent bugs that arise when ``datetime.utcnow()`` (naive UTC),
``datetime.now()`` (naive local), and ``datetime.now(timezone.utc)`` (aware UTC)
are mixed in comparisons, arithmetic, or serialisation.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    Use for: ISO-format strings, JSON payloads, comparisons with other aware
    datetimes, and anywhere the +00:00 suffix is beneficial.
    """
    return datetime.now(tz=timezone.utc)


def utcnow_naive() -> datetime:
    """Naive UTC now (tzinfo stripped).

    Use for: writing to DB columns that use plain ``DateTime`` (no
    ``timezone=True``), filename timestamps via ``.strftime()``, and
    compatibility with existing code that expects naive datetimes.
    """
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def utc_timestamp() -> float:
    """UTC epoch timestamp (seconds since Unix epoch).

    Use for: duration calculations, performance measurements that need
    wall-clock seconds.
    """
    return datetime.now(tz=timezone.utc).timestamp()


def utc_from_timestamp(ts: float) -> datetime:
    """Timezone-aware UTC datetime from a Unix timestamp."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)
