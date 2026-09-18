"""Reranker HTTP 客户端（.dsh/ragchain-interfaces.md §9）。"""

from __future__ import annotations

from typing import Any

import httpx

from ..errors import BackendError
from ._http import HTTPClientMixin


class RerankerClient(HTTPClientMixin):
    _default_error_code = "RERANKER_ERROR"

    def __init__(
        self,
        url: str,
        model: str,
        api_key: str,
        timeout: float = 30.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key or ""
        self._init_http(url, timeout, transport=transport, client=client)

    def _headers(self) -> dict[str, str]:
        if self._api_key.strip():
            return {"Authorization": f"Bearer {self._api_key.strip()}"}
        return {}

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []

        resp = await self._request(
            "POST",
            "",
            json={
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
            headers=self._headers(),
        )

        try:
            body: Any = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise BackendError("RERANKER_ERROR", f"重排响应不是合法 JSON: {exc}", 502) from exc
        if not isinstance(body, dict):
            raise BackendError("RERANKER_ERROR", "重排响应不是 JSON 对象", 502)

        items = body.get("results")
        index_key = "index"
        score_fallback = ("relevance_score", "score")
        if not isinstance(items, list):
            items = body.get("rankings")
            index_key = "doc_index"
            score_fallback = ("score",)
        if not isinstance(items, list):
            raise BackendError("RERANKER_ERROR", "重排响应缺少 results/rankings", 502)

        parsed: list[tuple[int, float]] = []
        for item in items:
            if not isinstance(item, dict):
                raise BackendError("RERANKER_ERROR", "重排结果条目格式错误", 502)
            raw_idx = item.get(index_key)
            if raw_idx is None:
                raise BackendError("RERANKER_ERROR", f"重排结果缺少 {index_key}", 502)
            try:
                idx = int(raw_idx)
                score = None
                for key in score_fallback:
                    if item.get(key) is not None:
                        score = float(item[key])
                        break
                if score is None:
                    raise KeyError(score_fallback)
            except (TypeError, ValueError, KeyError) as exc:
                raise BackendError(
                    "RERANKER_ERROR", f"重排结果字段无法解析: {item}", 502
                ) from exc
            if idx < 0 or idx >= len(documents):
                raise BackendError(
                    "RERANKER_ERROR", f"重排结果 index 越界: {idx}", 502
                )
            parsed.append((idx, score))

        parsed.sort(key=lambda pair: pair[1], reverse=True)
        if top_n is not None and top_n >= 0:
            parsed = parsed[:top_n]
        return parsed
