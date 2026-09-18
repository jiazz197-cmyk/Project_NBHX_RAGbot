"""Backend / Retriever / Reranker / Search HTTP 客户端测试（全部 MockTransport）。"""

from __future__ import annotations

import json
import urllib.parse

import httpx
import pytest

from app.clients.backend_client import BackendClient
from app.clients.reranker_client import RerankerClient
from app.clients.retriever_client import RetrieverClient
from app.clients.search_client import SearchClient, SearchResult
from app.errors import BackendError

BASE = "http://backend.local/api/v1"


def _json_response(request: httpx.Request, status: int, payload) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


# --------------------------------------------------------------------- backend
async def test_backend_create_conversation_url_body_and_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content.decode())
        return _json_response(request, 201, {"id": "c-1", "name": captured["body"]["name"]})

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        body = await client.create_conversation("tok-1", "会话名", {"search_mode": "本地检索"})
    finally:
        await client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == f"{BASE}/conversations"
    assert captured["auth"] == "Bearer tok-1"
    assert captured["body"] == {"name": "会话名", "inputs": {"search_mode": "本地检索"}}
    assert body["id"] == "c-1"


async def test_backend_get_messages_params_and_data():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        captured["auth"] = request.headers.get("authorization")
        return _json_response(request, 200, {"data": [{"id": "m1", "role": "user"}]})

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        data = await client.get_messages("tok", "c-1", page=2, limit=5)
    finally:
        await client.aclose()

    assert captured["url"].split("?")[0] == f"{BASE}/messages"
    assert captured["params"] == {"conversation_id": "c-1", "page": "2", "limit": "5"}
    assert captured["auth"] == "Bearer tok"
    assert data == [{"id": "m1", "role": "user"}]


async def test_backend_append_messages_path_and_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return _json_response(request, 201, {"stored": 2})

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        body = await client.append_messages("tok", "c/1", [{"role": "user"}])
    finally:
        await client.aclose()

    assert captured["url"] == f"{BASE}/conversations/c%2F1/messages"
    assert captured["body"] == {"messages": [{"role": "user"}]}
    assert body["stored"] == 2


async def test_backend_latest_summary_variants():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/u-404"):
            return _json_response(request, 404, {"detail": "not found"})
        if path.endswith("/u-none"):
            return _json_response(request, 200, {"data": {"exists": False, "latest_summary": None}})
        return _json_response(
            request, 200, {"data": {"exists": True, "latest_summary": " 用户关注售后费用 "}}
        )

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        assert await client.get_latest_summary("tok", "u-1") == "用户关注售后费用"
        assert await client.get_latest_summary("tok", "u-none") is None
        assert await client.get_latest_summary("tok", "u-404") is None
    finally:
        await client.aclose()


async def test_backend_compress_context_payload_and_result():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return _json_response(
            request, 200, {"data": {"compressed_context": "压缩后的上下文"}}
        )

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        result = await client.compress_context("tok", "u-1", "c-1", n_recent=3)
    finally:
        await client.aclose()

    assert captured["url"] == f"{BASE}/context-compression/compress"
    assert captured["body"] == {"user_id": "u-1", "conversation_id": "c-1", "n_recent": 3}
    assert result == "压缩后的上下文"


async def test_backend_error_mapping_json_and_default():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("conversation_id") == "permission":
            return _json_response(
                request, 403, {"message": "无权访问", "error_code": "PERMISSION_DENIED"}
            )
        return httpx.Response(500, text="boom", request=request)

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendError) as e1:
            await client.get_messages("tok", "permission")
        assert e1.value.code == "PERMISSION_DENIED"
        assert e1.value.status == 403
        assert e1.value.message == "无权访问"

        with pytest.raises(BackendError) as e2:
            await client.get_messages("tok", "other")
        assert e2.value.code == "BACKEND_ERROR"
        assert e2.value.status == 500
        assert "boom" in e2.value.message
    finally:
        await client.aclose()


