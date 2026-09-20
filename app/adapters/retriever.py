"""RAG retriever adapter wrapping ragsystem implementations."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.ports.outbound.retriever import ChartAnalysisPort, RetrievalQuery, RetrievalResult, RetrieverPort

# RetrievalQuery.top_k 的 dataclass 默认值（10）对旧调用没有“显式指定”语义；
# API 层仅在 HTTP 显式传 top_k 时往 metadata 打这个标记，以便显式 top_k=10
# 也能走结构化 chunks 路径。该路径不读取 metadata["rerank"]：显式 top_k 时
# 始终返回纯向量 chunks，重排由调用方负责。详见 _should_return_chunks。
TOP_K_EXPLICIT_META_KEY = "top_k_explicit"
_LEGACY_TOP_K_DEFAULT = RetrievalQuery.__dataclass_fields__["top_k"].default


def _should_return_chunks(q: RetrievalQuery) -> bool:
    """显式传 top_k>0 时走 get_chunks；未传 top_k 的旧调用保持 get_response。

    不能直接判断 ``q.top_k > 0``：RetrievalQuery 不传 top_k 时字段默认 10。
    因此以 metadata 标记为准；同时兼容直接构造且 top_k 非默认值的既有调用方。
    """
    if q.top_k is None or q.top_k <= 0:
        return False
    explicit = q.metadata.get(TOP_K_EXPLICIT_META_KEY)
    if explicit is None:
        explicit = q.top_k != _LEGACY_TOP_K_DEFAULT
    return bool(explicit)


class RAGRetrieverAdapter(RetrieverPort):
    """Adapter wrapping the ragsystem retriever implementations behind a port interface."""

    def __init__(self, rag_instance, collection_name: str = ""):
        self._rag_instance = rag_instance
        self._collection_name = collection_name

    def _build_retriever(self, q: RetrievalQuery):
        from app.adapters.ragsystem import retriever_for_nbhx
        collection = q.collection_name or self._collection_name
        return retriever_for_nbhx.retriever(
            rag_system=self._rag_instance,
            collection_name=collection,
        )

    async def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        """DB 检索（issue #37 后续：整链异步）。

        显式传 top_k(>0)：走 ``get_chunks_async`` 纯向量路径（``aretrieve``，
        不经过 query engine / 重排），``q.metadata["rerank"]`` 在该路径不生效，
        重排由调用方负责。
        未显式传 top_k：走 ``get_response_async``（``query_engine.aquery``），
        内部重排在 async 链上走 ``HTTPReranker._apostprocess_nodes``。
        """
        # OptimizedRetriever 构造/首次建索引含同步动作；放线程池，避免占事件循环
        retriever = await asyncio.to_thread(self._build_retriever, q)
        if _should_return_chunks(q):
            # 显式要 top_k 时返回纯向量 chunks（不经过内部 query engine / 重排）；
            # answer/sources 也从 chunks 派生，保证 HTTP 旧字段仍可用。
            result = await retriever.get_chunks_async(q.question, q.top_k) or {}
            chunks = result.get("chunks") or []
            return RetrievalResult(
                answer="\n".join(str(c.get("content", "")) for c in chunks),
                sources=[c.get("source", "Unknown") for c in chunks],
                metadata={"chunks": chunks},
            )

        result = await retriever.get_response_async(q.question)
        return RetrievalResult(
            answer="\n".join(result.get("content", [])),
            sources=result.get("source", []),
        )

    async def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        """Excel 表检索（issue #37 后续：整链异步）。

        显式传 top_k(>0)：与 :meth:`query_db` 一致，走 ``get_chunks_async``
        纯向量结构化 chunks（不经过内部 query engine / 重排，重排由调用方负责）。
        未显式传 top_k：保留旧行为，走 ``get_charts_async`` 整表 JSON——其中
        MinIO/Excel 同步 IO 已由 ``asyncio.to_thread`` 隔离。
        """
        retriever = await asyncio.to_thread(self._build_retriever, q)
        if _should_return_chunks(q):
            result = await retriever.get_chunks_async(q.question, q.top_k) or {}
            chunks = result.get("chunks") or []
            return RetrievalResult(
                answer="\n".join(str(c.get("content", "")) for c in chunks),
                sources=[c.get("source", "Unknown") for c in chunks],
                metadata={"chunks": chunks},
            )

        result = await retriever.get_charts_async(q.question)
        # 新内部契约：{"data": excel_to_json 结果, "sources": [源文件名]}；
        # 失败仍是 {"error": "..."}；字符串/旧 dict 形状也保留兼容分支。
        if isinstance(result, str):
            return RetrievalResult(answer=result, sources=[])
        if isinstance(result, dict) and "error" in result:
            return RetrievalResult(answer=str(result["error"]), sources=[])
        if isinstance(result, dict) and "data" in result:
            data = result["data"]
            answer = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
            sources = [s for s in (result.get("sources") or []) if s]
            return RetrievalResult(answer=answer, sources=sources)
        return RetrievalResult(
            answer=json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
            sources=[],
        )


class ChartAnalysisAdapter(ChartAnalysisPort):
    """Adapter wrapping the ragsystem chart analyzer behind a port interface."""

    async def analyze(self, data_source: Any, requirements: str) -> Any:
        from app.adapters.ragsystem import chart_analyze
        analyzer = chart_analyze.analyze()
        return await analyzer.get_response(data_source, requirements)
