"""主应用 retriever 增量扩展测试（子系统 A / 计划 §4.1）。

覆盖：
  1. OptimizedRetriever.get_chunks / get_chunks_async：结构化 chunks、top_k 透传与上限、
     异常兜底，以及 aretrieve 异步路径；
  2. ModelManager.get_query_engine：use_reranker=False 不挂 postprocessor，
     缓存 key 区分带/不带重排；
  3. RAGRetrieverAdapter.query_db（async）：显式 top_k 走 get_chunks_async 并带
     metadata["chunks"]，未传 top_k 的旧调用走 get_response_async（aquery）；
  4. query_excel（async）：显式 top_k 走 get_chunks_async 结构化 chunks；
     未传 top_k 走 get_charts_async 整表 JSON（data / sources 源文件名）；
  5. API /db、/excel：async 路由新参数透传 + 默认不传参数时旧行为不变；
  6. 多集合检索后处理（issue #36）：统一收池按分排序截断、部分失败降级、
     多库 fan-out 引擎参数（召回 _MULTI_RECALL_TOP_K / 重排 _MULTI_RERANK_TOP_N）、
     ModelManager 的 rerank_top_n 分实例缓存与缓存 key 隔离。

全部 monkeypatch 掉真实模型 / MinIO / DB，可在本地快速运行。
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("llama_index")

from app.adapters.ragsystem import retriever_for_nbhx  # noqa: E402
from app.adapters.ragsystem.retriever_for_nbhx import (  # noqa: E402
    ModelManager,
    OptimizedRetriever,
)
from app.adapters.retriever import (  # noqa: E402
    TOP_K_EXPLICIT_META_KEY,
    RAGRetrieverAdapter,
)
from app.api.v1 import retriever as api_mod  # noqa: E402
from app.adapters.web.base import ChatRequest  # noqa: E402
from app.ports.contracts.identity import ROLE_SUPERUSER  # noqa: E402
from app.ports.outbound.retriever import RetrievalQuery, RetrievalResult  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _FakeNodeWithScore:
    """鸭子类型：检索节点只需要 text / metadata / score。"""

    def __init__(self, text, metadata=None, score=0.5):
        self.text = text
        self.metadata = metadata if metadata is not None else {}
        self.score = score


class _FakeVectorRetriever:
    def __init__(self, nodes=None, error=None):
        self.nodes = list(nodes or [])
        self.error = error
        self.questions = []

    def retrieve(self, question):
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        return list(self.nodes)

    async def aretrieve(self, question):
        return self.retrieve(question)


class _FakeModelManager:
    def __init__(self, vector_retriever):
        self._vector_retriever = vector_retriever
        self.calls = []

    def get_retriever(self, collection_name, top_k):
        self.calls.append((collection_name, top_k))
        return self._vector_retriever


def _make_chunks_retriever(nodes=None, error=None):
    """object.__new__ 绕过 __init__（不加载模型 / DB）。"""
    r = object.__new__(OptimizedRetriever)
    r.collection_name = "knowledge_chunks"
    r.model_manager = _FakeModelManager(_FakeVectorRetriever(nodes, error=error))
    r.default_top_n = 3
    return r


class _FakeRagRetriever:
    """替代 ragsystem.retriever(...) 的返回值，记录调用（issue #37 后 adapter 只走 *_async）。"""

    def __init__(self):
        self.calls = []
        self.chunks_response = {"chunks": []}
        self.response = {"content": [], "source": []}
        self.charts_response = {"error": "未找到相关文件"}

    async def get_chunks_async(self, question, top_k):
        self.calls.append(("get_chunks_async", question, top_k))
        return self.chunks_response

    async def get_response_async(self, question):
        self.calls.append(("get_response_async", question))
        return self.response

    async def get_charts_async(self, question):
        self.calls.append(("get_charts_async", question))
        return self.charts_response


@pytest.fixture
def fake_rag_retriever(monkeypatch):
    fake = _FakeRagRetriever()
    created = []

    def _factory(**kwargs):
        created.append(kwargs)
        return fake

    monkeypatch.setattr(retriever_for_nbhx, "retriever", _factory)
    return fake


@pytest.fixture
def adapter(fake_rag_retriever):
    return RAGRetrieverAdapter(
        rag_instance=object(), collection_name="knowledge_chunks"
    )


# ---------------------------------------------------------------------------
# 1. OptimizedRetriever.get_chunks
# ---------------------------------------------------------------------------


def test_get_chunks_returns_structured_chunks_and_passes_top_k():
    nodes = [
        _FakeNodeWithScore("  命中一  ", {"source": "doc1.pdf", "page": 1}, 0.91),
        _FakeNodeWithScore("命中二", {"source": "doc2.pdf"}, 0.82),
        _FakeNodeWithScore("命中三", {"source": "doc3.pdf"}, 0.73),
    ]
    retriever = _make_chunks_retriever(nodes)

    result = retriever.get_chunks("去年售后费用趋势？", 2)

    assert len(result["chunks"]) == 2
    assert retriever.model_manager.calls == [("knowledge_chunks", 2)]
    assert retriever.model_manager._vector_retriever.questions == ["去年售后费用趋势？"]
    assert result["chunks"][0] == {
        "content": "命中一",
        "source": "doc1.pdf",
        "score": 0.91,
        "metadata": {"source": "doc1.pdf", "page": 1},
    }
    # metadata 是拷贝，调用方修改不会污染检索节点
    result["chunks"][0]["metadata"]["page"] = 99
    assert nodes[0].metadata == {"source": "doc1.pdf", "page": 1}


