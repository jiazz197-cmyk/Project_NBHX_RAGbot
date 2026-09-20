"""LLMClient 参数装配与 structured JSON 容错解析测试（不触网）。"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.clients import llm_client as llm_module
from app.clients.llm_client import LLMClient, LLMError
from app.config import settings


class Out(BaseModel):
    value: int
    label: str = ""


class _FakeMessage:
    def __init__(self, content: Any):
        self.content = content


def _install_fake_model(monkeypatch, response_content: str = "{}", exc: Exception | None = None):
    created: dict[str, dict] = {}
    instances: list[Any] = []

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.bound_tools = None
            self.messages = None
            instances.append(self)
            created.update(kwargs)

        def bind_tools(self, tools):
            self.bound_tools = tools
            return self

        async def ainvoke(self, messages):
            self.messages = messages
            if exc is not None:
                raise exc
            return _FakeMessage(response_content)

    monkeypatch.setattr(llm_module, "ChatOpenAI", FakeChatOpenAI)
    return created, instances, FakeChatOpenAI


def _fresh_settings():
    return settings.model_copy(deep=True)


def test_main_model_kwargs_and_api_key_fallback(monkeypatch):
    created, _, fake_cls = _install_fake_model(monkeypatch)
    s = _fresh_settings()
    s.MAIN_LLM_API_URL = "http://main-llm/v1"
    s.MAIN_LLM_API_KEY = ""
    client = LLMClient(s)

    model = client.main_model(streaming=True)

    # main_model 基于当前模块绑定的 ChatOpenAI 构造保留思考的子类
    assert isinstance(model, fake_cls)
    assert created["base_url"] == "http://main-llm/v1"
    assert created["api_key"] == "not-needed"
    assert created["model"] == s.MAIN_LLM_MODEL
    assert created["timeout"] == s.LANGCHAIN_CHAT_TIMEOUT_SEC
    assert created["max_tokens"] == s.LANGCHAIN_MAX_OUTPUT_TOKENS
    assert created["temperature"] == s.MAIN_LLM_TEMPERATURE
    assert created["streaming"] is True
    # 自定义 base_url 下 langchain-openai 不会自动开启 stream_usage，必须显式传
    assert created["stream_usage"] is True


def test_main_model_binds_tools(monkeypatch):
    created, instances, _ = _install_fake_model(monkeypatch)
    client = LLMClient(_fresh_settings())
    tools = [{"type": "function", "function": {"name": "python_exec"}}]

    model = client.main_model(streaming=False, tools=tools)

    assert model is instances[0]
    assert model.bound_tools == tools
    assert created["streaming"] is False


def test_sub_model_falls_back_to_main(monkeypatch):
    created, _, _ = _install_fake_model(monkeypatch)
    s = _fresh_settings()
    s.MAIN_LLM_API_URL = "http://main/v1"
    s.MAIN_LLM_API_KEY = "main-key"
    s.MAIN_LLM_MODEL = "main-model"
    s.SUB_LLM_API_URL = ""
    s.SUB_LLM_MODEL = ""
    s.SUB_LLM_API_KEY = ""
    s.SUB_LLM_ENABLE_THINKING = False
    client = LLMClient(s)

    client.sub_model()

    assert created["base_url"] == "http://main/v1"
    assert created["api_key"] == "main-key"
    assert created["model"] == "main-model"
    assert created["timeout"] == s.SUB_LLM_TIMEOUT_SEC
    assert created["temperature"] == s.SUB_LLM_TEMPERATURE
    assert created["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_sub_model_prefers_sub_values_and_can_enable_thinking(monkeypatch):
    created, _, _ = _install_fake_model(monkeypatch)
    s = _fresh_settings()
    s.MAIN_LLM_API_URL = "http://main/v1"
    s.MAIN_LLM_MODEL = "main-model"
    s.MAIN_LLM_API_KEY = "main-key"
    s.SUB_LLM_API_URL = "http://sub/v1"
    s.SUB_LLM_MODEL = "sub-model"
    s.SUB_LLM_API_KEY = "sub-key"
    s.SUB_LLM_ENABLE_THINKING = True
    client = LLMClient(s)

    client.sub_model()

    assert created["base_url"] == "http://sub/v1"
    assert created["api_key"] == "sub-key"
    assert created["model"] == "sub-model"
    assert "extra_body" not in created


async def test_structured_parses_fenced_json_and_sends_messages(monkeypatch):
    created, instances, _ = _install_fake_model(
        monkeypatch,
        response_content='好的，结果如下：\n```json\n{"value": 12, "label": "费用"}\n```\n希望有帮助',
    )
    client = LLMClient(_fresh_settings())

    result = await client.structured(system="系统提示", user="用户问题", schema_cls=Out)

    assert isinstance(result, Out)
    assert result.value == 12
    assert result.label == "费用"
    messages = instances[0].messages
    assert any(isinstance(m, SystemMessage) for m in messages)
    assert any(isinstance(m, HumanMessage) for m in messages)
    assert messages[0].content == "系统提示"
    assert messages[1].content == "用户问题"
    assert "extra_body" in created  # structured 走 sub_model


async def test_structured_plain_json_and_pydantic_validation_error(monkeypatch):
    _install_fake_model(monkeypatch, response_content='{"value": 1, "label": "x"}')
    client = LLMClient(_fresh_settings())
    assert (await client.structured(system="s", user="u", schema_cls=Out)).value == 1

    monkeypatch.undo()
    _install_fake_model(monkeypatch, response_content='not json at all')
    with pytest.raises(LLMError):
        await client.structured(system="s", user="u", schema_cls=Out)


async def test_structured_raises_on_model_error_and_empty(monkeypatch):
    _install_fake_model(monkeypatch, exc=RuntimeError("gateway down"))
    client = LLMClient(_fresh_settings())
    with pytest.raises(LLMError):
        await client.structured(system="s", user="u", schema_cls=Out)

    monkeypatch.undo()
    _install_fake_model(monkeypatch, response_content="")
    with pytest.raises(LLMError):
        await client.structured(system="s", user="u", schema_cls=Out)


def test_main_model_preserves_gateway_reasoning_content():
    """langchain-openai 1.x 的 ChatOpenAI 会丢弃 delta.reasoning_content；main_model 的
    子类必须把它挂回 AIMessageChunk.additional_kwargs（真实类离线转 chunk，不触网）。"""
    from langchain_core.messages import AIMessageChunk

    client = LLMClient(_fresh_settings())
    model = client.main_model(streaming=True)

    def _chunk(delta: dict | None) -> dict:
        return {
            "id": "chatcmpl-x",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }

    # 思考增量 → additional_kwargs.reasoning_content
    reasoning_gen = model._convert_chunk_to_generation_chunk(
        _chunk({"role": "assistant", "reasoning_content": "思考增量"}), AIMessageChunk, None
    )
    assert reasoning_gen is not None
    assert reasoning_gen.message.additional_kwargs.get("reasoning_content") == "思考增量"

    # 正文增量保持原样，不注入 reasoning_content
    content_gen = model._convert_chunk_to_generation_chunk(
        _chunk({"role": "assistant", "content": "答案"}), AIMessageChunk, None
    )
    assert content_gen.message.content == "答案"
    assert "reasoning_content" not in content_gen.message.additional_kwargs

    # 无 choices 的 usage 帧不报错；delta 为 None 的帧返回 None（对齐基类行为）
    usage_gen = model._convert_chunk_to_generation_chunk(
        {"id": "x", "choices": []}, AIMessageChunk, None
    )
    assert usage_gen is not None
    none_gen = model._convert_chunk_to_generation_chunk(_chunk(None), AIMessageChunk, None)
    assert none_gen is None


async def test_aclose_is_safe(monkeypatch):
    _install_fake_model(monkeypatch)
    client = LLMClient(_fresh_settings())
    client.main_model()
    client.sub_model()
    await client.aclose()
