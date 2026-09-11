"""限制 JSON / 上传体积的中间件。"""
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import request_logger


class RequestSizeLimit:
    """封装请求体大小校验逻辑。"""

    def __init__(self):
        self.json_limit = settings.MAX_JSON_SIZE
        self.file_limit = settings.MAX_FILE_SIZE
        self._body_methods = {"POST", "PUT", "PATCH"}

    @staticmethod
    async def _read_body_with_limit(request: Request, size_limit: int) -> tuple[bool, bytes]:
        """分块读 body，超限则 (False, b'')；否则拼接完整 bytes。"""
        total_size = 0
        chunks: list[bytes] = []
        async for chunk in request.stream():
            total_size += len(chunk)
            if total_size > size_limit:
                return False, b""
            chunks.append(chunk)
        return True, b"".join(chunks)

    async def check_request_size(self, request: Request) -> bool:
        """返回 True 表示符合限制。"""
        try:
            content_type = request.headers.get("content-type", "")
            content_length = request.headers.get("content-length")
            size_limit = self.file_limit if "multipart/form-data" in content_type else self.json_limit

            if not content_length:
                if request.method.upper() not in self._body_methods:
                    return True
                within_limit, body = await self._read_body_with_limit(request, size_limit)
                if not within_limit:
                    request_logger.warning(
                        "Chunked/missing-length request body exceeds limit %s",
                        size_limit,
                    )
                    return False
                request._body = body  # type: ignore[attr-defined]
                return True

            content_length = int(content_length)

            if "multipart/form-data" in content_type:
                if content_length > self.file_limit:
                    request_logger.warning(
                        "File upload size %s exceeds limit %s", content_length, self.file_limit
                    )
                    return False
            elif "application/json" in content_type:
                if content_length > self.json_limit:
                    request_logger.warning(
                        "JSON request size %s exceeds limit %s", content_length, self.json_limit
                    )
                    return False

            return True
        except Exception as exc:
            request_logger.error("Request size check failed: %s", exc)
            return False


class RequestSizeMiddleware(BaseHTTPMiddleware):
    """应用层中间件，用于拦截超限请求。"""

    def __init__(self, app):
        super().__init__(app)
        self.limiter = RequestSizeLimit()

    async def dispatch(self, request: Request, call_next: Callable):
        if not settings.ENABLE_REQUEST_SIZE_LIMIT:
            return await call_next(request)

        if not await self.limiter.check_request_size(request):
            return JSONResponse(
                status_code=413,
                content={"code": 413, "message": "请求体过大", "data": None},
            )

        return await call_next(request)
