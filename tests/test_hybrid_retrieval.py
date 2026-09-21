"""issue #16 混合检索单测：关键词清洗、RRF 融合、双路适配器与 API 契约。

全部不依赖 llama-index / 数据库：domain 纯函数 + 假 port + 假 session，
因此能在 CI 的最小依赖集（无 llama-index）里真跑。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.adapters.retriever import (
    RAGRetrieverAdapter,
    HybridRetrieverAdapter,
    build_retriever_port,
    TOP_K_EXPLICIT_META_KEY,
)
from app.adapters.web.base import ChatRequest
from app.api.v1 import retriever as api_mod
from app.core.config import settings
from app.domain.retrieval.ranking import (
    PATH_DENSE,
    PATH_LEXICAL,
    dedupe_key,
    normalize_keywords,
    rrf_fuse,
)
from app.ports.contracts.identity import ROLE_SUPERUSER
from app.ports.outbound.retriever import LexicalHit, RetrievalQuery, RetrievalResult


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeDense:
    """RetrieverPort 替身：记录调用并返回预置 RetrievalResult（可抛异常）。"""

    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.db_calls = []
        self.excel_calls = []

    def _result(self):
        return RetrievalResult(
            answer="\n".join(c["content"] for c in self.chunks),
            sources=[c["source"] for c in self.chunks],
            metadata={"chunks": list(self.chunks)},
        )

    async def query_db(self, q):
        self.db_calls.append(q)
        if self.error is not None:
            raise self.error
        return self._result()

    async def query_excel(self, q):
        self.excel_calls.append(q)
        if self.error is not None:
            raise self.error
        return self._result()


class _FakeLexical:
    """LexicalSearchPort 替身。"""

    def __init__(self, hits=None, error=None):
        self.hits = list(hits or [])
        self.error = error
        self.calls = []

    async def search(self, collection, keywords, top_k):
        self.calls.append(
            {"collection": collection, "keywords": list(keywords), "top_k": top_k}
        )
        if self.error is not None:
            raise self.error
        return list(self.hits)


def _chunk(content, source="doc.pdf", score=0.9, node_id=None):
    return {
        "content": content,
        "source": source,
        "score": score,
        "metadata": {"source": source},
        "node_id": node_id,
    }


def _hit(content, node_id, source="doc.pdf", hits=1):
    return LexicalHit(
        node_id=node_id, content=content, source=source, metadata={"source": source}, hits=hits
    )


def _query(**kwargs):
    kwargs.setdefault("question", "q")
    kwargs.setdefault("collection_name", "knowledge_chunks")
    kwargs.setdefault("top_k", 10)
    kwargs.setdefault("metadata", {TOP_K_EXPLICIT_META_KEY: True})
    return RetrievalQuery(**kwargs)


def _adapter(dense, lexical, **kwargs):
    kwargs.setdefault("collection_name", "knowledge_chunks")
    return HybridRetrieverAdapter(dense=dense, lexical=lexical, **kwargs)


# ---------------------------------------------------------------------------
# 1. domain：关键词清洗
# ---------------------------------------------------------------------------


def test_normalize_keywords_strips_drops_and_dedupes():
    assert normalize_keywords(["  V254 ", "", "   ", "V254", "杨贵宁"]) == ["V254", "杨贵宁"]


def test_normalize_keywords_accepts_single_string_and_none():
    assert normalize_keywords("V254") == ["V254"]
    assert normalize_keywords(None) == []
    assert normalize_keywords([]) == []


def test_normalize_keywords_caps_count_and_length():
    assert normalize_keywords(["a", "b", "c"], max_count=2) == ["a", "b"]
    assert normalize_keywords(["x" * 10, "ok"], max_len=8) == ["ok"]


def test_normalize_keywords_coerces_non_string_items():
    assert normalize_keywords([2020, "V254"]) == ["2020", "V254"]


# ---------------------------------------------------------------------------
# 2. domain：去重键与 RRF
# ---------------------------------------------------------------------------


def test_dedupe_key_prefers_node_id_then_content_hash():
    assert dedupe_key("node-1", "任意内容") == "node-1"
    assert dedupe_key(None, "同一段文本") == dedupe_key("", "同一段文本")
    assert dedupe_key(None, "同一段文本") != dedupe_key(None, "另一段文本")
    assert dedupe_key(None, "同一段文本").startswith("sha1:")


def test_rrf_fuse_marks_paths_and_prefers_two_path_hits():
    fused = rrf_fuse({PATH_DENSE: ["a", "b", "c"], PATH_LEXICAL: ["z", "a"]}, k=60)

    by_key = {item.key: item for item in fused}
    # a 出现在两路 → 分数最高，且带两路溯源
    assert fused[0].key == "a"
    assert by_key["a"].paths == (PATH_DENSE, PATH_LEXICAL)
    assert by_key["a"].score > by_key["z"].score
    # 单路命中的 chunk 只带自己的路径
    assert by_key["z"].paths == (PATH_LEXICAL,)
    assert by_key["b"].paths == (PATH_DENSE,)


def test_rrf_fuse_is_deterministic_on_ties():
    fused_a = rrf_fuse({PATH_DENSE: ["x"], PATH_LEXICAL: ["y"]}, k=60)
    fused_b = rrf_fuse({PATH_DENSE: ["x"], PATH_LEXICAL: ["y"]}, k=60)

    assert [i.key for i in fused_a] == [i.key for i in fused_b]
    # 同分时有两路名次者优先，再按 dense 名次 → x 在前
    assert [i.key for i in fused_a] == ["x", "y"]


def test_rrf_fuse_handles_empty_paths():
    assert rrf_fuse({PATH_DENSE: [], PATH_LEXICAL: []}, k=60) == []
    assert [i.key for i in rrf_fuse({PATH_DENSE: ["a"], PATH_LEXICAL: []})] == ["a"]


# ---------------------------------------------------------------------------
# 3. 双路适配器
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_query_without_explicit_top_k_delegates_untouched():
    """未显式传 top_k（旧 query engine 路径）不融合，原样返回。"""
    dense_result = RetrievalResult(answer="旧", sources=["a.pdf"])

    class _Dense:
        async def query_db(self, q):
            return dense_result

        async def query_excel(self, q):
            return dense_result

    lexical = _FakeLexical(hits=[_hit("词面命中", "n1")])
    adapter = _adapter(_Dense(), lexical)

    result = await adapter.query_db(
        RetrievalQuery(question="q", collection_name="knowledge_chunks", top_k=10, keywords=["V254"])
    )

    assert result is dense_result
    assert lexical.calls == []


@pytest.mark.asyncio
async def test_empty_keywords_skip_lexical_entirely():
    dense = _FakeDense(chunks=[_chunk("A", node_id="n1")])
    lexical = _FakeLexical(hits=[_hit("词面命中", "n2")])
    adapter = _adapter(dense, lexical)

    result = await adapter.query_db(_query(keywords=[]))

    assert "词面命中" not in result.answer
    assert lexical.calls == []
    # 纯向量路径：chunk 形状与改造前一致（不含 retrieval 溯源）
    assert "retrieval" not in result.metadata["chunks"][0]


@pytest.mark.asyncio
async def test_fusion_interleaves_lexical_hits_and_caps_top_k():
    dense = _FakeDense(
        chunks=[
            _chunk("d1", node_id="d1"),
            _chunk("d2", node_id="d2"),
            _chunk("d3", node_id="d3"),
        ]
    )
    lexical = _FakeLexical(hits=[_hit("l1", "l1", hits=2)])
    adapter = _adapter(dense, lexical)

    result = await adapter.query_db(_query(top_k=2, keywords=["V254"]))

    chunks = result.metadata["chunks"]
    # d1(1/61) 与 l1(1/61) 同分：dense 有向量名次 → 先；再 d2(1/62)
    assert [c["content"] for c in chunks] == ["d1", "l1"]
    assert lexical.calls[0]["collection"] == "knowledge_chunks"
    assert lexical.calls[0]["keywords"] == ["V254"]
    assert lexical.calls[0]["top_k"] == 10  # lexical_top_k 默认
    assert result.sources == [c["source"] for c in chunks]
    assert result.answer == "d1\nl1"


@pytest.mark.asyncio
async def test_fusion_dedupes_same_chunk_across_paths():
    dense = _FakeDense(chunks=[_chunk("同一段", node_id="n1", score=0.77)])
    lexical = _FakeLexical(hits=[_hit("同一段", "n1", hits=3)])
    adapter = _adapter(dense, lexical)

    result = await adapter.query_db(_query(top_k=5, keywords=["V254"]))

    chunks = result.metadata["chunks"]
    assert len(chunks) == 1
    assert chunks[0]["score"] == 0.77  # dense 的分数保留
    assert chunks[0]["retrieval"]["paths"] == [PATH_DENSE, PATH_LEXICAL]
    assert chunks[0]["retrieval"]["lexical_hits"] == 3


@pytest.mark.asyncio
async def test_lexical_only_hit_has_no_vector_score_but_keeps_content():
    dense = _FakeDense(chunks=[])
    lexical = _FakeLexical(hits=[_hit("只有词面能命中", "n9", source="台账.xlsx")])
    adapter = _adapter(dense, lexical)

    result = await adapter.query_db(_query(top_k=5, keywords=["杨贵宁"]))

    chunk = result.metadata["chunks"][0]
    assert chunk["score"] is None
    assert chunk["source"] == "台账.xlsx"
    assert chunk["node_id"] == "n9"
    assert chunk["retrieval"]["paths"] == [PATH_LEXICAL]


@pytest.mark.asyncio
async def test_dense_failure_degrades_to_lexical_only():
    dense = _FakeDense(error=RuntimeError("BGE-M3 不可达"))
    lexical = _FakeLexical(hits=[_hit("词面命中", "n1")])
    adapter = _adapter(dense, lexical)

    result = await adapter.query_db(_query(keywords=["V254"]))

    assert [c["content"] for c in result.metadata["chunks"]] == ["词面命中"]


@pytest.mark.asyncio
async def test_lexical_failure_keeps_dense_results():
    dense = _FakeDense(chunks=[_chunk("d1", node_id="d1")])
    lexical = _FakeLexical(error=RuntimeError("pg_trgm 挂了"))
    adapter = _adapter(dense, lexical)

    result = await adapter.query_db(_query(keywords=["V254"]))

    chunks = result.metadata["chunks"]
    assert [c["content"] for c in chunks] == ["d1"]
    assert chunks[0]["retrieval"]["paths"] == [PATH_DENSE]


@pytest.mark.asyncio
async def test_query_excel_also_fuses():
    dense = _FakeDense(chunks=[_chunk("表格块", source="表.xlsx", node_id="e1")])
    lexical = _FakeLexical(hits=[_hit("V254 负责人：杨贵宁", "e2", source="台账.xlsx")])
    adapter = _adapter(dense, lexical)

    result = await adapter.query_excel(_query(top_k=5, keywords=["V254"]))

    assert dense.excel_calls and not dense.db_calls
    assert [c["content"] for c in result.metadata["chunks"]] == ["表格块", "V254 负责人：杨贵宁"]


@pytest.mark.asyncio
async def test_keyword_injection_shape_is_normalized_before_sql():
    dense = _FakeDense(chunks=[])
    lexical = _FakeLexical(hits=[])
    adapter = _adapter(dense, lexical, max_keywords=2, max_keyword_len=8)

    await adapter.query_db(_query(keywords=["V254", "杨贵宁", "第三个", "x" * 20]))

    assert lexical.calls[0]["keywords"] == ["V254", "杨贵宁"]


# ---------------------------------------------------------------------------
# 4. 组合根工厂
# ---------------------------------------------------------------------------


def test_build_retriever_port_returns_pure_dense_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_HYBRID_ENABLED", False, raising=False)

    # cache=None：本用例只验「dense / hybrid 的选择」，缓存包装由
    # tests/test_retrieval_cache.py 覆盖（issue #21）
    port = build_retriever_port(object(), "knowledge_chunks", cache=None)

    assert isinstance(port, RAGRetrieverAdapter)
    assert not isinstance(port, HybridRetrieverAdapter)


def test_build_retriever_port_wraps_hybrid_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "RETRIEVAL_HYBRID_ENABLED", True, raising=False)

    port = build_retriever_port(object(), "knowledge_chunks", cache=None)

    assert isinstance(port, HybridRetrieverAdapter)


# ---------------------------------------------------------------------------
# 5. API 契约（/db、/excel）
# ---------------------------------------------------------------------------


def _superuser():
    return SimpleNamespace(role=ROLE_SUPERUSER)


def _install_fake_api_port(monkeypatch, result):
    captured = {}

    class _FakePort:
        async def query_db(self, q):
            captured["q"] = q
            return result

        async def query_excel(self, q):
            captured["q"] = q
            return result

    def _factory(rag_instance, collection_name):
        captured["collection_name"] = collection_name
        return _FakePort()

    monkeypatch.setattr(api_mod, "build_retriever_port", _factory)
    return captured


@pytest.mark.asyncio
async def test_api_db_forwards_keywords_and_returns_chunks(monkeypatch):
    chunks = [_chunk("命中", node_id="n1")]
    result = RetrievalResult(answer="命中", sources=["doc.pdf"], metadata={"chunks": chunks})
    captured = _install_fake_api_port(monkeypatch, result)

    response = await api_mod.db(
        ChatRequest(question="项目 V254 负责人是谁", keywords=["V254", " "]),
        collection="knowledge_chunks",
        top_k=7,
        rerank=False,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].keywords == ["V254"]  # 空白项被过滤
    assert captured["q"].top_k == 7
    assert captured["collection_name"] == "knowledge_chunks"
    assert response == {"answer": "命中", "sources": ["doc.pdf"], "chunks": chunks}


@pytest.mark.asyncio
async def test_api_db_without_keywords_keeps_legacy_query(monkeypatch):
    """不传 keywords：RetrievalQuery 形状与改造前一致（空列表），旧路径不变。"""
    result = RetrievalResult(answer="旧答案", sources=["旧.pdf"])
    captured = _install_fake_api_port(monkeypatch, result)

    response = await api_mod.db(
        ChatRequest(question="q"),
        collection="knowledge_chunks",
        top_k=None,
        rerank=True,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].keywords == []
    assert captured["q"].metadata == {}
    assert response == {"answer": "旧答案", "sources": ["旧.pdf"], "chunks": []}


@pytest.mark.asyncio
async def test_api_excel_forwards_keywords(monkeypatch):
    result = RetrievalResult(answer="杨贵宁", sources=["台账.xlsx"], metadata={"chunks": []})
    captured = _install_fake_api_port(monkeypatch, result)

    await api_mod.excel(
        ChatRequest(question="V254 负责人", keywords=["V254"]),
        collection="excel_db_chunks",
        top_k=10,
        rerank=False,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].keywords == ["V254"]
    assert captured["collection_name"] == "excel_db_chunks"


def test_http_route_parses_keywords_from_body(monkeypatch):
    """HTTP 层真实解析 body 里的 keywords（RAG 容器调用路径）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    result = RetrievalResult(answer="命中", sources=["a.pdf"], metadata={"chunks": []})
    captured = _install_fake_api_port(monkeypatch, result)

    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/v1/retriever")
    app.dependency_overrides[api_mod.get_rag_instance] = lambda: object()
    app.dependency_overrides[api_mod.get_current_user] = _superuser
    client = TestClient(app)

    response = client.post(
        "/api/v1/retriever/db?collection=knowledge_chunks&top_k=7&rerank=false",
        json={"question": "V254 负责人", "keywords": ["V254", "杨贵宁"]},
    )

    assert response.status_code == 200
    assert captured["q"].keywords == ["V254", "杨贵宁"]
