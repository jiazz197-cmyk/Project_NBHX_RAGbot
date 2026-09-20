"""``BGEM3EmbeddingWrapper`` 承重语义回归（issue #35；含并入的 #28 批量接口）。

外部 BGE-M3 网关不可达，全部用 ``httpx.MockTransport`` 注入 openai SDK 的
``http_client`` / ``async_http_client``（构造器参数；正式代码默认走全局 httpx 单例）。

覆盖 issue #35 列出的 5 条必须保留的语义 + #28 的批量收敛：

1. 空文本 → 零向量（且不发给网关：空串会让 rerank 网关 400）；
2. NaN / Inf → 零向量；
3. 批量整批失败 → 逐条回退；
4. 每次重试的日志行；
5. ``probe`` 单次探活 + ``embed_text`` / ``embed_texts`` 公开方法；
6. ``get_text_embedding_batch`` → 一次 HTTP 带 ``BGE_M3_BATCH_SIZE`` 条。
"""

from __future__ import annotations

import json
import logging
from typing import List, Optional

import pytest

# 重依赖（llama_index）缺失时跳过：CI 的最小依赖集里没有它
pytest.importorskip("llama_index")

import httpx  # noqa: E402

from app.adapters.doc_processing import embedding_store  # noqa: E402
from app.adapters.doc_processing.embedding_store import BGEM3EmbeddingWrapper  # noqa: E402
from app.adapters.doc_processing.exceptions import EmbeddingError  # noqa: E402
from app.core.config import settings  # noqa: E402

_EMBEDDING_URL = "http://embedding.test/v1/embeddings"
_DIM = embedding_store._EMBED_DIM
_ZERO = [0.0] * _DIM


class FakeGateway:
    """假 BGE-M3 网关（OpenAI 兼容）：记录每次请求的 input，可注入各种故障。

    返回的向量以「输入序号 + 1」填满，便于断言结果与输入的对应关系。
    """

    def __init__(
        self,
        *,
        reject_batch: bool = False,
        drop_last: bool = False,
        nan_texts: Optional[set] = None,
        empty_data: bool = False,
        reverse: bool = False,
        vector_key: bool = False,
        status: int = 200,
    ) -> None:
        self.calls: List[List[str]] = []
        self.urls: List[str] = []
        self.bodies: List[dict] = []
        self.reject_batch = reject_batch
        self.drop_last = drop_last
        self.nan_texts = nan_texts or set()
        self.empty_data = empty_data
        self.reverse = reverse
        self.vector_key = vector_key
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        inputs: List[str] = body["input"]
        self.calls.append(inputs)
        self.urls.append(str(request.url))
        self.bodies.append(body)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "boom"}})
        if self.reject_batch and len(inputs) > 1:
            return httpx.Response(400, json={"error": {"message": "batch rejected"}})
        if self.empty_data:
            data: List[dict] = []
        else:
            key = "vector" if self.vector_key else "embedding"
            data = [
                {
                    "object": "embedding",
                    "index": i,
                    key: [float("nan")] * _DIM if text in self.nan_texts else [float(i + 1)] * _DIM,
                }
                for i, text in enumerate(inputs)
            ]
            if self.drop_last and len(data) > 1:
                data = data[:-1]
        if self.reverse:
            data = list(reversed(data))
        # 手写 content：httpx 的 json= 走 allow_nan=False，NaN 用例需要原样输出 NaN
        payload = json.dumps({"object": "list", "data": data, "model": "bge-m3"}).encode()
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})

    @property
    def single_item_calls(self) -> List[str]:
        return [call[0] for call in self.calls if len(call) == 1]


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """重试间隔默认 3s（.env），测试里把等待压成 0，仅保留重试次数与日志。"""
    from tenacity import wait_fixed

    monkeypatch.setitem(embedding_store._RETRY_KWARGS, "wait", wait_fixed(0))


def _wrapper(gw: FakeGateway) -> BGEM3EmbeddingWrapper:
    """同步用例：注入 MockTransport 客户端（正式代码默认走全局单例）。"""
    return BGEM3EmbeddingWrapper(
        api_url=_EMBEDDING_URL,
        http_client=httpx.Client(transport=httpx.MockTransport(gw)),
    )


class _AsyncHarness:
    """异步用例：把 MockTransport 异步客户端注入 wrapper，并在同一事件循环里关闭。"""

    def __init__(self, gw: FakeGateway) -> None:
        self.gw = gw

    async def __aenter__(self) -> BGEM3EmbeddingWrapper:
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.gw))
        await self.client.__aenter__()
        self.wrapper = BGEM3EmbeddingWrapper(api_url=_EMBEDDING_URL, async_http_client=self.client)
        return self.wrapper

    async def __aexit__(self, *exc) -> None:
        await self.client.__aexit__(*exc)


