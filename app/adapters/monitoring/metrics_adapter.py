"""Monitoring adapter bridging the metrics port with the Prometheus backend."""

from __future__ import annotations

from app.adapters.monitoring.prometheus import metrics
from app.ports.contracts.metrics import RequestMetricsPort


class PrometheusRequestMetricsAdapter(RequestMetricsPort):
    def record_request(self, method: str, endpoint: str, status_code: int, duration: float) -> None:
        metrics.record_request(
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            duration=duration,
        )


_request_metrics_adapter = PrometheusRequestMetricsAdapter()


def get_request_metrics() -> RequestMetricsPort:
    return _request_metrics_adapter
