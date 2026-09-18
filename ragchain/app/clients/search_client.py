"""SearXNG 风格联网搜索客户端（.dsh/ragchain-interfaces.md §10）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..errors import BackendError
from ._http import HTTPClientMixin


@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    content: str = ""


class SearchClient(HTTPClientMixin):
    _default_error_code = "SEARCH_ERROR"

    def __init__(
        self,
        url: str,
        timeout: float = 15.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._init_http(url, timeout, transport=transport, client=client)

    async def search(self, query: str, count: int = 5) -> list[SearchResult]:
        resp = await self._request(
            "POST",
            "",
            data={"q": query, "format": "json"},
        )
        try:
            body: Any = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BackendError("SEARCH_ERROR", f"搜索响应不是合法 JSON: {exc}", 502) from exc
        if not isinstance(body, dict):
            return []
        results = body.get("results")
        if not isinstance(results, list):
            return []

        parsed: list[SearchResult] = []
        for item in results:
            if count is not None and count >= 0 and len(parsed) >= count:
                break
            if not isinstance(item, dict):
                continue
            parsed.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    content=str(item.get("content") or ""),
                )
            )
        return parsed