def test_get_chunks_without_source_falls_back_to_unknown_and_keeps_top_k_ceiling():
    nodes = [_FakeNodeWithScore(f"c{i}", {}) for i in range(5)]
    retriever = _make_chunks_retriever(nodes)

    result = retriever.get_chunks("q", 3)

    assert [c["content"] for c in result["chunks"]] == ["c0", "c1", "c2"]
    assert all(c["source"] == "Unknown" for c in result["chunks"])


def test_get_chunks_returns_empty_on_retrieval_error():
    retriever = _make_chunks_retriever(error=RuntimeError("embedding down"))
    assert retriever.get_chunks("q", 5) == {"chunks": []}


def test_get_chunks_requires_collection(monkeypatch):
    retriever = _make_chunks_retriever()
    retriever.collection_name = None
    assert retriever.get_chunks("q", 5) == {"chunks": []}


@pytest.mark.asyncio
async def test_get_chunks_async_uses_aretrieve():
    """异步 chunks 路径走 retriever.aretrieve，而不是同步 retrieve。"""
    nodes = [
        _FakeNodeWithScore("命中一", {"source": "doc1.pdf"}, 0.91),
        _FakeNodeWithScore("命中二", {"source": "doc2.pdf"}, 0.82),
    ]
    retriever = _make_chunks_retriever(nodes)

    result = await retriever.get_chunks_async("去年售后费用趋势？", 1)

    assert [c["content"] for c in result["chunks"]] == ["命中一"]
    assert retriever.model_manager._vector_retriever.questions == ["去年售后费用趋势？"]


@pytest.mark.asyncio
async def test_get_response_async_uses_aquery():
    """异步 response 路径走 query_engine.aquery（进而触发异步 reranker 钩子）。"""
    from types import SimpleNamespace

    r = object.__new__(OptimizedRetriever)
    r.collection_name = "knowledge_chunks"
    r.default_top_n = 3
    r.model_manager = None
    calls = []

    class _FakeQueryEngine:
        async def aquery(self, question):
            calls.append(question)
            return SimpleNamespace(
                source_nodes=[
                    _FakeNodeWithScore("命中", {"source": "doc.pdf"}, 0.9)
                ]
            )

    r.query_engines = _FakeQueryEngine()

    result = await r.get_response_async("q")

    assert calls == ["q"]
    assert result["content"] == ["命中"]
    assert result["source"] == ["doc.pdf"]
    assert result["metadata"] == [{"source": "doc.pdf"}]


