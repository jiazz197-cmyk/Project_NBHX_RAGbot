"""主应用 Retriever HTTP 客户端（.dsh/ragchain-interfaces.md §8）。"""

from __future__ import annotations

from typing import Any, Sequence

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

    @staticmethod
    def _body(question: str, keywords: Sequence[str] | None) -> dict[str, Any]:
        """请求体：仅在有关键词时带 keywords（issue #16）。

        无关键词时不出现该键，保证老调用方的 payload 形状与改造前完全一致。
        """
        body: dict[str, Any] = {"question": question}
        if keywords:
            body["keywords"] = [str(kw) for kw in keywords]
        return body

    async def query_db(
        self,
        token: str,
        collection: str,
        question: str,
        top_k: int,
        rerank: bool = False,
        keywords: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/retriever/db",
            params={
                "collection": collection,
                "top_k": top_k,
                "rerank": "true" if rerank else "false",
            },
            json=self._body(question, keywords),
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
        keywords: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/retriever/excel",
            params={"collection": collection, "top_k": top_k},
            json=self._body(question, keywords),
            headers=self._headers(token),
        )
        body = resp.json()
        if not isinstance(body, dict):
            return {"answer": "", "sources": []}
        if "sources" not in body:
            body["sources"] = []
        return body
