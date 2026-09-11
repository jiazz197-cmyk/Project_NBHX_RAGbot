"""Monitoring adapters: Prometheus metrics backend + system health checks.

Import explicitly from submodules (e.g. ``app.adapters.monitoring.metrics_adapter``).
The package re-exports ``get_request_metrics`` so existing
``from app.adapters.monitoring import get_request_metrics`` callers keep working.
"""

from __future__ import annotations

from app.adapters.monitoring.metrics_adapter import (
    get_request_metrics,
)

__all__ = ["get_request_metrics"]
