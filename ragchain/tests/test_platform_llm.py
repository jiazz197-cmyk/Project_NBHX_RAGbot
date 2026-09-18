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
    return created, instances


def _fresh_settings():
    return settings.model_copy(deep=True)


def test_main_model_kwargs_and_api_key_fallback(monkeypatch):
    created, _ = _install_fake_model(monkeypatch)
    s = _fresh_settings()
    s.MAIN_LLM_API_URL = "http://main-llm/v1"
    s.MAIN_LLM_API_KEY = ""
    client = LLMClient(s)

    model = client.main_model(streaming=True)

    assert model.__class__.__name__ == "FakeChatOpenAI"
    assert created["base_url"] == "http://main-llm/v1"
    assert created["api_key"] == "not-needed"
    assert created["model"] == s.MAIN_LLM_MODEL
    assert created["timeout"] == s.LANGCHAIN_CHAT_TIMEOUT_SEC
    assert created["max_tokens"] == s.LANGCHAIN_MAX_OUTPUT_TOKENS
    assert created["temperature"] == s.MAIN_LLM_TEMPERATURE
    assert created["streaming"] is True


def test_main_model_binds_tools(monkeypatch):
    created, instances = _install_fake_model(monkeypatch)
    client = LLMClient(_fresh_settings())
    tools = [{"type": "function", "function": {"name": "python_exec"}}]

    model = client.main_model(streaming=False, tools=tools)

    assert model is instances[0]
    assert model.bound_tools == tools
    assert created["streaming"] is False


def test_sub_model_falls_back_to_main(monkeypatch):
    created, _ = _install_fake_model(monkeypatch)
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
    created, _ = _install_fake_model(monkeypatch)
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
    created, instances = _install_fake_model(
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


async def test_aclose_is_safe(monkeypatch):
    _install_fake_model(monkeypatch)
    client = LLMClient(_fresh_settings())
    client.main_model()
    client.sub_model()
    await client.aclose()
