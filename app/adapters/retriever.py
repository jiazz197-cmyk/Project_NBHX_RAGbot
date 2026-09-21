"""RAG retriever adapter wrapping ragsystem implementations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.adapters.retrieval_cache import PAYLOAD_VERSION, get_retrieval_cache
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.retrieval.cache_key import (
    PATH_CHUNKS,
    PATH_LEGACY,
    keywords_fingerprint,
    retrieval_cache_key,
)
from app.domain.retrieval.ranking import (
    PATH_DENSE,
    PATH_LEXICAL,
    dedupe_key,
    normalize_keywords,
    rrf_fuse,
)
from app.ports.outbound.retriever import (
    ChartAnalysisPort,
    LexicalSearchPort,
    RetrievalQuery,
    RetrievalResult,
    RetrieverPort,
)

logger = get_logger("adapters.retriever")

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


# ---------------------------------------------------------------------------
# issue #16 路线 1：双路召回 + RRF 融合
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    """融合前的统一候选（dense chunk 与 lexical hit 归一化后的形状）。"""

    key: str
    content: str
    source: str
    score: Optional[float]
    metadata: Dict[str, Any]
    node_id: Optional[str]
    # 仅字面路有：命中的关键词个数（溯源用）
    hits: int = 0


def _chunks_to_candidates(result: Any) -> List[_Candidate]:
    """dense 路结果（RetrievalResult.metadata["chunks"]）→ 候选列表。"""
    if not isinstance(result, RetrievalResult):
        return []
    candidates: List[_Candidate] = []
    for chunk in result.metadata.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        content = str(chunk.get("content") or "").strip()
        if not content:
            continue
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        node_id = chunk.get("node_id")
        candidates.append(
            _Candidate(
                key=dedupe_key(node_id if isinstance(node_id, str) else None, content),
                content=content,
                source=str(chunk.get("source") or metadata.get("source") or "Unknown"),
                score=chunk.get("score"),
                metadata=dict(metadata),
                node_id=node_id if isinstance(node_id, str) and node_id else None,
            )
        )
    return candidates


def _hits_to_candidates(hits: Any) -> List[_Candidate]:
    """lexical 路结果（LexicalHit 列表）→ 候选列表。"""
    candidates: List[_Candidate] = []
    for hit in hits or []:
        content = str(getattr(hit, "content", "") or "").strip()
        if not content:
            continue
        node_id = getattr(hit, "node_id", "") or None
        candidates.append(
            _Candidate(
                key=dedupe_key(node_id, content),
                content=content,
                source=str(getattr(hit, "source", "") or "Unknown"),
                # 字面路没有向量相似度：score 留 None，顺序由 RRF 名次决定
                score=None,
                metadata=dict(getattr(hit, "metadata", None) or {}),
                node_id=node_id,
                hits=int(getattr(hit, "hits", 0) or 0),
            )
        )
    return candidates


class HybridRetrieverAdapter(RetrieverPort):
    """dense ∪ lexical 双路召回 + RRF 融合（issue #16）。

    行为边界（承重，别顺手改）：

    - **只增强 chunks 路径**：未显式传 top_k 的旧调用（内部 query engine +
      重排）原样 delegate，不融合；
    - **keywords 为空即纯向量**：老调用方（含未升级的 ragchain）不传 keywords
      → 与改造前逐字段一致；
    - **稀疏路失败不降级主路**：lexical 抛异常只记 warning，dense 结果照常返回；
      dense 失败时返回 lexical-only（优于改造前的空结果）；
    - **候选池仍封顶 top_k**：融合只改变池子成分，不增加 reranker / prompt 成本。
    """

    def __init__(
        self,
        dense: RetrieverPort,
        lexical: LexicalSearchPort,
        collection_name: str = "",
        *,
        rrf_k: int = 60,
        lexical_top_k: int = 10,
        max_keywords: int = 12,
        max_keyword_len: int = 64,
    ) -> None:
        self._dense = dense
        self._lexical = lexical
        self._collection_name = collection_name
        self._rrf_k = rrf_k
        self._lexical_top_k = lexical_top_k
        self._max_keywords = max_keywords
        self._max_keyword_len = max_keyword_len

    async def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        return await self._query(q, dense_call=self._dense.query_db)

    async def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        return await self._query(q, dense_call=self._dense.query_excel)

    async def _query(self, q: RetrievalQuery, dense_call) -> RetrievalResult:
        if not _should_return_chunks(q):
            return await dense_call(q)

        keywords = normalize_keywords(
            q.keywords, max_count=self._max_keywords, max_len=self._max_keyword_len
        )
        if not keywords:
            # 旧调用（无关键词）：完全走 dense，行为与改造前一致
            return await dense_call(q)

        collection = q.collection_name or self._collection_name
        dense_outcome, lexical_outcome = await asyncio.gather(
            dense_call(q),
            self._lexical.search(collection, keywords, self._lexical_top_k),
            return_exceptions=True,
        )

        if isinstance(dense_outcome, BaseException):
            logger.warning("dense 检索失败，融合退化为字面路: %s", dense_outcome)
            dense_candidates: List[_Candidate] = []
        else:
            dense_candidates = _chunks_to_candidates(dense_outcome)

        if isinstance(lexical_outcome, BaseException):
            logger.warning("字面检索失败，融合退化为向量路: %s", lexical_outcome)
            lexical_candidates: List[_Candidate] = []
        else:
            lexical_candidates = _hits_to_candidates(lexical_outcome)

        return self._fuse(dense_candidates, lexical_candidates, q, keywords)

    def _fuse(
        self,
        dense_candidates: List[_Candidate],
        lexical_candidates: List[_Candidate],
        q: RetrievalQuery,
        keywords: List[str],
    ) -> RetrievalResult:
        by_key: Dict[str, _Candidate] = {}
        for candidate in dense_candidates:
            by_key.setdefault(candidate.key, candidate)
        for candidate in lexical_candidates:
            by_key.setdefault(candidate.key, candidate)

        # lexical 命中词数（两路命中同一 chunk 时也要保留，用于溯源展示）
        lexical_hits: Dict[str, int] = {}
        for candidate in lexical_candidates:
            lexical_hits[candidate.key] = max(
                lexical_hits.get(candidate.key, 0), candidate.hits
            )

        fused = rrf_fuse(
            {
                PATH_DENSE: [c.key for c in dense_candidates],
                PATH_LEXICAL: [c.key for c in lexical_candidates],
            },
            k=self._rrf_k,
        )

        limit = int(q.top_k) if q.top_k and q.top_k > 0 else len(fused)
        chunks: List[Dict[str, Any]] = []
        for item in fused[:limit]:
            candidate = by_key.get(item.key)
            if candidate is None:
                continue
            chunk: Dict[str, Any] = {
                "content": candidate.content,
                "source": candidate.source,
                "score": candidate.score,
                "metadata": candidate.metadata,
                "node_id": candidate.node_id,
                "retrieval": {
                    "paths": list(item.paths),
                    "rrf": round(item.score, 8),
                    "lexical_hits": lexical_hits.get(item.key),
                },
            }
            chunks.append(chunk)

        logger.debug(
            "混合检索融合: dense=%d lexical=%d keywords=%d → %d 条",
            len(dense_candidates),
            len(lexical_candidates),
            len(keywords),
            len(chunks),
        )
        return RetrievalResult(
            answer="\n".join(str(c["content"]) for c in chunks),
            sources=[c["source"] for c in chunks],
            metadata={"chunks": chunks},
        )


# ---------------------------------------------------------------------------
# issue #21：检索结果缓存装饰器
# ---------------------------------------------------------------------------

_AUTO_CACHE = object()
"""``build_retriever_port(cache=...)`` 默认值：按配置自动取缓存单例。"""


def _result_from_payload(payload: Dict[str, Any]) -> Optional[RetrievalResult]:
    """缓存载荷 → :class:`RetrievalResult`；形状不符返回 ``None``（按未命中处理）。"""
    answer = payload.get("answer")
    if not isinstance(answer, str):
        return None
    sources = payload.get("sources")
    metadata = payload.get("metadata")
    return RetrievalResult(
        answer=answer,
        sources=list(sources) if isinstance(sources, list) else [],
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


class CachingRetrieverAdapter(RetrieverPort):
    """结果级缓存装饰器：包在 dense / hybrid 端口的最外层（issue #21）。

    行为边界（承重，别顺手改）：

    - **键** = 端点域（``db`` / ``excel``）+ collection + 路径（chunks / legacy）
      + ``top_k`` + 归一化问题 + 关键词集合 + **集合版本号**。知识库写入会让
      版本号自增，旧键自然不再命中（不需要写路径 SCAN 删键）。
    - **只缓存成功的非空结果**：空 chunks / 空 answer 不写缓存（否则一次网关
      抖动会被固化成数分钟的「空答案」）；内层异常照原样抛出，不缓存错误。
    - **``metadata["rerank"]`` 不进键**：chunks 路径不消费它（见
      :func:`_should_return_chunks` 的说明）。若将来 chunks 路径开始消费 rerank，
      必须把它加进键并补用例。
    - **拿不到版本号即旁路**：缓存不可用（Redis 挂）时行为与改造前完全一致。
    """

    def __init__(
        self,
        inner: RetrieverPort,
        cache: Any,
        collection_name: str = "",
        *,
        include_keywords: bool = False,
        max_keywords: Optional[int] = None,
        max_keyword_len: Optional[int] = None,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._collection_name = collection_name
        self._include_keywords = include_keywords
        self._max_keywords = (
            settings.RETRIEVAL_MAX_KEYWORDS if max_keywords is None else max_keywords
        )
        self._max_keyword_len = (
            settings.RETRIEVAL_KEYWORD_MAX_LEN if max_keyword_len is None else max_keyword_len
        )

    async def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        return await self._cached(q, "db", self._inner.query_db)

    async def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        return await self._cached(q, "excel", self._inner.query_excel)

    async def _cached(
        self, q: RetrievalQuery, scope: str, call: Callable[[RetrievalQuery], Any]
    ) -> RetrievalResult:
        collection = q.collection_name or self._collection_name
        if not collection:
            # 没有集合名就无法做隔离（缓存键必须含 collection），直接放行
            return await call(q)

        version = await self._cache.collection_version(collection)
        if version is None:
            return await call(q)

        chunks_path = _should_return_chunks(q)
        path = PATH_CHUNKS if chunks_path else PATH_LEGACY
        keywords: Tuple[str, ...] = ()
        if self._include_keywords:
            keywords = keywords_fingerprint(
                q.keywords, max_count=self._max_keywords, max_len=self._max_keyword_len
            )
        key = retrieval_cache_key(
            scope=scope,
            collection=collection,
            path=path,
            question=q.question,
            top_k=q.top_k if chunks_path else None,
            keywords=keywords,
            version=version,
        )

        cached = await self._cache.get_result(key)
        if cached is not None:
            result = _result_from_payload(cached)
            if result is not None:
                logger.info(
                    "检索缓存命中: scope=%s collection=%s path=%s", scope, collection, path
                )
                return result

        result = await call(q)
        if self._is_cacheable(result, chunks_path):
            await self._cache.set_result(
                key,
                {
                    "v": PAYLOAD_VERSION,
                    "answer": result.answer,
                    "sources": result.sources,
                    "metadata": result.metadata,
                },
            )
        return result

    @staticmethod
    def _is_cacheable(result: Any, chunks_path: bool) -> bool:
        """只有成功的非空结果进缓存（空结果按未命中处理，下请求会重算）。"""
        if not isinstance(result, RetrievalResult):
            return False
        if chunks_path:
            return bool(result.metadata.get("chunks"))
        return bool(result.answer)


def build_retriever_port(
    rag_instance, collection_name: str, *, cache: Any = _AUTO_CACHE
) -> RetrieverPort:
    """组合根工厂：决定「纯向量 / 双路融合」并（issue #21）按配置包结果缓存。

    - ``RETRIEVAL_HYBRID_ENABLED`` 关闭（默认）时内层与改造前完全一致；打开后
      仅在调用方传了非空 keywords 时走融合，未传 keywords 的调用行为不变。
    - ``cache=_AUTO_CACHE``（默认）：按 ``RETRIEVAL_CACHE_ENABLED`` 自动取缓存
      单例；显式传 ``None``：不包缓存（测试 / 需要旁路时用）。
    """
    dense = RAGRetrieverAdapter(rag_instance=rag_instance, collection_name=collection_name)
    hybrid_enabled = bool(getattr(settings, "RETRIEVAL_HYBRID_ENABLED", False))
    if not hybrid_enabled:
        port: RetrieverPort = dense
    else:
        from app.adapters.knowledge.lexical_search import PostgresLexicalSearcher

        port = HybridRetrieverAdapter(
            dense=dense,
            lexical=PostgresLexicalSearcher(
                max_keywords=settings.RETRIEVAL_MAX_KEYWORDS,
                max_keyword_len=settings.RETRIEVAL_KEYWORD_MAX_LEN,
            ),
            collection_name=collection_name,
            rrf_k=settings.RETRIEVAL_RRF_K,
            lexical_top_k=settings.RETRIEVAL_LEXICAL_TOP_K,
            max_keywords=settings.RETRIEVAL_MAX_KEYWORDS,
            max_keyword_len=settings.RETRIEVAL_KEYWORD_MAX_LEN,
        )

    resolved = get_retrieval_cache() if cache is _AUTO_CACHE else cache
    if resolved is None or not getattr(resolved, "enabled", False):
        return port

    return CachingRetrieverAdapter(
        inner=port,
        cache=resolved,
        collection_name=collection_name,
        # 关键词只在稀疏路真的消费它时才进键（否则只会平白拆散缓存条目）
        include_keywords=hybrid_enabled,
        max_keywords=settings.RETRIEVAL_MAX_KEYWORDS,
        max_keyword_len=settings.RETRIEVAL_KEYWORD_MAX_LEN,
    )