# ---------------------------------------------------------------------------
# 1. 空文本 → 零向量
# ---------------------------------------------------------------------------

def test_empty_text_returns_zero_vector_without_http():
    gw = FakeGateway()
    wrapper = _wrapper(gw)

    assert wrapper.embed_text("") == _ZERO
    assert wrapper.embed_text("   \n\t ") == _ZERO
    assert gw.calls == []  # 空文本根本不发（空串会让 rerank 网关 400）


def test_empty_texts_keep_positions_in_batch():
    gw = FakeGateway()
    wrapper = _wrapper(gw)

    vectors = wrapper.embed_texts(["a", "", "  ", "d"])

    assert len(vectors) == 4
    assert vectors[1] == _ZERO and vectors[2] == _ZERO
    assert vectors[0] == [1.0] * _DIM and vectors[3] == [2.0] * _DIM
    assert gw.calls == [["a", "d"]]  # 只把非空文本发出去，且顺序不变


def test_embed_texts_empty_input_short_circuits():
    gw = FakeGateway()
    assert _wrapper(gw).embed_texts([]) == []
    assert gw.calls == []


# ---------------------------------------------------------------------------
# 2. NaN / Inf → 零向量
# ---------------------------------------------------------------------------

def test_nan_vector_falls_back_to_zero_vector(caplog):
    gw = FakeGateway(nan_texts={"bad"})
    wrapper = _wrapper(gw)

    with caplog.at_level(logging.WARNING, logger="app.adapters.doc_processing.embedding_store"):
        vectors = wrapper.embed_texts(["ok", "bad"])

    assert vectors[0] == [1.0] * _DIM
    assert vectors[1] == _ZERO  # NaN 不进 pgvector
    assert any("NaN/Inf" in record.getMessage() for record in caplog.records)


def test_nan_vector_on_single_text_path():
    gw = FakeGateway(nan_texts={"bad"})
    assert _wrapper(gw).embed_text("bad") == _ZERO


# ---------------------------------------------------------------------------
# 3. 批量整批失败 → 逐条回退
# ---------------------------------------------------------------------------

def test_rejected_batch_falls_back_to_per_item_requests():
    gw = FakeGateway(reject_batch=True)
    wrapper = _wrapper(gw)

    vectors = wrapper.embed_texts(["a", "b", "c"])

    assert len(vectors) == 3  # 整批 400 不能整批丢
    assert gw.single_item_calls == ["a", "b", "c"]
    assert len(gw.calls[: settings.BGE_M3_MAX_RETRIES]) == settings.BGE_M3_MAX_RETRIES
    assert all(len(call) == 3 for call in gw.calls[: settings.BGE_M3_MAX_RETRIES])  # 前 N 次是批量尝试


def test_short_batch_response_falls_back_to_per_item_requests():
    """网关少返回一条时不能静默丢 chunk（条数校验 → 逐条重来）。"""
    gw = FakeGateway(drop_last=True)
    wrapper = _wrapper(gw)

    vectors = wrapper.embed_texts(["a", "b", "c"])

    assert len(vectors) == 3
    assert gw.single_item_calls == ["a", "b", "c"]


def test_single_request_failure_raises_embedding_error():
    gw = FakeGateway(status=500)
    with pytest.raises(EmbeddingError):
        _wrapper(gw).embed_text("boom")


def test_single_text_failure_retries_once_not_twice():
    """单条路径（查询嵌入）失败只走一个重试周期。

    回归点：若 ``_get_text_embedding`` 也走批量实现，失败会先按「批量」重试一轮
    （2×间隔）再按「单条」重试一轮，最坏耗时从 6s 翻到 12s——查询链路承重。
    """
    gw = FakeGateway(status=500)
    with pytest.raises(EmbeddingError):
        _wrapper(gw).embed_text("boom")

    assert len(gw.calls) == settings.BGE_M3_MAX_RETRIES
    assert all(len(call) == 1 for call in gw.calls)


# ---------------------------------------------------------------------------
# 4. 每次重试的日志行
# ---------------------------------------------------------------------------

def test_every_retry_is_logged(caplog):
    gw = FakeGateway(reject_batch=True)
    wrapper = _wrapper(gw)

    with caplog.at_level(logging.WARNING, logger="app.adapters.doc_processing.embedding_store"):
        wrapper.embed_texts(["a", "b"])

    messages = [record.getMessage() for record in caplog.records]
    for attempt in range(1, settings.BGE_M3_MAX_RETRIES):
        assert any(
            f"批量嵌入第 {attempt}/{settings.BGE_M3_MAX_RETRIES} 次失败" in message for message in messages
        ), messages
    # 最后一次失败不重试（只有 before_sleep 回调在尝试之间触发）
    assert not any(f"第 {settings.BGE_M3_MAX_RETRIES}/" in message for message in messages)