async def test_backend_connection_error_maps_to_503():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = BackendClient(BASE, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendError) as excinfo:
            await client.get_messages("tok", "c-1")
        assert excinfo.value.code == "BACKEND_UNAVAILABLE"
        assert excinfo.value.status == 503
    finally:
        await client.aclose()


# ------------------------------------------------------------------ retriever
async def test_retriever_query_db_url_params_auth_and_chunks_fallback():
    captured = {}

    captured_bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        captured["auth"] = request.headers.get("authorization")
        captured_bodies.append(json.loads(request.content.decode()))
        if captured["params"]["collection"] == "old":
            return _json_response(request, 200, {"answer": "旧的", "sources": ["a.pdf"]})
        return _json_response(
            request,
            200,
            {
                "chunks": [
                    {"content": "块1", "source": "a.pdf", "score": 0.9, "metadata": {}}
                ],
                "answer": "块1",
            },
        )

    client = RetrieverClient(BASE, timeout=60, transport=httpx.MockTransport(handler))
    try:
        result = await client.query_db("tok", "knowledge_chunks", "去年费用", top_k=7)
        old = await client.query_db("tok", "old", "旧问题", top_k=3)
    finally:
        await client.aclose()

    assert captured["auth"] == "Bearer tok"
    assert captured_bodies == [{"question": "去年费用"}, {"question": "旧问题"}]
    assert captured["params"]["top_k"] == "3"
    assert captured["params"]["rerank"] == "false"
    assert result["chunks"][0]["source"] == "a.pdf"
    assert old["chunks"] == []
    assert old["answer"] == "旧的"


async def test_retriever_query_excel_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return _json_response(
            request, 200, {"answer": "{\"rows\":1}", "sources": ["费用表.xlsx"]}
        )

    client = RetrieverClient(BASE, transport=httpx.MockTransport(handler))
    try:
        result = await client.query_excel("tok", "excel_db_chunks", "费用", top_k=5)
    finally:
        await client.aclose()

    assert captured["url"].split("?")[0] == f"{BASE}/retriever/excel"
    assert captured["params"] == {"collection": "excel_db_chunks", "top_k": "5"}
    assert result["answer"] == "{\"rows\":1}"
    assert result["sources"] == ["费用表.xlsx"]


async def test_retriever_error_maps_code_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request, 503, {"detail": "retriever unavailable", "message": "检索不可用"}
        )

    client = RetrieverClient(BASE, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendError) as excinfo:
            await client.query_db("tok", "knowledge_chunks", "q", top_k=1)
        assert excinfo.value.code == "retriever unavailable"
        assert excinfo.value.status == 503
        assert excinfo.value.message == "检索不可用"
    finally:
        await client.aclose()


# ------------------------------------------------------------------- reranker
RERANK_URL = "http://reranker.local/v1/rerank"


async def test_reranker_results_format_sorted_and_top_n():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        captured["auth"] = request.headers.get("authorization")
        return _json_response(
            request,
            200,
            {
                "results": [
                    {"index": 0, "relevance_score": 0.2},
                    {"index": 2, "relevance_score": 0.9},
                    {"index": 1, "score": 0.5},
                ]
            },
        )

    client = RerankerClient(RERANK_URL, "bge", "key-1", timeout=30, transport=httpx.MockTransport(handler))
    try:
        result = await client.rerank("query", ["a", "b", "c"], top_n=2)
    finally:
        await client.aclose()

    assert captured["url"] == RERANK_URL
    assert captured["auth"] == "Bearer key-1"
    assert captured["body"] == {
        "model": "bge",
        "query": "query",
        "documents": ["a", "b", "c"],
        "top_n": 2,
    }
    assert result == [(2, 0.9), (1, 0.5)]


async def test_reranker_rankings_format_and_empty_documents():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            200,
            {"rankings": [{"doc_index": 1, "score": 1.5}, {"doc_index": 0, "score": 0.1}]},
        )

    client = RerankerClient(RERANK_URL, "bge", "", transport=httpx.MockTransport(handler))
    try:
        result = await client.rerank("q", ["a", "b"], top_n=5)
        empty = await client.rerank("q", [], top_n=5)
    finally:
        await client.aclose()

    assert result == [(1, 1.5), (0, 0.1)]
    assert empty == []


