"""Reranker HTTP 客户端（.dsh/ragchain-interfaces.md §9）。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..errors import BackendError
from ._http import HTTPClientMixin

logger = logging.getLogger(__name__)

# 重排网关（GPUSTACK + bge-reranker-v2-m3，8192 token）的上下文限制是**按单条文档**算的，
# 不是整批总量：2026-09-18 实测 10 条 × 8000 字符（共 8 万字符）→ 200，单条 20000 字符
# → 400（"maximum context length is 8192 tokens ... 13351 tokens"）。空串同样整次 400
# （"Only one multi-modal item is supported"）。所以这里只做「单条上限 + 空串占位」，
# 不能按文档数均分（均分会把答案截掉，实测 600 字符/条会切掉 814 字符 chunk 末尾的行）。
_RERANK_MAX_DOC_CHARS = 6000
_EMPTY_DOC_PLACEHOLDER = "（空内容）"


def prepare_rerank_documents(
    documents: list[str],
    max_doc_chars: int = _RERANK_MAX_DOC_CHARS,
) -> list[str]:
    """清洗重排入参：空文档替换为占位符、单条超长则截断。

    返回列表长度与顺序**不变**——重排 API 返回的 index 是对入参下标，
    丢元素会让引用错位；因此只做替换/截断，不做过滤。

    6000 字符 ≈ 4000 token，对中文留足余量（实测 20000 字符 ≈ 13351 token）。
    """
    prepared: list[str] = []
    for document in documents:
        text = str(document or "").strip()
        if not text:
            text = _EMPTY_DOC_PLACEHOLDER
        if max_doc_chars > 0 and len(text) > max_doc_chars:
            text = text[:max_doc_chars]
        prepared.append(text)
    return prepared


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

        payload_documents = prepare_rerank_documents(documents)
        if payload_documents != list(documents):
            logger.info(
                "重排入参已清洗：%d 条文档（空串占位 / 单条上限 %d 字符）",
                len(documents),
                _RERANK_MAX_DOC_CHARS,
            )

        resp = await self._request(
            "POST",
            "",
            json={
                "model": self._model,
                "query": query,
                "documents": payload_documents,
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