# ---------------------------------------------------------------------------
# 5. probe 探活 + 公开方法
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_sends_single_minimal_request():
    gw = FakeGateway()
    async with _AsyncHarness(gw) as wrapper:
        vector = await wrapper.probe(timeout_sec=1.0)

    assert vector == [1.0] * _DIM
    assert gw.calls == [["ping"]]
    assert gw.urls == [_EMBEDDING_URL]  # api_base 由完整端点剥出来，路径不变


@pytest.mark.asyncio
async def test_probe_is_single_attempt_no_retry():
    gw = FakeGateway(status=404)
    async with _AsyncHarness(gw) as wrapper:
        with pytest.raises(Exception) as excinfo:  # noqa: B017  SDK 异常类型由 openai 决定
            await wrapper.probe(timeout_sec=1.0)

    assert len(gw.calls) == 1  # 探活不套用 tenacity 重试（启动会卡 9s+）
    assert "404" in str(excinfo.value)


@pytest.mark.asyncio
async def test_probe_rejects_200_without_embedding_data():
    gw = FakeGateway(empty_data=True)
    async with _AsyncHarness(gw) as wrapper:
        with pytest.raises(EmbeddingError):
            await wrapper.probe(timeout_sec=1.0)

    assert len(gw.calls) == 1


def test_request_body_asks_for_float_encoding():
    """线上请求形状：显式 encoding_format=float（SDK 默认会发 base64）。"""
    gw = FakeGateway()
    _wrapper(gw).embed_text("x")

    assert gw.bodies == [{"input": ["x"], "model": "bge-m3", "encoding_format": "float"}]


def test_non_openai_response_shape_raises_clear_error():
    """旧实现嗅探 `data[].vector`；改造后只认 OpenAI 标准的 `data[].embedding`，但要报清楚。"""
    gw = FakeGateway(vector_key=True)
    wrapper = _wrapper(gw)

    with pytest.raises(EmbeddingError, match="embedding"):
        wrapper.embed_text("x")


def test_public_methods_are_kept():
    wrapper = _wrapper(FakeGateway())
    assert wrapper.embed_text("x") == [1.0] * _DIM
    assert wrapper.embed_texts(["x", "y"]) == [[1.0] * _DIM, [2.0] * _DIM]
    assert wrapper.get_memory_info()["mode"] == "remote"


# ---------------------------------------------------------------------------
# 6. #28：批量接口（入库主链路）真实批量 + 异步同语义
# ---------------------------------------------------------------------------

def test_batch_interface_uses_one_http_request_per_batch():
    gw = FakeGateway()
    wrapper = _wrapper(gw)
    texts = [f"chunk-{i}" for i in range(settings.BGE_M3_BATCH_SIZE + 6)]

    vectors = wrapper.get_text_embedding_batch(texts, show_progress=False)

    assert len(vectors) == len(texts)
    assert [len(call) for call in gw.calls] == [settings.BGE_M3_BATCH_SIZE, 6]


def test_vectors_follow_response_index_not_response_order():
    gw = FakeGateway(reverse=True)
    vectors = _wrapper(gw).embed_texts(["a", "b", "c"])
    assert vectors == [[1.0] * _DIM, [2.0] * _DIM, [3.0] * _DIM]


@pytest.mark.asyncio
async def test_async_batch_is_a_single_request():
    gw = FakeGateway()
    async with _AsyncHarness(gw) as wrapper:
        vectors = await wrapper.aget_text_embedding_batch(["a", "b", "c"], show_progress=False)

    assert len(vectors) == 3
    assert [len(call) for call in gw.calls] == [3]


@pytest.mark.asyncio
async def test_async_path_keeps_empty_text_semantics():
    gw = FakeGateway()
    async with _AsyncHarness(gw) as wrapper:
        assert await wrapper.aget_query_embedding("") == _ZERO
        assert gw.calls == []
        assert await wrapper.aget_text_embedding("x") == [1.0] * _DIM


@pytest.mark.asyncio
async def test_async_single_text_failure_retries_once_not_twice():
    gw = FakeGateway(status=500)
    async with _AsyncHarness(gw) as wrapper:
        with pytest.raises(EmbeddingError):
            await wrapper.aget_query_embedding("boom")

    assert len(gw.calls) == settings.BGE_M3_MAX_RETRIES


# ---------------------------------------------------------------------------
# 端点归一化（配置里存的是完整端点，SDK 要的是 base_url）
# ---------------------------------------------------------------------------

def test_api_base_derivation():
    assert embedding_store._to_api_base("http://h:8096/v1/embeddings") == "http://h:8096/v1"
    assert embedding_store._to_api_base("http://h:8096/v1/embeddings/") == "http://h:8096/v1"
    assert embedding_store._to_api_base("http://h:8096/v1") == "http://h:8096/v1"
