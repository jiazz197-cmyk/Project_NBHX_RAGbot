"""Redis 滑动窗口限流。"""
from dataclasses import dataclass
import ipaddress
import time
from typing import Callable, Tuple, Any

from fastapi import Request
from fastapi.responses import JSONResponse
import jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.cache import redis_manager
from app.core.config import settings
from app.core.logging import security_logger
from app.ports.contracts.identity import ROLE_ADMIN, ROLE_SUPERUSER

# 享有 admin 分档限流预算的角色
_ADMIN_ROLES = (ROLE_ADMIN, ROLE_SUPERUSER)


@dataclass
class RateLimitDecision:
    allowed: bool
    retry_after: int | None = None
    redis_error: bool = False


class RateLimiter:
    """按认证状态区分限流，使用滑动窗口。"""

    def __init__(self):
        self.auth_limit = settings.RATE_LIMIT_AUTH
        self.anon_limit = settings.RATE_LIMIT_ANON
        self.expensive_auth_limit = settings.RATE_LIMIT_EXPENSIVE_AUTH
        self.expensive_anon_limit = settings.RATE_LIMIT_EXPENSIVE_ANON
        self.auth_admin_limit = settings.RATE_LIMIT_AUTH_ADMIN
        self.expensive_auth_admin_limit = settings.RATE_LIMIT_EXPENSIVE_AUTH_ADMIN
        self.window_size = settings.RATE_LIMIT_WINDOW
        self.expensive_path_prefixes = (
            f"{settings.API_V1_STR}/retriever",
            f"{settings.API_V1_STR}/chat-summary/create",
            f"{settings.API_V1_STR}/context-compression",
            f"{settings.API_V1_STR}/sqlserver",
            f"{settings.API_V1_STR}/ocr",
            f"{settings.API_V1_STR}/document-tasks",
            f"{settings.API_V1_STR}/docs",
            f"{settings.API_V1_STR}/image2url",
            f"{settings.API_V1_STR}/pdf2image",
        )
        self.trust_proxy_headers = settings.TRUST_PROXY_HEADERS
        self.trusted_proxy_networks = self._build_trusted_proxy_networks(settings.TRUSTED_PROXIES)

    @staticmethod
    def _build_trusted_proxy_networks(proxies: list[str]) -> list[Any]:
        networks: list[Any] = []
        for raw in proxies:
            value = str(raw).strip()
            if not value:
                continue
            try:
                if "/" in value:
                    networks.append(ipaddress.ip_network(value, strict=False))
                else:
                    ip = ipaddress.ip_address(value)
                    prefix = 32 if ip.version == 4 else 128
                    networks.append(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
            except ValueError:
                security_logger.warning("Ignored invalid trusted proxy config: %s", value)
        return networks

    def _is_trusted_proxy(self, client_ip: str) -> bool:
        if not self.trusted_proxy_networks:
            return False
        try:
            ip_obj = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        return any(ip_obj in network for network in self.trusted_proxy_networks)

    def _get_effective_client_ip(self, request: Request) -> str:
        direct_ip = (request.client.host if request.client else "") or "unknown"
        if not self.trust_proxy_headers:
            return direct_ip
        if not self._is_trusted_proxy(direct_ip):
            return direct_ip

        x_forwarded_for = request.headers.get("x-forwarded-for", "")
        if not x_forwarded_for:
            return direct_ip

        first_hop = x_forwarded_for.split(",", 1)[0].strip()
        try:
            ipaddress.ip_address(first_hop)
        except ValueError:
            security_logger.warning("Invalid X-Forwarded-For value: %s", x_forwarded_for)
            return direct_ip
        return first_hop

    async def _get_client_identifier(self, request: Request) -> Tuple[str, bool, str | None]:
        """返回 (identifier, is_authenticated, role)。

        role 从 JWT ``role`` claim 读取（旧 token 无该 claim → None，视为非 admin）。
        identifier 仍为 ``auth:{user_id}``，保持每用户独立桶、跨部署连续。
        """
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1].strip()
            if token:
                try:
                    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
                    user_id = str(payload.get("sub", "")).strip()
                    if user_id:
                        role = payload.get("role")
                        role = role if isinstance(role, str) else None
                        return f"auth:{user_id}", True, role
                except jwt.PyJWTError:
                    pass
        client_ip = self._get_effective_client_ip(request)
        return f"anon:{client_ip}", False, None

    def _get_limit_for_request(self, request: Request, is_authenticated: bool, role: str | None) -> int:
        is_admin = is_authenticated and role in _ADMIN_ROLES
        if request.url.path.startswith(self.expensive_path_prefixes):
            if is_admin:
                return self.expensive_auth_admin_limit
            return self.expensive_auth_limit if is_authenticated else self.expensive_anon_limit
        if is_admin:
            return self.auth_admin_limit
        return self.auth_limit if is_authenticated else self.anon_limit

    async def check_rate_limit(self, request: Request) -> RateLimitDecision:
        identifier, is_authenticated, role = await self._get_client_identifier(request)
        limit = self._get_limit_for_request(request, is_authenticated, role)
        current_time = int(time.time())
        window_key = f"rate_limit:{identifier}:{current_time // self.window_size}"

        try:
            count = await redis_manager.redis_client.incr(window_key)
            if count == 1:
                await redis_manager.redis_client.expire(window_key, self.window_size)
        except Exception as exc:
            security_logger.warning("Rate limiter fallback due to Redis error: %s", exc)
            if settings.RATE_LIMIT_FAIL_OPEN:
                return RateLimitDecision(allowed=True, redis_error=True)
            return RateLimitDecision(
                allowed=False,
                retry_after=1,
                redis_error=True,
            )

        if count > limit:
            retry_after = self.window_size - (current_time % self.window_size)
            retry_after = max(retry_after, 1)
            security_logger.warning("Rate limit exceeded for %s", identifier)
            return RateLimitDecision(allowed=False, retry_after=retry_after)
        return RateLimitDecision(allowed=True)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """在 FastAPI 中应用限流策略。"""

    def __init__(self, app):
        super().__init__(app)
        self.limiter = RateLimiter()

    async def dispatch(self, request: Request, call_next: Callable):
        if not settings.ENABLE_RATE_LIMIT:
            return await call_next(request)

        decision = await self.limiter.check_rate_limit(request)
        if not decision.allowed:
            headers = {}
            if decision.retry_after is not None:
                headers["Retry-After"] = str(decision.retry_after)

            if decision.redis_error:
                status_code = settings.RATE_LIMIT_REDIS_ERROR_STATUS
                message = "服务繁忙，请稍后重试"
            else:
                status_code = 429
                message = "请求过于频繁，请稍后再试"

            return JSONResponse(
                status_code=status_code,
                headers=headers,
                content={"code": status_code, "message": message, "data": None},
            )

        return await call_next(request)
