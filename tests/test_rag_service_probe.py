"""RAG 模型服务启动探活回归测试。

背景：BGE-M3 / Reranker 适配器构造时只登记 URL、不校验连通性，
历史上启动日志打「初始化完成」具有误导性（地址错了照样启动成功，
错误被推迟到首次 RAG 调用才暴露，reranker 甚至静默降级）。

probe_services() 用单次最小请求真实探活；本文件用 httpx.MockTransport
覆盖：全部可达 / 嵌入服务不可达（不影响 reranker）/ 200 但结构不符 /
HTTP 404（端口有东西但路径错）/ 探活必须单次不重试。

注（issue #35）：嵌入客户端已收敛到 llama-index OpenAIEmbedding（openai SDK），
注入点不再是模块里的 ``embedding_store.get_http_client``，而是
``HttpClientManager.get_instance()``——``BGEM3EmbeddingWrapper`` 构造时从这里取
共享 async client 交给 SDK。reranker 仍走模块级 ``get_http_client``。
异常类型也随之换了一套：嵌入侧是 openai SDK 的 ``APIConnectionError`` /
``NotFoundError`` / ``ValueError``（响应里没有 data），reranker 侧仍是 httpx 的
``ConnectError`` / ``HTTPStatusError`` / ``ValueError``。
"""

from __future__ import annotations

import pytest

# 重依赖（llama_index）缺失时跳过，CI 最小依赖集下其余测试仍可跑
pytest.importorskip("llama_index")

import httpx
import pytest_asyncio

from app.adapters.doc_processing import embedding_store
from app.adapters.ragsystem import RAGretriever

_EMBEDDING_URL = "http://embedding.test/v1/embeddings"
_RERANKER_URL = "http://reranker.test/v1/rerank"


def _make_rag_system() -> RAGretriever.RAGRetrieverSystem:
    """构造 RAG 检索系统；async engine 惰性建连，不会真连 5432。"""
    return RAGretriever.RAGRetrieverSystem(
        POSTGRES_SERVER="localhost",
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p",
        POSTGRES_DB="d",
        POSTGRES_PORT=5432,
        bge_m3_api_url=_EMBEDDING_URL,
        reranker_api_url=_RERANKER_URL,
    )


@pytest_asyncio.fixture
async def fake_http(monkeypatch):
    """把两个适配器的 HTTP 出口换成 MockTransport 客户端。"""
    clients: list[httpx.AsyncClient] = []

    def _install(handler) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)

        async def _get_client() -> httpx.AsyncClient:
            return client

        # 嵌入侧：openai SDK 用的就是 wrapper 构造时注入的共享 async client
        monkeypatch.setattr(
            embedding_store.HttpClientManager, "get_instance", classmethod(lambda cls: client)
        )
        # reranker 侧：仍是模块级 get_http_client
        monkeypatch.setattr(RAGretriever, "get_http_client", _get_client)

    yield _install

    for client in clients:
        await client.aclose()


def _ok_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "embedding.test":
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 8}]})
    return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})


@pytest.mark.asyncio
async def test_probe_services_reports_ok_when_both_alive(fake_http):
    fake_http(_ok_handler)
    rag = _make_rag_system()

    results = await rag.probe_services()

    by_name = {r["name"]: r for r in results}
    assert set(by_name) == {"BGE-M3 嵌入服务", "Reranker 重排服务"}
    assert all(r["ok"] is True for r in results)
    assert all(r["error"] is None for r in results)
    assert by_name["BGE-M3 嵌入服务"]["api_url"] == _EMBEDDING_URL
    assert by_name["Reranker 重排服务"]["api_url"] == _RERANKER_URL


@pytest.mark.asyncio
async def test_probe_services_embedding_down_does_not_affect_reranker(fake_http):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "embedding.test":
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    fake_http(handler)
    rag = _make_rag_system()

    results = await rag.probe_services()

    by_name = {r["name"]: r for r in results}
    assert by_name["BGE-M3 嵌入服务"]["ok"] is False
    # 嵌入侧是 openai SDK 的异常（不再手写 httpx 传输）
    assert "APIConnectionError" in by_name["BGE-M3 嵌入服务"]["error"]
    # 一个服务挂了不能拖垮另一个的探活结果
    assert by_name["Reranker 重排服务"]["ok"] is True


@pytest.mark.asyncio
async def test_probe_services_rejects_wrong_payload_shape(fake_http):
    # 200 但响应结构不符：embedding 缺 data、rerank 缺 results/rankings
    fake_http(lambda request: httpx.Response(200, json={"unexpected": True}))
    rag = _make_rag_system()

    results = await rag.probe_services()

    by_name = {r["name"]: r for r in results}
    assert all(r["ok"] is False for r in results)
    # 嵌入侧：openai SDK 的响应校验（data 为空 → ValueError）；reranker：手写解析
    assert "ValueError" in by_name["BGE-M3 嵌入服务"]["error"]
    assert "ValueError" in by_name["Reranker 重排服务"]["error"]


@pytest.mark.asyncio
async def test_probe_services_reports_http_error_status(fake_http):
    # 端口有服务但路径/地址错误（如错指到 nginx）→ 404 也必须在启动阶段暴露
    fake_http(lambda request: httpx.Response(404, text="not found"))
    rag = _make_rag_system()

    results = await rag.probe_services()

    by_name = {r["name"]: r for r in results}
    assert all(r["ok"] is False for r in results)
    # 嵌入侧：openai SDK 按状态码映射异常类型（NotFoundError = 404），
    # 非 JSON 错误体的 message 就是 body 文本，URL 由 probe_services 的日志另外带上
    assert "NotFoundError" in by_name["BGE-M3 嵌入服务"]["error"]
    assert "HTTPStatusError" in by_name["Reranker 重排服务"]["error"]
    assert "404" in by_name["Reranker 重排服务"]["error"]


@pytest.mark.asyncio
async def test_probe_is_single_attempt_no_retry(fake_http):
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        raise httpx.ConnectError("connection refused")

    fake_http(handler)
    rag = _make_rag_system()

    results = await rag.probe_services()

    # 探活必须单次命中：不能套用正式调用链里的 tenacity 3 次重试（启动会卡 9s+）
    assert calls.count("embedding.test") == 1
    assert calls.count("reranker.test") == 1
    assert all(r["ok"] is False for r in results)
