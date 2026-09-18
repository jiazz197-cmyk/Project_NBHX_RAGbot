"""主应用 Retriever HTTP 客户端（.dsh/ragchain-interfaces.md §8）。"""

from __future__ import annotations

from typing import Any

import httpx

from ._http import HTTPClientMixin


class RetrieverClient(HTTPClientMixin):
    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._init_http(base_url, timeout, transport=transport, client=client)

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def query_db(
        self,
        token: str,
        collection: str,
        question: str,
        top_k: int,
        rerank: bool = False,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/retriever/db",
            params={
                "collection": collection,
                "top_k": top_k,
                "rerank": "true" if rerank else "false",
            },
            json={"question": question},
            headers=self._headers(token),
        )
        body = resp.json()
        if not isinstance(body, dict):
            return {"chunks": []}
        if not isinstance(body.get("chunks"), list):
            # 旧版 /db 仅返回 answer/sources；steps 需能兼容回退。
            body["chunks"] = []
        return body

    async def query_excel(
        self,
        token: str,
        collection: str,
        question: str,
        top_k: int,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/retriever/excel",
            params={"collection": collection, "top_k": top_k},
            json={"question": question},
            headers=self._headers(token),
        )
        body = resp.json()
        if not isinstance(body, dict):
            return {"answer": "", "sources": []}
        if "sources" not in body:
            body["sources"] = []
        return body
