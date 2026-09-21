"""issue #21 查询级缓存：归一化 / 键、结果缓存装饰器、嵌入缓存、工厂包装。

全部不依赖 Redis / 数据库 / llama-index：

- Redis 用内存假件 ``_FakeStore``，其语义刻意对齐 ``RedisKVStore``
  （写入即 JSON 序列化、读取即 JSON 解析），这样序列化 bug 也能被测出来；
- 检索端口用 ``_FakeInner``（记录调用次数，可注入异常）；
- 真实 Redis 只在本地 E2E 里验证（见 ``docs/retrieval-cache.md``）。

对应 issue #21 的四条验收标准：缓存命中省调用、写入端版本号失效、跨集合不串、
命中率指标可读。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from app.adapters import retrieval_cache as cache_mod
from app.adapters.monitoring.prometheus import RETRIEVAL_CACHE_LOOKUPS
from app.adapters.retrieval_cache import (
    LAYER_EMBEDDING,
    LAYER_RESULT,
    PAYLOAD_VERSION,
    RetrievalCache,
)
from app.adapters.retriever import (
    TOP_K_EXPLICIT_META_KEY,
    CachingRetrieverAdapter,
    HybridRetrieverAdapter,
    RAGRetrieverAdapter,
    build_retriever_port,
)
from app.core.config import settings
from app.domain.retrieval.cache_key import (
    collection_version_key,
    embedding_cache_key,
    keywords_fingerprint,
    normalize_question,
    retrieval_cache_key,
)
from app.ports.outbound.retriever import RetrievalQuery, RetrievalResult


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeStore:
    """内存版 Redis 假件：``set`` 序列化、``get`` 反序列化，并记录 TTL。"""

    def __init__(self) -> None:
        self.data: Dict[str, Any] = {}
        self.ttls: Dict[str, Optional[int]] = {}
        self.fail_get = False
        self.fail_set = False
        self.fail_incr = False

    async def get(self, key: str) -> Optional[Any]:
        if self.fail_get:
            raise RuntimeError("redis down")
        value = self.data.get(key)
        if value is None:
            return None
        if isinstance(value, (bytes, str)):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if self.fail_set:
            raise RuntimeError("redis down")
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        self.data[key] = value
        self.ttls[key] = ttl
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        if self.fail_incr:
            raise RuntimeError("redis down")
        new_value = int(self.data.get(key) or 0) + int(amount)
        self.data[key] = str(new_value)  # Redis 返回字符串
        return new_value

    async def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None


class _FakeInner:
    """``RetrieverPort`` 替身：记录调用，返回预置 chunks / 旧路径 answer。"""

    def __init__(self, chunks: Optional[List[dict]] = None, answer: str = "", error=None):
        self.chunks = list(chunks or [])
        self.answer = answer
        self.error = error
        self.db_calls: List[RetrievalQuery] = []
        self.excel_calls: List[RetrievalQuery] = []

    def _result(self) -> RetrievalResult:
        if self.chunks:
            return RetrievalResult(
                answer="\n".join(str(c.get("content", "")) for c in self.chunks),
                sources=[str(c.get("source", "Unknown")) for c in self.chunks],
                metadata={"chunks": [dict(c) for c in self.chunks]},
            )
        return RetrievalResult(answer=self.answer, sources=["a.pdf"] if self.answer else [])

    async def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        self.db_calls.append(q)
        if self.error is not None:
            raise self.error
        return self._result()

    async def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        self.excel_calls.append(q)
        if self.error is not None:
            raise self.error
        return self._result()


def _chunk(text: str = "内容", source: str = "a.pdf") -> dict:
    return {
        "content": text,
        "source": source,
        "score": 0.9,
        "metadata": {"source": source, "page": 1},
        "node_id": f"node-{text}",
    }


def _chunks_query(
    question: str = "报销标准是什么",
    collection: str = "knowledge_chunks",
    top_k: int = 10,
    keywords=(),
) -> RetrievalQuery:
    """显式 top_k 的结构化 chunks 查询（生产主路径：ragchain 固定带 top_k）。"""
    return RetrievalQuery(
        question=question,
        collection_name=collection,
        top_k=top_k,
        metadata={TOP_K_EXPLICIT_META_KEY: True, "rerank": False},
        keywords=list(keywords),
    )


def _legacy_query(question: str = "报销标准是什么", collection: str = "knowledge_chunks"):
    """不传 top_k 的旧路径查询（内部 query engine + 重排）。"""
    return RetrievalQuery(question=question, collection_name=collection)


def _port(inner, cache, **kwargs) -> CachingRetrieverAdapter:
    return CachingRetrieverAdapter(inner, cache, "knowledge_chunks", **kwargs)


def _counter(layer: str, outcome: str) -> float:
    return RETRIEVAL_CACHE_LOOKUPS.labels(layer=layer, outcome=outcome)._value.get()


# ---------------------------------------------------------------------------
# 1. 归一化与键
# ---------------------------------------------------------------------------


def test_normalize_question_collapses_unicode_whitespace():
    assert normalize_question("  你好\u3000\u3000世界 \n\t第二行  ") == "你好 世界 第二行"
    assert normalize_question("\xa0a\xa0") == "a"
    assert normalize_question(None) == ""
    assert normalize_question("   ") == ""


def test_normalize_question_does_not_fold_case():
    """大小写有语义（SAP / 项目号等实体）——折叠会让不同文本共用一条嵌入。"""
    assert normalize_question("SAP") == "SAP"
    assert normalize_question("SAP") != normalize_question("sap")


def test_normalization_collapses_runs_to_a_single_space():
    """空白折叠是「多空格 → 1 个」，不是删除：无空格写法仍是不同文本。"""
    assert normalize_question("年假  标准") == normalize_question(" 年假 标准 ") == "年假 标准"
    assert normalize_question("年假标准") != normalize_question("年假 标准")


def test_key_is_stable_across_equivalent_questions_and_keyword_orders():
    first = retrieval_cache_key(
        scope="db",
        collection="knowledge_chunks",
        path="chunks",
        question=" 报销  标准 ",
        top_k=10,
        keywords=keywords_fingerprint(["项目号", "V254"]),
        version=3,
    )
    second = retrieval_cache_key(
        scope="db",
        collection="knowledge_chunks",
        path="chunks",
        question="报销 标准",
        top_k=10,
        keywords=keywords_fingerprint(["V254", "项目号", "V254"]),
        version=3,
    )
    assert first == second


def test_key_varies_with_collection_top_k_scope_path_and_version():
    base = dict(path="chunks", question="q", keywords=(), version=0)
    keys = {
        retrieval_cache_key(scope="db", collection="c1", top_k=5, **base),
        retrieval_cache_key(scope="db", collection="c2", top_k=5, **base),
        retrieval_cache_key(scope="db", collection="c1", top_k=6, **base),
        retrieval_cache_key(scope="excel", collection="c1", top_k=5, **base),
        retrieval_cache_key(scope="db", collection="c1", top_k=5, **{**base, "path": "legacy"}),
        retrieval_cache_key(scope="db", collection="c1", top_k=5, **{**base, "version": 1}),
    }
    assert len(keys) == 6  # 六个维度各自都能区分键


def test_keywords_fingerprint_drops_empty_overlong_and_duplicates():
    fingerprint = keywords_fingerprint(["  年假 ", "", "年假", "x" * 200, "调休"])
    assert fingerprint == ("年假", "调休")


def test_embedding_key_normalizes_text():
    assert embedding_cache_key(model="bge-m3", text=" 报销  标准 ") == embedding_cache_key(
        model="bge-m3", text="报销 标准"
    )
    assert embedding_cache_key(model="bge-m3", text="q") != embedding_cache_key(
        model="other", text="q"
    )


# ---------------------------------------------------------------------------
# 2. 缓存层：嵌入语义 + 失效语义（不经过装饰器）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_embedding_roundtrip_and_ttl():
    store = _FakeStore()
    cache = RetrievalCache(store=store)

    assert await cache.get_query_embedding(" 问题 ", "bge-m3") is None  # 未命中
    assert await cache.set_query_embedding(" 问题 ", "bge-m3", [0.1, 0.2]) is True

    assert await cache.get_query_embedding("问题", "bge-m3") == [0.1, 0.2]
    key = embedding_cache_key(model="bge-m3", text="问题")
    assert store.ttls[key] == cache.ttl_sec == settings.RETRIEVAL_CACHE_TTL_SEC


@pytest.mark.asyncio
async def test_empty_text_and_zero_vector_are_not_cached():
    store = _FakeStore()
    cache = RetrievalCache(store=store)

    assert await cache.get_query_embedding("", "bge-m3") is None
    assert await cache.set_query_embedding("", "bge-m3", [1.0, 2.0]) is False
    assert await cache.set_query_embedding("  ", "bge-m3", [1.0, 2.0]) is False
    # 全零向量 = 空文本 / NaN 回退的占位，缓存它只会固化降级结果
    assert await cache.set_query_embedding("q", "bge-m3", [0.0, 0.0]) is False
    assert store.data == {}


@pytest.mark.asyncio
async def test_embedding_cache_fails_open_on_store_errors():
    store = _FakeStore()
    cache = RetrievalCache(store=store)
    store.fail_get = True
    store.fail_set = True

    assert await cache.get_query_embedding("q", "bge-m3") is None
    assert await cache.set_query_embedding("q", "bge-m3", [1.0]) is False  # 不抛异常


@pytest.mark.asyncio
async def test_collection_version_defaults_to_zero_and_bumps():
    store = _FakeStore()
    cache = RetrievalCache(store=store)

    assert await cache.collection_version("knowledge_chunks") == 0
    assert await cache.invalidate_collection("knowledge_chunks") == 1
    assert await cache.collection_version("knowledge_chunks") == 1
    assert store.data[collection_version_key("knowledge_chunks")] == "1"


@pytest.mark.asyncio
async def test_disabled_cache_is_a_noop():
    store = _FakeStore()
    cache = RetrievalCache(store=store, enabled=False, embedding_enabled=False)

    assert await cache.collection_version("c") is None
    assert await cache.invalidate_collection("c") is None
    assert cache.invalidate_collection_sync("c") is None
    assert await cache.get_result("k") is None
    assert await cache.set_result("k", {"v": PAYLOAD_VERSION, "answer": "a"}) is False
    assert await cache.get_query_embedding("q", "bge-m3") is None
    assert store.data == {}


# ---------------------------------------------------------------------------
# 3. 结果缓存装饰器
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_identical_query_is_served_from_cache():
    """验收①：第二次不再调用内层端口（= 不再付嵌入 + PG 向量查询成本）。"""
    store = _FakeStore()
    inner = _FakeInner(chunks=[_chunk("甲"), _chunk("乙")])
    port = _port(inner, RetrievalCache(store=store))
    q = _chunks_query(keywords=["年假"])

    first = await port.query_db(q)
    second = await port.query_db(q)

    assert len(inner.db_calls) == 1
    assert second.answer == first.answer
    assert second.sources == first.sources
    assert second.metadata["chunks"] == first.metadata["chunks"]


@pytest.mark.asyncio
async def test_cache_key_isolates_collections():
    """验收③：跨集合不串数据（键含 collection）。"""
    store = _FakeStore()
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, RetrievalCache(store=store))

    await port.query_db(_chunks_query(collection="collection_a"))
    await port.query_db(_chunks_query(collection="collection_b"))
    await port.query_db(_chunks_query(collection="collection_a"))

    assert len(inner.db_calls) == 2  # a 命中第二次，b 各自算一次
    result_keys = [k for k in store.data if k.startswith("retrieval:cache:")]
    assert len(result_keys) == 2
    assert all("collection_a" in k or "collection_b" in k for k in result_keys)


@pytest.mark.asyncio
async def test_top_k_keywords_and_scope_are_part_of_the_key():
    store = _FakeStore()
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, RetrievalCache(store=store), include_keywords=True)

    await port.query_db(_chunks_query(top_k=10, keywords=["年假"]))
    await port.query_db(_chunks_query(top_k=10, keywords=["年假"]))  # 命中
    await port.query_db(_chunks_query(top_k=5, keywords=["年假"]))  # 不同 top_k
    await port.query_db(_chunks_query(top_k=10, keywords=["调休"]))  # 不同关键词
    await port.query_excel(_chunks_query(top_k=10, keywords=["年假"]))  # 不同端点域

    assert len(inner.db_calls) == 3
    assert len(inner.excel_calls) == 1


@pytest.mark.asyncio
async def test_version_bump_invalidates_collection_entries():
    """验收②机制：写入端 bump 版本号后，旧条目不再命中。"""
    store = _FakeStore()
    cache = RetrievalCache(store=store)
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, cache)
    q = _chunks_query()

    await port.query_db(q)
    await port.query_db(q)
    assert len(inner.db_calls) == 1

    bumped = await cache.invalidate_collection(q.collection_name)

    await port.query_db(q)
    assert bumped == 1
    assert len(inner.db_calls) == 2  # 版本号变了 → 旧键不可达
    # 版本键不设 TTL（过期归零会让失效前的旧条目重新命中）
    assert collection_version_key(q.collection_name) not in store.ttls


@pytest.mark.asyncio
async def test_legacy_and_chunks_paths_do_not_collide():
    store = _FakeStore()
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, RetrievalCache(store=store))

    chunks_result = await port.query_db(_chunks_query())
    legacy_result = await port.query_db(_legacy_query())
    again = await port.query_db(_chunks_query())

    assert len(inner.db_calls) == 2  # chunks 与 legacy 各算一次（两条路径的键不同）
    assert legacy_result.answer == "甲"
    assert again.metadata["chunks"] == chunks_result.metadata["chunks"]
    assert len([k for k in store.data if k.startswith("retrieval:cache:")]) == 2


@pytest.mark.asyncio
async def test_empty_and_failed_results_are_not_cached():
    store = _FakeStore()
    inner = _FakeInner(chunks=[])
    port = _port(inner, RetrievalCache(store=store))

    await port.query_db(_chunks_query())
    await port.query_db(_chunks_query())
    assert len(inner.db_calls) == 2  # 空结果不缓存（网关抖动不能被固化）

    boom = _FakeInner(chunks=[_chunk("甲")], error=RuntimeError("retrieval boom"))
    port = _port(boom, RetrievalCache(store=store))
    with pytest.raises(RuntimeError, match="retrieval boom"):
        await port.query_db(_chunks_query())
    assert not [k for k in store.data if k.startswith("retrieval:cache:")]  # 异常不缓存

    boom.error = None
    await port.query_db(_chunks_query())
    assert len(boom.db_calls) == 2


@pytest.mark.asyncio
async def test_cache_errors_fail_open():
    """Redis 挂掉时行为与改造前完全一致：照常检索，只是不缓存。"""
    store = _FakeStore()
    store.fail_get = True
    store.fail_set = True
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, RetrievalCache(store=store))

    first = await port.query_db(_chunks_query())
    second = await port.query_db(_chunks_query())

    assert first.answer == second.answer
    assert len(inner.db_calls) == 2


@pytest.mark.asyncio
async def test_unavailable_version_bypasses_cache():
    class _NoVersion:
        """版本号取不到（Redis 不可用）→ 必须整体旁路，一次都不该碰缓存。"""

        enabled = True

        async def collection_version(self, collection):
            return None

        async def get_result(self, key):  # pragma: no cover - 不应被调用
            raise AssertionError("旁路时不应读缓存")

        async def set_result(self, key, payload):  # pragma: no cover
            raise AssertionError("旁路时不应写缓存")

    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, _NoVersion())
    q = _chunks_query()

    await port.query_db(q)
    await port.query_db(q)
    assert len(inner.db_calls) == 2


@pytest.mark.asyncio
async def test_missing_collection_bypasses_cache():
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = CachingRetrieverAdapter(inner, RetrievalCache(store=_FakeStore()), "")
    q = RetrievalQuery(question="q", collection_name="", top_k=10)

    await port.query_db(q)
    await port.query_db(q)
    assert len(inner.db_calls) == 2


@pytest.mark.asyncio
async def test_oversized_and_unserializable_payloads_are_skipped():
    store = _FakeStore()
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, RetrievalCache(store=store, max_payload_bytes=16))
    await port.query_db(_chunks_query())
    await port.query_db(_chunks_query())
    assert len(inner.db_calls) == 2
    assert store.data == {}

    # metadata 里有不可 JSON 化的对象（set）→ 跳过写入，不抛异常
    store = _FakeStore()
    bad = _chunk("乙")
    bad["metadata"] = {"tags": {"a", "b"}}
    inner = _FakeInner(chunks=[bad])
    port = _port(inner, RetrievalCache(store=store))
    result = await port.query_db(_chunks_query())
    await port.query_db(_chunks_query())
    assert result.metadata["chunks"][0]["content"] == "乙"
    assert len(inner.db_calls) == 2
    assert store.data == {}


@pytest.mark.asyncio
async def test_corrupt_entry_is_deleted_and_treated_as_miss():
    store = _FakeStore()
    cache = RetrievalCache(store=store)
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, cache)
    q = _chunks_query()

    await port.query_db(q)
    key = next(k for k in store.data if k.startswith("retrieval:cache:"))
    store.data[key] = json.dumps({"v": PAYLOAD_VERSION + 1, "answer": "旧形状"})

    result = await port.query_db(q)
    assert len(inner.db_calls) == 2
    assert result.answer == "甲"


@pytest.mark.asyncio
async def test_lookup_metrics_expose_hit_and_miss():
    """验收④：命中率指标可读（Prometheus 计数）。"""
    store = _FakeStore()
    cache = RetrievalCache(store=store)
    inner = _FakeInner(chunks=[_chunk("甲")])
    port = _port(inner, cache)
    hits_before = _counter(LAYER_RESULT, "hit")
    misses_before = _counter(LAYER_RESULT, "miss")
    emb_hits_before = _counter(LAYER_EMBEDDING, "hit")

    await port.query_db(_chunks_query())  # miss
    await port.query_db(_chunks_query())  # hit
    await cache.set_query_embedding("问题", "bge-m3", [1.0, 2.0])
    await cache.get_query_embedding("问题", "bge-m3")  # hit

    assert _counter(LAYER_RESULT, "hit") == hits_before + 1
    assert _counter(LAYER_RESULT, "miss") == misses_before + 1
    assert _counter(LAYER_EMBEDDING, "hit") == emb_hits_before + 1


# ---------------------------------------------------------------------------
# 4. 组合根工厂
# ---------------------------------------------------------------------------


def test_build_retriever_port_wraps_cache_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_HYBRID_ENABLED", False, raising=False)
    port = build_retriever_port(
        object(), "knowledge_chunks", cache=RetrievalCache(store=_FakeStore())
    )

    assert isinstance(port, CachingRetrieverAdapter)
    assert isinstance(port._inner, RAGRetrieverAdapter)


def test_build_retriever_port_include_keywords_follows_hybrid_flag(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_HYBRID_ENABLED", True, raising=False)
    port = build_retriever_port(
        object(), "knowledge_chunks", cache=RetrievalCache(store=_FakeStore())
    )

    assert isinstance(port, CachingRetrieverAdapter)
    assert isinstance(port._inner, HybridRetrieverAdapter)
    assert port._include_keywords is True


def test_build_retriever_port_skips_cache_when_disabled_or_none(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_HYBRID_ENABLED", False, raising=False)
    disabled = build_retriever_port(
        object(), "knowledge_chunks", cache=RetrievalCache(store=_FakeStore(), enabled=False)
    )
    bypassed = build_retriever_port(object(), "knowledge_chunks", cache=None)

    assert isinstance(disabled, RAGRetrieverAdapter)
    assert not isinstance(disabled, CachingRetrieverAdapter)
    assert isinstance(bypassed, RAGRetrieverAdapter)


def test_build_retriever_port_uses_singleton_by_default(monkeypatch):
    store = _FakeStore()
    cache_mod.set_retrieval_cache_for_tests(RetrievalCache(store=store))
    monkeypatch.setattr(settings, "RETRIEVAL_HYBRID_ENABLED", False, raising=False)

    port = build_retriever_port(object(), "knowledge_chunks")

    assert isinstance(port, CachingRetrieverAdapter)
    assert port._cache is cache_mod.get_retrieval_cache()
