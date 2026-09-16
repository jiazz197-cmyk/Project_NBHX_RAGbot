"""TagGenerator 服务接入回归测试（issue #10）。

覆盖三层：

1. ``HttpTagGeneratorClient``（``app/adapters/doc_processing/tagger_client.py``）
   —— 用 ``httpx.MockTransport`` 打桩，不依赖真实容器：请求契约、响应清洗、
   错误映射（``ExternalServiceError``/502）、重试策略（5xx 重试、4xx 不重试）、
   连续失败熔断、服务端 ``fallback`` 告警只打一次、启动探活。
2. ``TagGenerator`` 薄壳（``text_splitter.py``）—— 注入假 client：正常透传、
   失败降级为本地 CPU ``_simple_tags``、降级日志不刷屏、保留旧构造签名。
3. 真实容器端到端 —— 需要 tagger 容器在跑，用 ``TAGGER_LIVE_TEST=1`` 显式开启::

       TAGGER_LIVE_TEST=1 pytest tests/test_tag_generator_client.py -v

   默认跳过（CI 与无容器环境不应因此变红）。
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import pytest

from app.adapters.doc_processing import tagger_client
from app.adapters.doc_processing.tagger_client import HttpTagGeneratorClient
from app.core.exceptions import ExternalServiceError
from app.ports.outbound.tag_generator import TagGeneratorPort

_ENDPOINT = "http://tagger.test/v1/tags"

# 与 issue #10 / Tag.http.yaml 一致的样例
_SAMPLE_TEXT = "公司2024年净利润为12.5亿元，同比增长15%。"


def _import_text_splitter():
    """text_splitter 依赖 transformers / sklearn / langchain（RAG 栈）；缺则跳过。"""
    for module in ("pandas", "transformers", "sklearn", "langchain_text_splitters"):
        pytest.importorskip(module)
    from app.adapters.doc_processing import text_splitter

    return text_splitter


@pytest.fixture
def fake_http(monkeypatch):
    """把适配器模块里的 ``get_sync_http_client`` 换成 MockTransport 客户端。"""
    clients: list[httpx.Client] = []

    def _install(handler) -> httpx.Client:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        monkeypatch.setattr(tagger_client, "get_sync_http_client", lambda: client)
        return client

    yield _install
    for client in clients:
        client.close()


def _client(**overrides) -> HttpTagGeneratorClient:
    """默认关熔断/重试延迟，单测里只验证被显式打开的行为。"""
    kwargs = dict(
        endpoint=_ENDPOINT,
        timeout_sec=1.0,
        max_retries=2,
        retry_delay_sec=0.0,
        probe_timeout_sec=1.0,
        failure_threshold=0,
        cooldown_sec=0,
    )
    kwargs.update(overrides)
    return HttpTagGeneratorClient(**kwargs)


class TestRequestContract:
    def test_posts_text_num_tags_diversity_and_parses_tags(self, fake_http):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "tags": ["净利润", "同比增长", "12.5亿元"],
                    "model": "paraphrase-multilingual-MiniLM-L12-v2",
                    "processing_time_ms": 9.4,
                    "fallback": False,
                },
            )

        fake_http(handler)
        tags = _client().extract_tags(_SAMPLE_TEXT, num_tags=5, diversity=0.5)

        assert tags == ["净利润", "同比增长", "12.5亿元"]
        assert len(seen) == 1
        request = seen[0]
        assert str(request.url) == _ENDPOINT
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/json"
        assert json.loads(request.content) == {
            "text": _SAMPLE_TEXT,
            "num_tags": 5,
            "diversity": 0.5,
        }

    def test_default_num_tags_and_diversity_match_service_defaults(self, fake_http):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"tags": []})

        fake_http(handler)
        _client().extract_tags("文本")

        assert json.loads(seen[0].content) == {
            "text": "文本",
            "num_tags": 5,
            "diversity": 0.5,
        }

    def test_empty_text_short_circuits_without_http(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            calls.append(request)
            return httpx.Response(200, json={"tags": ["不应发生"]})

        fake_http(handler)
        client = _client()

        assert client.extract_tags("") == []
        assert client.extract_tags("   \n\t ") == []
        assert calls == []

    def test_truncates_dedupes_and_drops_blank_tags(self, fake_http):
        fake_http(
            lambda request: httpx.Response(
                200,
                json={"tags": ["净利润", "净利润", "  ", None, "同比增长", "毛利率"]},
            )
        )

        assert _client().extract_tags("文本", num_tags=2) == ["净利润", "同比增长"]


class TestErrorMapping:
    def test_5xx_retries_then_raises_external_service_error(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(500, text="boom")

        fake_http(handler)
        with pytest.raises(ExternalServiceError) as excinfo:
            _client(max_retries=2).extract_tags("文本")

        assert len(calls) == 2  # 重试生效
        assert excinfo.value.status_code == 502
        assert excinfo.value.details == {"service": "TagGenerator"}
        assert "HTTP 500" in str(excinfo.value)

    def test_4xx_is_not_retried(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(422, json={"detail": "text too short"})

        fake_http(handler)
        with pytest.raises(ExternalServiceError) as excinfo:
            _client(max_retries=3).extract_tags("文本")

        assert len(calls) == 1  # 4xx 重试无意义
        assert "HTTP 422" in str(excinfo.value)
        assert "text too short" in str(excinfo.value)

    def test_connect_error_raises_external_service_error(self, fake_http):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        fake_http(handler)
        with pytest.raises(ExternalServiceError) as excinfo:
            _client().extract_tags("文本")

        assert "ConnectError" in str(excinfo.value)

    def test_timeout_is_retried_and_mapped(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ReadTimeout("timed out")

        fake_http(handler)
        with pytest.raises(ExternalServiceError) as excinfo:
            _client(max_retries=2).extract_tags("文本")

        assert len(calls) == 2
        assert "ReadTimeout" in str(excinfo.value)

    def test_wrong_payload_shape_raises_without_retry(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"unexpected": True})

        fake_http(handler)
        with pytest.raises(ExternalServiceError) as excinfo:
            _client(max_retries=3).extract_tags("文本")

        assert len(calls) == 1  # 200 但契约不符，重试无用
        assert "tags" in str(excinfo.value)

    def test_non_json_response_is_mapped(self, fake_http):
        fake_http(lambda request: httpx.Response(200, text="<html>not json</html>"))

        with pytest.raises(ExternalServiceError):
            _client().extract_tags("文本")


class TestFailureCooldown:
    def test_short_circuits_after_threshold_failures(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectError("connection refused")

        fake_http(handler)
        client = _client(max_retries=1, failure_threshold=2, cooldown_sec=60)

        for _ in range(2):
            with pytest.raises(ExternalServiceError):
                client.extract_tags("文本")
        assert len(calls) == 2

        # 熔断生效：冷却期内不再发请求，直接映射为 502 让上层降级
        for _ in range(3):
            with pytest.raises(ExternalServiceError) as excinfo:
                client.extract_tags("文本")
            assert "熔断" in str(excinfo.value)
        assert len(calls) == 2

    def test_half_open_after_cooldown_recovers(self, fake_http):
        calls: list[httpx.Request] = []
        healthy = {"value": False}

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if not healthy["value"]:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"tags": ["净利润"]})

        fake_http(handler)
        client = _client(max_retries=1, failure_threshold=1, cooldown_sec=0.05)

        with pytest.raises(ExternalServiceError):
            client.extract_tags("文本")
        assert len(calls) == 1

        healthy["value"] = True
        time.sleep(0.06)  # 冷却期满 → 半开放行一次
        assert client.extract_tags("文本") == ["净利润"]
        assert len(calls) == 2

    def test_success_resets_failure_streak(self, fake_http):
        state = {"fail": True}
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if state["fail"]:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"tags": ["净利润"]})

        fake_http(handler)
        client = _client(max_retries=1, failure_threshold=2, cooldown_sec=60)

        with pytest.raises(ExternalServiceError):
            client.extract_tags("文本")
        state["fail"] = False
        assert client.extract_tags("文本") == ["净利润"]

        # 计数已复位：再失败 1 次不应触发熔断（阈值 2）
        state["fail"] = True
        with pytest.raises(ExternalServiceError):
            client.extract_tags("文本")
        with pytest.raises(ExternalServiceError) as excinfo:
            client.extract_tags("文本")
        assert "熔断" not in str(excinfo.value)


class TestFallbackAndProbe:
    def test_service_side_fallback_warns_once(self, fake_http, caplog):
        tagger_client._reset_fallback_warning()
        fake_http(
            lambda request: httpx.Response(
                200,
                json={
                    "tags": ["净利润"],
                    "model": "cpu-fallback",
                    "processing_time_ms": 1.0,
                    "fallback": True,
                },
            )
        )
        client = _client()

        with caplog.at_level(logging.WARNING, logger=tagger_client.__name__):
            assert client.extract_tags("文本") == ["净利润"]
            assert client.extract_tags("文本") == ["净利润"]

        warnings = [r for r in caplog.records if "CPU 兜底" in r.getMessage()]
        assert len(warnings) == 1, "服务端 fallback 告警必须只打一次，避免逐 chunk 刷屏"

    def test_probe_returns_service_metadata(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "tags": ["探活"],
                    "model": "paraphrase-multilingual-MiniLM-L12-v2",
                    "processing_time_ms": 9.4,
                    "fallback": False,
                },
            )

        fake_http(handler)
        info = _client().probe()

        assert info["endpoint"] == _ENDPOINT
        assert info["model"] == "paraphrase-multilingual-MiniLM-L12-v2"
        assert info["fallback"] is False
        assert len(calls) == 1

    def test_probe_is_single_attempt_and_maps_failure(self, fake_http):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectError("connection refused")

        fake_http(handler)
        with pytest.raises(ExternalServiceError) as excinfo:
            _client(max_retries=5).probe()

        assert len(calls) == 1, "探活必须单次命中，不套用正式调用链的重试"
        assert "探活失败" in str(excinfo.value)

    def test_probe_resets_open_circuit(self, fake_http):
        state = {"fail": True}
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if state["fail"]:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"tags": ["探活"]})

        fake_http(handler)
        client = _client(failure_threshold=1, cooldown_sec=3600)

        with pytest.raises(ExternalServiceError):
            client.extract_tags("文本")

        state["fail"] = False
        client.probe()  # 探活不受熔断限制，成功即复位

        assert client.extract_tags("文本") == ["探活"]


class TestTagGeneratorShell:
    def test_delegates_to_injected_client(self):
        text_splitter = _import_text_splitter()
        calls: list[tuple] = []

        class _FakeClient:
            def extract_tags(self, text, num_tags=5, diversity=0.5):
                calls.append((text, num_tags, diversity))
                return ["净利润", "同比增长"]

        shell = text_splitter.TagGenerator(device="auto", client=_FakeClient())

        assert shell.extract_tags(_SAMPLE_TEXT, num_tags=3, diversity=0.7) == [
            "净利润",
            "同比增长",
        ]
        assert calls == [(_SAMPLE_TEXT, 3, 0.7)]

    def test_empty_text_returns_empty_without_calling_client(self):
        text_splitter = _import_text_splitter()

        class _Exploding:
            def extract_tags(self, *args, **kwargs):  # pragma: no cover
                raise AssertionError("空文本不应调用服务")

        shell = text_splitter.TagGenerator(client=_Exploding())
        assert shell.extract_tags("  ") == []

    def test_degrades_to_local_simple_tags_on_failure(self, caplog):
        text_splitter = _import_text_splitter()

        class _Down:
            def extract_tags(self, *args, **kwargs):
                raise ExternalServiceError("TagGenerator", "HTTP 500 from x: boom")

        shell = text_splitter.TagGenerator(client=_Down())

        with caplog.at_level(logging.WARNING, logger=text_splitter.__name__):
            tags = shell.extract_tags(_SAMPLE_TEXT, num_tags=5)

        assert tags, "服务不可用时必须仍返回本地兜底标签"
        assert tags == text_splitter._simple_tags(_SAMPLE_TEXT, 5)
        assert all(isinstance(t, str) and t for t in tags)
        assert len(tags) <= 5
        assert any("降级" in r.getMessage() for r in caplog.records)

    def test_degradation_warning_is_not_repeated_per_chunk(self, caplog):
        text_splitter = _import_text_splitter()

        class _Down:
            def extract_tags(self, *args, **kwargs):
                raise ExternalServiceError("TagGenerator", "connection refused")

        shell = text_splitter.TagGenerator(client=_Down())

        with caplog.at_level(logging.WARNING, logger=text_splitter.__name__):
            for chunk in ("第一段文本内容", "第二段文本内容", "第三段文本内容"):
                shell.extract_tags(chunk, num_tags=3)

        degraded = [r for r in caplog.records if "降级" in r.getMessage()]
        assert len(degraded) == 1, "同一实例内降级日志只应告警一次"

    def test_legacy_signature_builds_http_client_lazily(self):
        text_splitter = _import_text_splitter()
        from app.core.config import settings

        shell = text_splitter.TagGenerator(device="auto")  # pipeline.py 的调用方式
        assert shell._model_name == "paraphrase-multilingual-MiniLM-L12-v2"

        client = shell.client  # 惰性构造，不发请求
        assert isinstance(client, HttpTagGeneratorClient)
        assert client.endpoint == settings.TAGGER_ENDPOINT


class TestIntegrationSurface:
    """结构性回归：主应用侧不再持有本地模型，且 Port/Adapter 表面一致。"""

    def test_text_splitter_no_longer_loads_local_model(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "adapters"
            / "doc_processing"
            / "text_splitter.py"
        ).read_text(encoding="utf-8")

        # 只匹配真正的 import 语句：注释/文档字符串里提到模型名是允许的
        for forbidden in ("torch", "keybert", "sentence_transformers"):
            pattern = rf"^\s*(?:import|from)\s+{forbidden}\b"
            assert not re.search(pattern, source, re.MULTILINE), (
                f"主应用不应再 import {forbidden}（issue #10：模型在 tagger 容器里）"
            )
        assert "_taggen_pool" not in source, "本地模型池应已随服务化移除"

    def test_adapter_matches_port_signature(self):
        port_sig = inspect.signature(TagGeneratorPort.extract_tags)
        adapter_sig = inspect.signature(HttpTagGeneratorClient.extract_tags)

        assert list(port_sig.parameters) == list(adapter_sig.parameters)
        assert port_sig.parameters["num_tags"].default == 5
        assert port_sig.parameters["diversity"].default == 0.5

    def test_port_stays_pure(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "ports"
            / "outbound"
            / "tag_generator.py"
        ).read_text(encoding="utf-8")

        for forbidden in ("app.adapters", "app.usecases", "app.api"):
            assert forbidden not in source


# ── 真实容器端到端（默认跳过） ──────────────────────────────────
_live = pytest.mark.skipif(
    os.environ.get("TAGGER_LIVE_TEST") != "1",
    reason="需要真实 tagger 容器：TAGGER_LIVE_TEST=1 pytest tests/test_tag_generator_client.py",
)


@_live
class TestLiveTaggerService:
    def test_health_and_probe(self):
        client = HttpTagGeneratorClient()  # 走 .env 的 TAGGER_ENDPOINT
        info = client.probe()

        assert info["endpoint"] == os.environ.get(
            "TAGGER_ENDPOINT", "http://localhost:8004/v1/tags"
        )
        assert info["model"], "服务端应返回实际模型名"
        assert info["fallback"] is False, "容器模型应已加载（/health 的 model_loaded）"

    def test_extract_tags_live(self):
        tags = HttpTagGeneratorClient().extract_tags(_SAMPLE_TEXT, num_tags=5, diversity=0.5)

        assert tags, "真实服务应返回标签"
        assert len(tags) <= 5
        assert all(isinstance(tag, str) and tag.strip() for tag in tags)

    def test_shell_end_to_end_live(self):
        text_splitter = _import_text_splitter()
        shell = text_splitter.TagGenerator(device="auto")

        tags = shell.extract_tags(_SAMPLE_TEXT, num_tags=5)

        assert tags
        assert len(tags) <= 5
        assert shell._degraded_logged is False, "真实容器在跑时不应走本地降级路径"