async def test_reranker_http_error_and_bad_format_raise_backend_error():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="oops", request=request)
        return _json_response(request, 200, {"unexpected": []})

    client = RerankerClient(
        "http://reranker.local/v1/rerank", "bge", "", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(BackendError) as e1:
            await client.rerank("q", ["a"], top_n=1)
        assert e1.value.status == 500

        with pytest.raises(BackendError) as e2:
            await client.rerank("q", ["a"], top_n=1)
        assert e2.value.status == 502
    finally:
        await client.aclose()


async def test_reranker_out_of_range_index_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, 200, {"results": [{"index": 5, "score": 1.0}]})

    client = RerankerClient(RERANK_URL, "bge", "", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(BackendError):
            await client.rerank("q", ["only-one"], top_n=1)
    finally:
        await client.aclose()


def test_prepare_rerank_documents_placeholders_truncation_and_order():
    from app.clients.reranker_client import prepare_rerank_documents

    documents = ["", "   ", "命中" * 4000]
    prepared = prepare_rerank_documents(documents)

    # 下标必须与入参一一对应（重排返回的 index 是入参下标），长度/顺序不变
    assert len(prepared) == len(documents)
    assert all(text.strip() for text in prepared)
    assert prepared[2] == "命中" * 3000  # 单条上限 6000 字符
    assert len(prepared[2]) == 6000
    assert prepare_rerank_documents([]) == []

    # 单条上限是**按条**算的：10 条 5000 字符不会因为条数多被均分成 600
    many = ["长" * 5000 for _ in range(10)]
    assert all(text == "长" * 5000 for text in prepare_rerank_documents(many))


async def test_reranker_payload_documents_are_sanitized():
    """空文档 → 400（Only one multi-modal item）、超长 → 400（8192 token）都要在发请求前消掉。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return _json_response(request, 200, {"results": [{"index": 0, "score": 1.0}]})

    client = RerankerClient(RERANK_URL, "bge", "", transport=httpx.MockTransport(handler))
    try:
        await client.rerank("q", ["", "x" * 5000, "正常"], top_n=1)
    finally:
        await client.aclose()

    sent = captured["body"]["documents"]
    assert len(sent) == 3
    assert all(text.strip() for text in sent)
    assert sent[2] == "正常"


# --------------------------------------------------------------------- search
async def test_search_form_encoding_and_result_parse():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type", "")
        captured["form"] = urllib.parse.parse_qs(request.content.decode())
        extra = [{"title": f"t{i}", "url": f"u{i}", "content": f"c{i}"} for i in range(5)]
        return _json_response(request, 200, {"results": extra})

    client = SearchClient("http://search.local/search", transport=httpx.MockTransport(handler))
    try:
        results = await client.search("去年 费用", count=2)
    finally:
        await client.aclose()

    assert captured["method"] == "POST"
    assert captured["url"] == "http://search.local/search"
    assert "application/x-www-form-urlencoded" in captured["content_type"]
    assert captured["form"] == {"q": ["去年 费用"], "format": ["json"]}
    assert results == [
        SearchResult(title="t0", url="u0", content="c0"),
        SearchResult(title="t1", url="u1", content="c1"),
    ]


async def test_search_error_malformed_json_and_missing_results():
    clients: list[SearchClient] = []
    for case in ("error", "bad", "empty"):
        def build(case=case):
            def h(request: httpx.Request) -> httpx.Response:
                if case == "error":
                    return httpx.Response(503, text="unavailable", request=request)
                if case == "bad":
                    return httpx.Response(200, text="<html>", request=request)
                return _json_response(request, 200, {"foo": "bar"})

            return h

        clients.append(SearchClient("http://search.local/search", transport=httpx.MockTransport(build())))

    try:
        with pytest.raises(BackendError) as excinfo:
            await clients[0].search("q")
        assert excinfo.value.status == 503
        with pytest.raises(BackendError):
            await clients[1].search("q")
        assert await clients[2].search("q") == []
    finally:
        for c in clients:
            await c.aclose()