@pytest.mark.asyncio
async def test_get_response_async_end_to_end_uses_async_reranker_http(monkeypatch):
    """端到端：真实 RetrieverQueryEngine.aquery → HTTPReranker → 异步 HTTP。

    这条用例不再只测 OptimizedRetriever 的转发，而是把真实 llama-index 引擎、
    真实 HTTPReranker 和 MockTransport 串起来，证明 async 查询链最终走的是
    ``_apostprocess_nodes``（异步 HTTP），而不是基类 ``asyncio.to_thread`` 同步路径。
    """
    import httpx
    from llama_index.core import Settings
    from llama_index.core.llms.mock import MockLLM
    from llama_index.core.query_engine import RetrieverQueryEngine
    from llama_index.core.retrievers import BaseRetriever
    from llama_index.core.schema import NodeWithScore, TextNode

    from app.adapters.ragsystem import RAGretriever as rag_module
    from app.adapters.ragsystem.RAGretriever import HTTPReranker

    class _ListRetriever(BaseRetriever):
        def __init__(self, nodes):
            super().__init__()
            self._nodes = nodes

        def _retrieve(self, query_bundle):
            return self._nodes

    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"results": [{"index": 1, "relevance_score": 0.99}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def fake_get_client():
        return client

    monkeypatch.setattr(rag_module, "get_http_client", fake_get_client)

    old_llm = Settings._llm  # 直接存私有字段，避免读属性时触发默认 OpenAI resolve
    Settings.llm = MockLLM()
    try:
        engine = RetrieverQueryEngine.from_args(
            retriever=_ListRetriever([
                NodeWithScore(node=TextNode(text="低分"), score=0.1),
                NodeWithScore(node=TextNode(text="命中"), score=0.2),
            ]),
            node_postprocessors=[
                HTTPReranker(api_url="http://rerank.test/v1/rerank", top_n=1)
            ],
            streaming=False,
        )

        retriever = object.__new__(OptimizedRetriever)
        retriever.collection_name = "knowledge_chunks"
        retriever.default_top_n = 1
        retriever.model_manager = None
        retriever.query_engines = engine

        try:
            result = await retriever.get_response_async("q")
        finally:
            await client.aclose()
    finally:
        Settings.llm = old_llm

    assert payloads and payloads[0]["documents"] == ["低分", "命中"]
    # 重排返回 index=1/top_n=1：结果只剩被重排命中的节点
    assert result["content"] == ["命中"]
    assert result["source"] == ["Unknown"]


# ---------------------------------------------------------------------------
# 2. ModelManager.get_query_engine(rerank)
# ---------------------------------------------------------------------------


def _make_model_manager():
    manager = object.__new__(ModelManager)
    manager._rag_system = None
    manager._rerankers = {}
    manager._retrievers_cache = {}
    manager._query_engines_cache = {}
    manager._cache_lock = threading.Lock()
    manager._reranker_api_url = "http://reranker.test/v1/rerank"
    return manager


def _patch_query_engine_builder(monkeypatch):
    captured = []

    class _FakeQueryEngine:
        @staticmethod
        def from_args(**kwargs):
            captured.append(kwargs)
            return object()

    monkeypatch.setattr(retriever_for_nbhx, "RetrieverQueryEngine", _FakeQueryEngine)
    return captured


def test_get_query_engine_use_reranker_false_has_no_postprocessors(monkeypatch):
    manager = _make_model_manager()
    manager.get_retriever = lambda collection_name, top_k: "retriever"
    captured = _patch_query_engine_builder(monkeypatch)

    reranker_calls = []

    def _reranker():
        reranker_calls.append("called")
        return "reranker"

    manager.get_reranker = _reranker

    manager.get_query_engine("knowledge_chunks", 5, use_reranker=False)

    assert captured[0]["node_postprocessors"] == []
    assert reranker_calls == []  # 关闭重排时不应初始化/调用 reranker


def test_get_query_engine_use_reranker_true_keeps_postprocessor_and_cache_key(monkeypatch):
    manager = _make_model_manager()
    manager.get_retriever = lambda collection_name, top_k: "retriever"
    manager.get_reranker = lambda top_n=3: "reranker"
    captured = _patch_query_engine_builder(monkeypatch)

    manager.get_query_engine("knowledge_chunks", 5, use_reranker=False)
    manager.get_query_engine("knowledge_chunks", 5, use_reranker=True)
    manager.get_query_engine("knowledge_chunks", 5, use_reranker=False)  # 命中缓存

    assert captured[0]["node_postprocessors"] == []
    assert captured[1]["node_postprocessors"] == ["reranker"]
    # 带/不带重排的引擎是不同的缓存条目，不能互相覆盖
    assert len(captured) == 2


def test_get_query_engine_legacy_call_keeps_reranker(monkeypatch):
    manager = _make_model_manager()
    manager.get_retriever = lambda collection_name, top_k: "retriever"
    manager.get_reranker = lambda top_n=3: "reranker"
    captured = _patch_query_engine_builder(monkeypatch)

    manager.get_query_engine("knowledge_chunks", top_k=5)  # 旧签名调用

    assert captured[0]["node_postprocessors"] == ["reranker"]


def test_get_query_engine_rerank_top_n_isolated_per_value(monkeypatch):
    """rerank_top_n 参与缓存 key 与重排器选择（issue #36 后续：多库用 4，单库默认 3）。

    不同 top_n 各建一个 HTTPReranker 实例、各占一条引擎缓存，互不覆盖；
    同一 top_n 二次调用命中缓存，不重复创建。
    """
    manager = _make_model_manager()
    manager.get_retriever = lambda collection_name, top_k: "retriever"
    captured = _patch_query_engine_builder(monkeypatch)

    created = []

    def _fake_http_reranker(api_url=None, top_n=5, timeout=30):
        reranker = SimpleNamespace(top_n=top_n)
        created.append(reranker)
        return reranker

    monkeypatch.setattr(retriever_for_nbhx, "HTTPReranker", _fake_http_reranker)

    engine_n4 = manager.get_query_engine("kb", 10, rerank_top_n=4)
    engine_n4_again = manager.get_query_engine("kb", 10, rerank_top_n=4)  # 命中缓存
    engine_n3 = manager.get_query_engine("kb", 5)  # 默认 rerank_top_n=3（单库路径）

    # 只创建两个重排器实例：top_n=4 与 top_n=3 各一个
    assert [r.top_n for r in created] == [4, 3]
    assert engine_n4_again is engine_n4
    assert engine_n3 is not engine_n4
    assert captured[0]["node_postprocessors"] == [created[0]]
    assert captured[1]["node_postprocessors"] == [created[1]]
    assert len(captured) == 2  # top_n=4 的二次调用没有重建引擎


# ---------------------------------------------------------------------------
# 3. RAGRetrieverAdapter.query_db
# ---------------------------------------------------------------------------


def _chunk(content, source="doc.pdf", score=0.8):
    return {
        "content": content,
        "source": source,
        "score": score,
        "metadata": {"source": source},
    }


@pytest.mark.asyncio
async def test_query_db_explicit_top_k_returns_chunks(adapter, fake_rag_retriever):
    fake_rag_retriever.chunks_response = {
        "chunks": [_chunk("命中一", "甲.pdf", 0.9), _chunk("命中二", "乙.pdf", 0.8)]
    }

    result = await adapter.query_db(
        RetrievalQuery(
            question="去年售后费用？",
            collection_name="knowledge_chunks",
            top_k=3,
        )
    )

    assert fake_rag_retriever.calls == [
        ("get_chunks_async", "去年售后费用？", 3)
    ]
    assert result.answer == "命中一\n命中二"
    assert result.sources == ["甲.pdf", "乙.pdf"]
    assert result.metadata["chunks"] == fake_rag_retriever.chunks_response["chunks"]


@pytest.mark.asyncio
async def test_query_db_explicit_top_k_equal_default_is_still_chunks(adapter, fake_rag_retriever):
    """显式 top_k=10 与 dataclass 默认值相同，靠 metadata 标记区分。"""
    fake_rag_retriever.chunks_response = {"chunks": [_chunk("x")]}

    result = await adapter.query_db(
        RetrievalQuery(
            question="q",
            collection_name="knowledge_chunks",
            top_k=10,
            metadata={TOP_K_EXPLICIT_META_KEY: True},
        )
    )

    assert fake_rag_retriever.calls[0][0] == "get_chunks_async"
    assert result.metadata["chunks"] == [_chunk("x")]


@pytest.mark.asyncio
async def test_query_db_explicit_top_k_rerank_flag_is_noop_for_chunks(adapter, fake_rag_retriever):
    """显式 top_k 时始终走纯向量 chunks，metadata["rerank"]=True 也不触发内部重排。"""
    fake_rag_retriever.chunks_response = {"chunks": [_chunk("x")]}

    result = await adapter.query_db(
        RetrievalQuery(
            question="q",
            collection_name="knowledge_chunks",
            top_k=4,
            metadata={TOP_K_EXPLICIT_META_KEY: True, "rerank": True},
        )
    )

    assert fake_rag_retriever.calls == [("get_chunks_async", "q", 4)]
    assert result.metadata["chunks"] == [_chunk("x")]


@pytest.mark.asyncio
async def test_query_db_without_top_k_keeps_legacy_get_response(adapter, fake_rag_retriever):
    fake_rag_retriever.response = {
        "content": ["旧内容一", "旧内容二"],
        "source": ["old1.pdf", "old2.pdf"],
    }

    result = await adapter.query_db(
        RetrievalQuery(question="q", collection_name="knowledge_chunks")
    )

    assert fake_rag_retriever.calls == [("get_response_async", "q")]
    assert result.answer == "旧内容一\n旧内容二"
    assert result.sources == ["old1.pdf", "old2.pdf"]
    assert result.metadata == {}


# ---------------------------------------------------------------------------
# 4. RAGRetrieverAdapter.query_excel + get_charts 新契约
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_excel_explicit_top_k_returns_chunks(adapter, fake_rag_retriever):
    """显式 top_k：/excel 与 /db 一致，返回纯向量结构化 chunks（重排交给调用方）。"""
    fake_rag_retriever.chunks_response = {
        "chunks": [_chunk("杨贵宁", "PM项目分配表_0618.xlsx", 0.91)]
    }

    result = await adapter.query_excel(
        RetrievalQuery(
            question="项目 V254 (GLC) 的负责人是谁？",
            collection_name="excel_db_chunks",
            top_k=10,
            metadata={TOP_K_EXPLICIT_META_KEY: True},
        )
    )

    assert fake_rag_retriever.calls == [("get_chunks_async", "项目 V254 (GLC) 的负责人是谁？", 10)]
    assert result.answer == "杨贵宁"
    assert result.sources == ["PM项目分配表_0618.xlsx"]
    assert result.metadata["chunks"] == fake_rag_retriever.chunks_response["chunks"]


@pytest.mark.asyncio
async def test_query_excel_without_top_k_keeps_legacy_charts_json(adapter, fake_rag_retriever):
    data_json = json.dumps({"sheet_name": "S", "headers": [], "rows": []}, ensure_ascii=False)
    fake_rag_retriever.charts_response = {
        "data": data_json,
        "sources": ["华翔定价表.xlsx"],
    }

    result = await adapter.query_excel(
        RetrievalQuery(question="哪个供应商延期最多？", collection_name="excel_db_chunks")
    )

    assert fake_rag_retriever.calls == [("get_charts_async", "哪个供应商延期最多？")]
    assert result.answer == data_json
    assert result.sources == ["华翔定价表.xlsx"]
    assert result.metadata == {}


@pytest.mark.asyncio
async def test_query_excel_error_keeps_old_answer_sources_shape(adapter, fake_rag_retriever):
    fake_rag_retriever.charts_response = {"error": "NoSuchKey"}

    result = await adapter.query_excel(
        RetrievalQuery(question="q", collection_name="excel_db_chunks")
    )

    assert result.answer == "NoSuchKey"
    assert result.sources == []


def test_get_charts_returns_data_and_only_first_successful_source(monkeypatch, tmp_path):
    retriever = object.__new__(OptimizedRetriever)
    retriever.get_response = lambda question: {
        "content": ["a", "b"],
        "source": ["甲.xlsx", "乙.xlsx"],
        "metadata": [
            {"source": "甲.xlsx", "minio_object_path": "documents/a.xlsx"},
            {"source": "乙.xlsx", "minio_object_path": "documents/b.xlsx"},
        ],
    }

    downloads = []

    def fake_save(object_name):
        downloads.append(object_name)
        path = tmp_path / "download.xlsx"
        path.write_bytes(b"xlsx")
        return path

    data_json = json.dumps({"sheet_name": "S", "headers": [], "rows": []})
    monkeypatch.setattr(retriever_for_nbhx, "save_file_from_minio", fake_save)
    monkeypatch.setattr(retriever_for_nbhx, "excel_to_json", lambda path: data_json)

    result = retriever.get_charts("价格参数？")

    # 新调用方契约：dict 语义 / 取值 / JSON 序列化 / 相等比较全部正常
    assert isinstance(result, dict)
    assert result["data"] == data_json
    assert result["sources"] == ["甲.xlsx"]
    assert result == {"data": data_json, "sources": ["甲.xlsx"]}
    assert json.loads(json.dumps(result, ensure_ascii=False))["data"] == data_json
    assert downloads == ["documents/a.xlsx"]  # 第一个成功即返回，不再尝试后面的候选
    # 兼容既有回归测试的成员判断（`'"sheet_name"' in result`）
    assert '"sheet_name"' in result


# ---------------------------------------------------------------------------
# 5. API /db、/excel
# ---------------------------------------------------------------------------


def _superuser():
    return SimpleNamespace(role=ROLE_SUPERUSER)


def _install_fake_api_port(monkeypatch, result):
    captured = {}

    class _FakePort:
        def __init__(self, rag_instance, collection_name):
            captured["rag_instance"] = rag_instance
            captured["collection_name"] = collection_name

        async def query_db(self, q):
            captured["q"] = q
            return result

        async def query_excel(self, q):
            captured["q"] = q
            return result

    monkeypatch.setattr(api_mod, "RAGRetrieverAdapter", _FakePort)
    return captured


@pytest.mark.asyncio
async def test_api_db_forwards_top_k_and_rerank_and_returns_chunks(monkeypatch):
    chunks = [_chunk("命中一", "甲.pdf", 0.9)]
    result = RetrievalResult(answer="命中一", sources=["甲.pdf"], metadata={"chunks": chunks})
    captured = _install_fake_api_port(monkeypatch, result)

    response = await api_mod.db(
        ChatRequest(question="去年售后费用？"),
        collection="knowledge_chunks",
        top_k=7,
        rerank=False,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["collection_name"] == "knowledge_chunks"
    assert captured["q"].top_k == 7
    assert captured["q"].metadata == {TOP_K_EXPLICIT_META_KEY: True, "rerank": False}
    assert response == {"answer": "命中一", "sources": ["甲.pdf"], "chunks": chunks}


@pytest.mark.asyncio
async def test_api_db_without_new_params_keeps_legacy_query_defaults(monkeypatch):
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

    # 不显式传 top_k：维持 RetrievalQuery 默认值，不设置 chunks 标记 -> 旧路径
    assert captured["q"].top_k == RetrievalQuery.__dataclass_fields__["top_k"].default
    assert captured["q"].metadata == {}
    # /db 响应新增 chunks 键，旧字段保持原样
    assert response == {"answer": "旧答案", "sources": ["旧.pdf"], "chunks": []}


@pytest.mark.asyncio
async def test_api_db_direct_call_omitting_new_params_uses_defaults(monkeypatch):
    """直接调用路由函数（非 HTTP）时，未传参拿到的是 Query 默认对象，也要归一化。"""
    result = RetrievalResult(answer="旧答案", sources=["旧.pdf"])
    captured = _install_fake_api_port(monkeypatch, result)

    response = await api_mod.db(
        ChatRequest(question="q"),
        collection="knowledge_chunks",
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].top_k == RetrievalQuery.__dataclass_fields__["top_k"].default
    assert captured["q"].metadata == {}
    assert response["chunks"] == []


@pytest.mark.asyncio
async def test_api_excel_returns_chunks_and_forwards_top_k(monkeypatch):
    chunks = [_chunk("杨贵宁", "PM项目分配表_0618.xlsx", 0.9)]
    result = RetrievalResult(
        answer="杨贵宁",
        sources=["PM项目分配表_0618.xlsx"],
        metadata={"chunks": chunks},
    )
    captured = _install_fake_api_port(monkeypatch, result)

    response = await api_mod.excel(
        ChatRequest(question="项目 V254 (GLC) 的负责人是谁？"),
        collection="excel_db_chunks",
        top_k=10,
        rerank=False,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].top_k == 10
    assert captured["q"].metadata == {TOP_K_EXPLICIT_META_KEY: True, "rerank": False}
    assert response == {
        "answer": "杨贵宁",
        "sources": ["PM项目分配表_0618.xlsx"],
        "chunks": chunks,
    }


def test_http_routes_parse_top_k_and_rerank(monkeypatch):
    """HTTP 层真实解析 Query 参数（ragchain 容器调用路径）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    result = RetrievalResult(
        answer="命中一",
        sources=["甲.pdf"],
        metadata={"chunks": [_chunk("命中一", "甲.pdf")]},
    )
    captured = _install_fake_api_port(monkeypatch, result)

    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/v1/retriever")
    app.dependency_overrides[api_mod.get_rag_instance] = lambda: object()
    app.dependency_overrides[api_mod.get_current_user] = _superuser
    client = TestClient(app)

    response = client.post(
        "/api/v1/retriever/db?collection=knowledge_chunks&top_k=7&rerank=false",
        json={"question": "去年售后费用？"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "answer": "命中一",
        "sources": ["甲.pdf"],
        "chunks": [_chunk("命中一", "甲.pdf")],
    }
    assert captured["q"].top_k == 7
    assert captured["q"].metadata == {TOP_K_EXPLICIT_META_KEY: True, "rerank": False}

    # 不传新参数：维持旧默认 (top_k=10 / rerank=True)
    response = client.post(
        "/api/v1/retriever/db?collection=knowledge_chunks",
        json={"question": "q"},
    )
    assert response.status_code == 200
    assert "chunks" in response.json()
    assert captured["q"].top_k == RetrievalQuery.__dataclass_fields__["top_k"].default
    assert captured["q"].metadata == {}

    response = client.post(
        "/api/v1/retriever/excel?collection=excel_db_chunks&top_k=5",
        json={"question": "q"},
    )
    assert response.status_code == 200
    assert "answer" in response.json()
    assert "sources" in response.json()
    assert "chunks" in response.json()
    assert captured["q"].top_k == 5

    # 参数下界校验：top_k=0 -> 422
    response = client.post(
        "/api/v1/retriever/db?collection=knowledge_chunks&top_k=0",
        json={"question": "q"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_excel_without_new_params_uses_legacy_default(monkeypatch):
    result = RetrievalResult(answer="{}", sources=[])
    captured = _install_fake_api_port(monkeypatch, result)

    response = await api_mod.excel(
        ChatRequest(question="q"),
        collection="excel_db_chunks",
        top_k=None,
        rerank=True,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].top_k == RetrievalQuery.__dataclass_fields__["top_k"].default
    assert captured["q"].metadata == {}
    # /excel 响应与 /db 对齐：新增 chunks 键，旧字段保持原样
    assert response == {"answer": "{}", "sources": [], "chunks": []}


# ---------------------------------------------------------------------------
# 6. 多集合检索后处理（issue #36：移除「每集合固定 2 条」硬编码配额）
# ---------------------------------------------------------------------------


class _FakeMultiQueryEngine:
    """多库模式替身：query/aquery 返回预设节点；error 非空时抛异常（模拟单集合故障）。"""

    def __init__(self, nodes=None, error=None):
        self._nodes = list(nodes or [])
        self._error = error
        self.questions = []

    def query(self, question):
        self.questions.append(question)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(source_nodes=list(self._nodes))

    async def aquery(self, question):
        return self.query(question)


class _FakeMultiModelManager:
    """_get_or_create_query_engine 依赖的最小 ModelManager 替身。

    只按集合名返回预置引擎；未预置的名字返回 None（模拟引擎建不出来）。
    """

    def __init__(self, engines=None):
        self._engines = dict(engines or {})

    def get_query_engine(self, collection_name, top_k=5, use_reranker=True, rerank_top_n=None):
        return self._engines.get(collection_name)


def _make_multi_retriever(engines, available=None):
    """多库模式替身：object.__new__ 绕过 __init__（不加载模型 / DB）。

    engines: {collection_name: _FakeMultiQueryEngine}；available 缺省取
    engines 的键序，可传更多名字模拟「引擎建不出来」的集合。
    """
    r = object.__new__(OptimizedRetriever)
    r.collection_name = None
    r.available_collections = (
        list(available) if available is not None else list(engines)
    )
    r.query_engines = {}
    r.default_top_n = 3
    r.model_manager = _FakeMultiModelManager(engines)
    return r


def test_multi_collection_pools_all_and_sorts_by_score():
    """核心回归：各集合召回统一收池 → 全局按分数排序 → 截断 top_k。

    三个集合各召回 3 条（分数见下），旧实现每集合固定取 2 条再按插入序
    盲截 5 条，只能拿到 [a1, a2, b1, b2, c1]；新实现允许质量最好的集合
    贡献 3 条，拿到真正的全局分数序 top5。
    """
    engines = {
        "kb_a": _FakeMultiQueryEngine([
            _FakeNodeWithScore("a1", {"source": "a1.pdf"}, 0.92),
            _FakeNodeWithScore("a2", {"source": "a2.pdf"}, 0.90),
            _FakeNodeWithScore("a3", {"source": "a3.pdf"}, 0.89),
        ]),
        "kb_b": _FakeMultiQueryEngine([
            _FakeNodeWithScore("b1", {"source": "b1.pdf"}, 0.88),
            _FakeNodeWithScore("b2", {"source": "b2.pdf"}, 0.55),
            _FakeNodeWithScore("b3", {"source": "b3.pdf"}, 0.05),
        ]),
        "kb_c": _FakeMultiQueryEngine([
            _FakeNodeWithScore("c1", {"source": "c1.pdf"}, 0.80),
            _FakeNodeWithScore("c2", {"source": "c2.pdf"}, 0.50),
            _FakeNodeWithScore("c3", {"source": "c3.pdf"}, 0.01),
        ]),
    }
    retriever = _make_multi_retriever(engines)

    result = retriever.get_response("q")

    # 全局分数序 top5：.92 > .90 > .89 > .88 > .80（kb_a 占 3 条，不再被砍到 2）
    assert result["content"] == ["a1", "a2", "a3", "b1", "c1"]
    assert result["source"] == ["a1.pdf", "a2.pdf", "a3.pdf", "b1.pdf", "c1.pdf"]
    # 9 条入池，封顶 top_k=5
    assert len(result["content"]) == 5


def test_multi_collection_response_three_columns_aligned():
    """验收项：content / source / metadata 三列长度严格对齐，metadata 不短于 content。"""
    engines = {
        "kb_a": _FakeMultiQueryEngine([
            _FakeNodeWithScore("a1", {"source": "a1.pdf", "minio_object_path": "documents/a1.pdf"}, 0.9),
            _FakeNodeWithScore("a2", {"source": "a2.pdf"}, 0.6),
        ]),
        "kb_b": _FakeMultiQueryEngine([
            _FakeNodeWithScore("b1", {"source": "b1.pdf"}, 0.7),
        ]),
    }
    retriever = _make_multi_retriever(engines)

    result = retriever.get_response("q")

    assert len(result["content"]) == len(result["source"]) == len(result["metadata"])
    for source, metadata in zip(result["source"], result["metadata"]):
        assert metadata["source"] == source
    # metadata 携带 chunk 原始字段（get_charts 解析 MinIO 路径依赖）
    assert result["metadata"][0]["minio_object_path"] == "documents/a1.pdf"


def test_multi_collection_partial_failure_only_drops_that_collection():
    """验收项（回归）：单集合查询失败只影响该集合，其余集合照常收池。

    旧/新实现都要求逐集合 try/except 降级——失败集合不产生「检索失败」
    占位内容，也不能让 get_response 走外层整体兜底。
    """
    engines = {
        "kb_ok": _FakeMultiQueryEngine([
            _FakeNodeWithScore("ok-1", {"source": "ok1.pdf"}, 0.9),
            _FakeNodeWithScore("ok-2", {"source": "ok2.pdf"}, 0.8),
        ]),
        "kb_boom": _FakeMultiQueryEngine(
            [_FakeNodeWithScore("never", {"source": "boom.pdf"}, 0.99)],
            error=RuntimeError("pg vector down"),
        ),
        "kb_ok2": _FakeMultiQueryEngine([
            _FakeNodeWithScore("ok-3", {"source": "ok3.pdf"}, 0.7),
        ]),
    }
    retriever = _make_multi_retriever(engines)

    result = retriever.get_response("q")

    assert result["content"] == ["ok-1", "ok-2", "ok-3"]
    assert result["source"] == ["ok1.pdf", "ok2.pdf", "ok3.pdf"]
    assert all(not c.startswith("检索失败") for c in result["content"])


def test_multi_collection_engine_creation_failure_is_skipped():
    """引擎建不出来（None）的集合被跳过，不影响其余集合。"""
    engines = {
        "kb_ok": _FakeMultiQueryEngine([
            _FakeNodeWithScore("ok-1", {"source": "ok1.pdf"}, 0.9),
        ]),
    }
    retriever = _make_multi_retriever(engines, available=["kb_ghost", "kb_ok"])

    result = retriever.get_response("q")

    assert result["content"] == ["ok-1"]
    assert result["source"] == ["ok1.pdf"]


def test_multi_collection_all_collections_failed_returns_empty_not_error():
    """全部集合失败：返回空列表（阈值未引入，空结果语义与旧实现一致，等 #18）。"""
    engines = {"kb_boom": _FakeMultiQueryEngine(error=RuntimeError("down"))}
    # kb_ghost 不预置引擎 -> get_query_engine 返回 None（建不出来）
    retriever = _make_multi_retriever(engines, available=["kb_boom", "kb_ghost"])

    result = retriever.get_response("q")

    assert result["content"] == []
    assert result["source"] == []
    assert result["metadata"] == []


def test_multi_collection_respects_max_collections_cap():
    """max_collections 仍限制参与查询的集合数（本 issue 不动这个行为）。"""
    engines = {
        "kb_a": _FakeMultiQueryEngine([_FakeNodeWithScore("a", {"source": "a.pdf"}, 0.9)]),
        "kb_b": _FakeMultiQueryEngine([_FakeNodeWithScore("b", {"source": "b.pdf"}, 0.8)]),
        "kb_c": _FakeMultiQueryEngine([_FakeNodeWithScore("c", {"source": "c.pdf"}, 0.7)]),
    }
    retriever = _make_multi_retriever(engines)

    result = retriever.get_response("q", max_collections=2)

    assert result["content"] == ["a", "b"]
    assert engines["kb_c"].questions == []


def test_multi_collection_none_scores_sort_last_and_ties_keep_order():
    """score=None 的节点排最后；分数并列的节点保持收集顺序（sort 稳定）。"""
    engines = {
        "kb_a": _FakeMultiQueryEngine([
            _FakeNodeWithScore("a-tie", {"source": "a1.pdf"}, 0.5),
            _FakeNodeWithScore("a-noscore", {"source": "a2.pdf"}, None),
        ]),
        "kb_b": _FakeMultiQueryEngine([
            _FakeNodeWithScore("b-tie", {"source": "b1.pdf"}, 0.5),
        ]),
    }
    retriever = _make_multi_retriever(engines)

    result = retriever.get_response("q")

    assert result["content"] == ["a-tie", "b-tie", "a-noscore"]


@pytest.mark.asyncio
async def test_multi_collection_async_pools_all_and_sorts_by_score():
    """异步链路与同步一致：统一收池 → 全局分数序 → 截断 top_k。"""
    engines = {
        "kb_a": _FakeMultiQueryEngine([
            _FakeNodeWithScore("a1", {"source": "a1.pdf"}, 0.92),
            _FakeNodeWithScore("a2", {"source": "a2.pdf"}, 0.90),
            _FakeNodeWithScore("a3", {"source": "a3.pdf"}, 0.89),
        ]),
        "kb_b": _FakeMultiQueryEngine([
            _FakeNodeWithScore("b1", {"source": "b1.pdf"}, 0.88),
            _FakeNodeWithScore("b2", {"source": "b2.pdf"}, 0.55),
        ]),
        "kb_c": _FakeMultiQueryEngine([
            _FakeNodeWithScore("c1", {"source": "c1.pdf"}, 0.80),
            _FakeNodeWithScore("c2", {"source": "c2.pdf"}, 0.50),
        ]),
    }
    retriever = _make_multi_retriever(engines)

    result = await retriever.get_response_async("q")

    assert result["content"] == ["a1", "a2", "a3", "b1", "c1"]
    assert len(result["source"]) == len(result["metadata"]) == 5


@pytest.mark.asyncio
async def test_multi_collection_async_partial_failure_only_drops_that_collection():
    """异步链路同样的逐集合降级：失败集合不影响其余集合。"""
    engines = {
        "kb_ok": _FakeMultiQueryEngine([
            _FakeNodeWithScore("ok-1", {"source": "ok1.pdf"}, 0.9),
        ]),
        "kb_boom": _FakeMultiQueryEngine(error=RuntimeError("reranker 502")),
    }
    retriever = _make_multi_retriever(engines)

    result = await retriever.get_response_async("q")

    assert result["content"] == ["ok-1"]
    assert all(not c.startswith("检索失败") for c in result["content"])


@pytest.mark.asyncio
async def test_multi_collection_async_without_aquery_falls_back_to_thread():
    """引擎没有 aquery 时走线程池兜底（_aquery_engine 既有语义不被本 issue 破坏）。"""

    class _SyncOnlyEngine:
        def __init__(self):
            self.questions = []

        def query(self, question):
            self.questions.append(question)
            return SimpleNamespace(
                source_nodes=[_FakeNodeWithScore("sync-hit", {"source": "s.pdf"}, 0.9)]
            )

    engine = _SyncOnlyEngine()
    retriever = _make_multi_retriever({"kb_sync": engine})

    result = await retriever.get_response_async("q")

    assert engine.questions == ["q"]
    assert result["content"] == ["sync-hit"]


def test_multi_collection_engine_created_with_recall10_rerank4():
    """多库 fan-out 引擎参数：每集合召回 10、重排保留 4（issue #36 后续调整）。

    旧值 top_k=3 / 重排 top_n=3 会让统一收池没料可选（旧配额还再砍到 2）。
    """
    captured = {}

    class _RecordingManager:
        def get_query_engine(self, collection_name, top_k=5, use_reranker=True, rerank_top_n=3):
            captured["args"] = (collection_name, top_k, rerank_top_n)
            return object()

    r = object.__new__(OptimizedRetriever)
    r.collection_name = None
    r.available_collections = ["kb_a"]
    r.query_engines = {}
    r.default_top_n = 3
    r.model_manager = _RecordingManager()

    engine = r._get_or_create_query_engine("kb_a")

    assert captured["args"] == ("kb_a", 10, 4)
    assert r.query_engines["kb_a"] is engine  # 建好后进缓存，下次直接命中
    # 二次调用不再请求 model_manager
    r._get_or_create_query_engine("kb_a")
    assert captured["args"] == ("kb_a", 10, 4)  # 记录未被覆盖，说明走了缓存
