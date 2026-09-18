"""主应用 retriever 增量扩展测试（子系统 A / 计划 §4.1）。

覆盖：
  1. OptimizedRetriever.get_chunks：结构化 chunks、top_k 透传与上限、异常兜底；
  2. ModelManager.get_query_engine：use_reranker=False 不挂 postprocessor，
     缓存 key 区分带/不带重排；
  3. RAGRetrieverAdapter.query_db：显式 top_k 走 get_chunks 并带 metadata["chunks"]，
     未传 top_k 的旧调用仍走 get_response；
  4. query_excel：适配 get_charts 新契约（data / sources 源文件名）；
  5. API /db、/excel：新参数透传 + 默认不传参数时旧行为不变。

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
    """替代 ragsystem.retriever(...) 的返回值，记录调用。"""

    def __init__(self):
        self.calls = []
        self.chunks_response = {"chunks": []}
        self.response = {"content": [], "source": []}
        self.charts_response = {"error": "未找到相关文件"}

    def get_chunks(self, question, top_k):
        self.calls.append(("get_chunks", question, top_k))
        return self.chunks_response

    def get_response(self, question):
        self.calls.append(("get_response", question))
        return self.response

    def get_charts(self, question):
        self.calls.append(("get_charts", question))
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


# ---------------------------------------------------------------------------
# 2. ModelManager.get_query_engine(rerank)
# ---------------------------------------------------------------------------


def _make_model_manager():
    manager = object.__new__(ModelManager)
    manager._rag_system = None
    manager._reranker = None
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
    manager.get_reranker = lambda: "reranker"
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
    manager.get_reranker = lambda: "reranker"
    captured = _patch_query_engine_builder(monkeypatch)

    manager.get_query_engine("knowledge_chunks", top_k=5)  # 旧签名调用

    assert captured[0]["node_postprocessors"] == ["reranker"]


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


def test_query_db_explicit_top_k_returns_chunks(adapter, fake_rag_retriever):
    fake_rag_retriever.chunks_response = {
        "chunks": [_chunk("命中一", "甲.pdf", 0.9), _chunk("命中二", "乙.pdf", 0.8)]
    }

    result = adapter.query_db(
        RetrievalQuery(
            question="去年售后费用？",
            collection_name="knowledge_chunks",
            top_k=3,
        )
    )

    assert fake_rag_retriever.calls == [
        ("get_chunks", "去年售后费用？", 3)
    ]
    assert result.answer == "命中一\n命中二"
    assert result.sources == ["甲.pdf", "乙.pdf"]
    assert result.metadata["chunks"] == fake_rag_retriever.chunks_response["chunks"]


def test_query_db_explicit_top_k_equal_default_is_still_chunks(adapter, fake_rag_retriever):
    """显式 top_k=10 与 dataclass 默认值相同，靠 metadata 标记区分。"""
    fake_rag_retriever.chunks_response = {"chunks": [_chunk("x")]}

    result = adapter.query_db(
        RetrievalQuery(
            question="q",
            collection_name="knowledge_chunks",
            top_k=10,
            metadata={TOP_K_EXPLICIT_META_KEY: True},
        )
    )

    assert fake_rag_retriever.calls[0][0] == "get_chunks"
    assert result.metadata["chunks"] == [_chunk("x")]


def test_query_db_explicit_top_k_rerank_flag_is_noop_for_chunks(adapter, fake_rag_retriever):
    """显式 top_k 时始终走纯向量 chunks，metadata["rerank"]=True 也不触发内部重排。"""
    fake_rag_retriever.chunks_response = {"chunks": [_chunk("x")]}

    result = adapter.query_db(
        RetrievalQuery(
            question="q",
            collection_name="knowledge_chunks",
            top_k=4,
            metadata={TOP_K_EXPLICIT_META_KEY: True, "rerank": True},
        )
    )

    assert fake_rag_retriever.calls == [("get_chunks", "q", 4)]
    assert result.metadata["chunks"] == [_chunk("x")]


def test_query_db_without_top_k_keeps_legacy_get_response(adapter, fake_rag_retriever):
    fake_rag_retriever.response = {
        "content": ["旧内容一", "旧内容二"],
        "source": ["old1.pdf", "old2.pdf"],
    }

    result = adapter.query_db(
        RetrievalQuery(question="q", collection_name="knowledge_chunks")
    )

    assert fake_rag_retriever.calls == [("get_response", "q")]
    assert result.answer == "旧内容一\n旧内容二"
    assert result.sources == ["old1.pdf", "old2.pdf"]
    assert result.metadata == {}


# ---------------------------------------------------------------------------
# 4. RAGRetrieverAdapter.query_excel + get_charts 新契约
# ---------------------------------------------------------------------------


def test_query_excel_returns_data_json_and_source_filenames(adapter, fake_rag_retriever):
    data_json = json.dumps({"sheet_name": "S", "headers": [], "rows": []}, ensure_ascii=False)
    fake_rag_retriever.charts_response = {
        "data": data_json,
        "sources": ["华翔定价表.xlsx"],
    }

    result = adapter.query_excel(
        RetrievalQuery(
            question="哪个供应商延期最多？",
            collection_name="excel_db_chunks",
            top_k=5,
        )
    )

    assert result.answer == data_json
    assert result.sources == ["华翔定价表.xlsx"]


def test_query_excel_error_keeps_old_answer_sources_shape(adapter, fake_rag_retriever):
    fake_rag_retriever.charts_response = {"error": "NoSuchKey"}

    result = adapter.query_excel(
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

        def query_db(self, q):
            captured["q"] = q
            return result

        def query_excel(self, q):
            captured["q"] = q
            return result

    monkeypatch.setattr(api_mod, "RAGRetrieverAdapter", _FakePort)
    return captured


def test_api_db_forwards_top_k_and_rerank_and_returns_chunks(monkeypatch):
    chunks = [_chunk("命中一", "甲.pdf", 0.9)]
    result = RetrievalResult(answer="命中一", sources=["甲.pdf"], metadata={"chunks": chunks})
    captured = _install_fake_api_port(monkeypatch, result)

    response = api_mod.db(
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


def test_api_db_without_new_params_keeps_legacy_query_defaults(monkeypatch):
    result = RetrievalResult(answer="旧答案", sources=["旧.pdf"])
    captured = _install_fake_api_port(monkeypatch, result)

    response = api_mod.db(
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


def test_api_db_direct_call_omitting_new_params_uses_defaults(monkeypatch):
    """直接调用路由函数（非 HTTP）时，未传参拿到的是 Query 默认对象，也要归一化。"""
    result = RetrievalResult(answer="旧答案", sources=["旧.pdf"])
    captured = _install_fake_api_port(monkeypatch, result)

    response = api_mod.db(
        ChatRequest(question="q"),
        collection="knowledge_chunks",
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].top_k == RetrievalQuery.__dataclass_fields__["top_k"].default
    assert captured["q"].metadata == {}
    assert response["chunks"] == []


def test_api_excel_returns_source_filenames_and_forwards_top_k(monkeypatch):
    result = RetrievalResult(
        answer='{"sheet_name": "S", "headers": [], "rows": []}',
        sources=["华翔定价表.xlsx"],
    )
    captured = _install_fake_api_port(monkeypatch, result)

    response = api_mod.excel(
        ChatRequest(question="哪个供应商延期最多？"),
        collection="excel_db_chunks",
        top_k=5,
        rerank=False,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].top_k == 5
    assert captured["q"].metadata == {TOP_K_EXPLICIT_META_KEY: True, "rerank": False}
    assert response == {
        "answer": '{"sheet_name": "S", "headers": [], "rows": []}',
        "sources": ["华翔定价表.xlsx"],
    }
    assert "chunks" not in response


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
    assert captured["q"].top_k == 5

    # 参数下界校验：top_k=0 -> 422
    response = client.post(
        "/api/v1/retriever/db?collection=knowledge_chunks&top_k=0",
        json={"question": "q"},
    )
    assert response.status_code == 422


def test_api_excel_without_new_params_uses_legacy_default(monkeypatch):
    result = RetrievalResult(answer="{}", sources=[])
    captured = _install_fake_api_port(monkeypatch, result)

    response = api_mod.excel(
        ChatRequest(question="q"),
        collection="excel_db_chunks",
        top_k=None,
        rerank=True,
        rag_instance=object(),
        current_user=_superuser(),
    )

    assert captured["q"].top_k == RetrievalQuery.__dataclass_fields__["top_k"].default
    assert captured["q"].metadata == {}
    assert response == {"answer": "{}", "sources": []}
