"""HTTP 客户端公共基类与错误映射工具。

所有 client 都允许注入 ``httpx.MockTransport`` / 现成的 ``httpx.AsyncClient``，
便于单测完全不触网。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ..errors import BackendError


def extract_error(resp: httpx.Response) -> tuple[str, str]:
    """按冻结契约从响应中提取 ``(code, message)``。"""
    body: Any = None
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - 非 JSON 响应走兜底
        body = None

    code: str | None = None
    message: str | None = None

    if isinstance(body, dict):
        raw_code = body.get("error_code")
        detail = body.get("detail")
        if isinstance(raw_code, str) and raw_code.strip():
            code = raw_code.strip()
        elif detail is not None:
            code = detail if isinstance(detail, str) else str(detail)

        raw_message = body.get("message")
        if isinstance(raw_message, str) and raw_message.strip():
            message = raw_message.strip()
        elif isinstance(detail, str) and detail.strip():
            message = detail.strip()
        elif detail is not None:
            message = str(detail)

    if not code:
        code = "BACKEND_ERROR"
    if not message:
        message = (resp.text or f"HTTP {resp.status_code}").strip()
    return code, message[:2000]


class HTTPClientMixin:
    """共享连接管理、URL 拼接与统一错误映射。"""

    _default_error_code = "BACKEND_ERROR"

    def _init_http(
        self,
        base_url: str,
        timeout: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = float(timeout)
        if client is not None:
            self._client = client
        else:
            kwargs: dict[str, Any] = {"timeout": self._timeout}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            if transport is not None:
                kwargs["transport"] = transport
            self._client = httpx.AsyncClient(**kwargs)

    def _url(self, path: str) -> str:
        if not path:
            return self._base_url
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self._base_url}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        try:
            resp = await self._client.request(
                method,
                self._url(path),
                params=params,
                json=json,
                data=data,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise BackendError(
                "BACKEND_UNAVAILABLE",
                f"外部服务不可达: {exc.__class__.__name__}: {exc}",
                status=503,
            ) from exc

        if allow_404 and resp.status_code == 404:
            return resp
        if resp.status_code < 200 or resp.status_code >= 300:
            code, message = extract_error(resp)
            if code == "BACKEND_ERROR":
                code = self._default_error_code
            raise BackendError(code, message, status=resp.status_code)
        return resp

    async def aclose(self) -> None:
        await self._client.aclose()
