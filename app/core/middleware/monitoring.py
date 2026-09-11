"""记录请求耗时并写入 Prometheus metrics。"""
import time
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.adapters.monitoring import get_request_metrics
from app.core.logging import get_logger

logger = get_logger("monitoring")
request_metrics = get_request_metrics()


def _normalize_endpoint(request: Request) -> str:
    """优先用路由模板路径，避免 metrics 标签爆炸。"""
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return request.url.path


class MonitoringMiddleware(BaseHTTPMiddleware):
    """记录请求耗时、状态并写入 metrics。"""

    async def dispatch(self, request: Request, call_next: Callable):
        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start_time
            logger.exception("Request failed: %s %s", request.method, request.url.path)
            request_metrics.record_request(
                method=request.method,
                endpoint=_normalize_endpoint(request),
                status_code=500,
                duration=duration,
            )
            raise

        duration = time.perf_counter() - start_time
        request_metrics.record_request(
            method=request.method,
            endpoint=_normalize_endpoint(request),
            status_code=response.status_code,
            duration=duration,
        )
        response.headers["X-Process-Time"] = f"{duration:.6f}"
        return response
