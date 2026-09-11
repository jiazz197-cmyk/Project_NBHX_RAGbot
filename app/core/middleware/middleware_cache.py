"""GET 响应可缓存时走 Redis。"""
import hashlib
import json
from typing import Callable, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cache import redis_manager
from app.core.config import settings
from app.core.logging import request_logger

EXCLUDED_PATHS = {
    "/api/v1/health",
    "/api/v1/metrics",
    "/api/v1/docs",
    "/api/v1/openapi.json",
    "/api/v1/redoc",
}


class CacheMiddleware(BaseHTTPMiddleware):
    """简单的 GET 请求缓存中间件。"""

    def __init__(self, app: FastAPI):
        super().__init__(app)
        self.cache_ttl = settings.CACHE_TTL

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.ENABLE_CACHE or not self._should_cache(request):
            return await call_next(request)

        cache_key = self._generate_cache_key(request)
        cached_response = await self._get_cached_response(cache_key)
        if cached_response:
            request_logger.debug("Cache hit: %s", cache_key)
            return cached_response

        response = await call_next(request)
        if response.status_code == 200:
            await self._cache_response(cache_key, response)
        return response

    def _should_cache(self, request: Request) -> bool:
        if request.method != "GET":
            return False
        if request.url.path in EXCLUDED_PATHS:
            return False
        if request.headers.get("Authorization"):
            return False
        cache_control = request.headers.get("Cache-Control", "")
        return not any(flag in cache_control for flag in ("no-cache", "no-store"))

    def _generate_cache_key(self, request: Request) -> str:
        key_parts = [request.url.path]
        if request.query_params:
            key_parts.append(str(request.query_params))
        auth_header = request.headers.get("Authorization", "")
        if auth_header:
            auth_fingerprint = hashlib.sha256(auth_header.encode("utf-8")).hexdigest()[:12]
            key_parts.append(f"auth:{auth_fingerprint}")
        return f"cache:{':'.join(key_parts)}"

    async def _get_cached_response(self, cache_key: str) -> Optional[Response]:
        cached_data = await redis_manager.get(cache_key)
        if not cached_data:
            return None
        data = cached_data if isinstance(cached_data, dict) else json.loads(cached_data)
        return JSONResponse(
            content=data["content"],
            status_code=data["status_code"],
            headers=data["headers"],
        )

    async def _cache_response(self, cache_key: str, response: Response) -> None:
        if isinstance(response, StreamingResponse) or response.status_code != 200:
            return

        body = await self._extract_body(response)
        if not body:
            return
        try:
            content = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            request_logger.debug("Response body not JSON, skip cache: %s", cache_key)
            return

        cache_data = {
            "content": content,
            "status_code": response.status_code,
            "headers": {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-length", "transfer-encoding")
            },
        }
        await redis_manager.set(cache_key, cache_data, ttl=self.cache_ttl)
        request_logger.debug("Response cached: %s", cache_key)

    @staticmethod
    async def _extract_body(response: Response) -> bytes:
        if hasattr(response, "body") and response.body:
            return response.body
        if hasattr(response, "body_iterator"):
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            response.body_iterator = iter(chunks)
            return b"".join(chunks)
        return b""
