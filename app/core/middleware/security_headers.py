"""为响应补充常见安全相关 Header。"""
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """X-Frame-Options、CSP 相关基础头等。"""

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)

        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

        if settings.ENABLE_HSTS:
            request_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
            if request_scheme == "https":
                response.headers.setdefault(
                    "Strict-Transport-Security",
                    f"max-age={settings.HSTS_MAX_AGE}; includeSubDomains",
                )

        return response
