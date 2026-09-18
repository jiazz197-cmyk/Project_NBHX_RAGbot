"""主应用 Memory/会话 HTTP 客户端（.dsh/ragchain-interfaces.md §7）。"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from ._http import HTTPClientMixin


class BackendClient(HTTPClientMixin):
    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._init_http(base_url, timeout, transport=transport, client=client)

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def create_conversation(self, token: str, name: str, inputs: dict) -> dict:
        resp = await self._request(
            "POST",
            "/conversations",
            json={"name": name, "inputs": inputs or {}},
            headers=self._headers(token),
        )
        body = resp.json()
        return body if isinstance(body, dict) else {}

    async def get_messages(
        self,
        token: str,
        conversation_id: str,
        page: int = 1,
        limit: int = 10,
    ) -> list[dict]:
        resp = await self._request(
            "GET",
            "/messages",
            params={
                "conversation_id": conversation_id,
                "page": page,
                "limit": limit,
            },
            headers=self._headers(token),
        )
        body = resp.json()
        if not isinstance(body, dict):
            return []
        data = body.get("data")
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    async def append_messages(
        self,
        token: str,
        conversation_id: str,
        messages: list[dict],
    ) -> dict:
        resp = await self._request(
            "POST",
            f"/conversations/{quote(str(conversation_id), safe='')}/messages",
            json={"messages": messages},
            headers=self._headers(token),
        )
        body = resp.json()
        return body if isinstance(body, dict) else {}

    async def get_latest_summary(self, token: str, user_id: str) -> Optional[str]:
        resp = await self._request(
            "GET",
            f"/chat-summary/query/{quote(str(user_id), safe='')}",
            headers=self._headers(token),
            allow_404=True,
        )
        if resp.status_code == 404:
            return None
        body = resp.json()
        if not isinstance(body, dict):
            return None
        data = body.get("data")
        if not isinstance(data, dict):
            return None
        if data.get("exists") is False:
            return None
        summary = data.get("latest_summary")
        if summary is None:
            return None
        text = str(summary).strip()
        return text or None

    async def compress_context(
        self,
        token: str,
        user_id: str,
        conversation_id: str,
        n_recent: int,
    ) -> Optional[str]:
        resp = await self._request(
            "POST",
            "/context-compression/compress",
            json={
                "user_id": user_id,
                "conversation_id": conversation_id,
                "n_recent": n_recent,
            },
            headers=self._headers(token),
        )
        body = resp.json()
        if not isinstance(body, dict):
            return None
        data = body.get("data")
        if not isinstance(data, dict):
            return None
        compressed = data.get("compressed_context")
        if compressed is None:
            return None
        text = str(compressed).strip()
        return text or None
