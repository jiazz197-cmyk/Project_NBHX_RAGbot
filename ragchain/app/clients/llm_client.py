"""LangChain 1.x LLM 客户端（.dsh/ragchain-interfaces.md §11）。

只创建并调用 ChatOpenAI，不引入 function calling 依赖；structured 通过
「模型只输出 JSON」+ 容错解析实现。

主 LLM（Qwen3 思考模型，经 Sophnet OpenAI 兼容网关）的思考增量走
``delta.reasoning_content``；langchain-openai 1.x 的 ``ChatOpenAI`` 只认
官方 OpenAI 规范，会丢弃该字段（见其模块 docstring），因此这里用子类在
chunk 转换层把它挂回 ``AIMessageChunk.additional_kwargs["reasoning_content"]``，
供 generate 步骤以 ``thinking`` 事件流出。
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError as PydanticValidationError

from ..config import Settings


class LLMError(RuntimeError):
    """LLM 调用或结构化输出解析失败。"""


def _reasoning_delta(chunk: Any) -> str:
    """从原始 chat.completion.chunk dict 里取 ``delta.reasoning_content``。"""
    if not isinstance(chunk, dict):
        return ""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        # beta.chat.completions.stream 的包装形态
        wrapped = chunk.get("chunk")
        choices = wrapped.get("choices") if isinstance(wrapped, dict) else None
        if not isinstance(choices, list) or not choices:
            return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    value = delta.get("reasoning_content")
    return value if isinstance(value, str) else ""


def _reasoning_preserving_cls(base: type) -> type:
    """构造保留 ``reasoning_content`` 的 ``base``（ChatOpenAI）子类。

    基类经参数显式传入（调用时解析模块属性），保持可测试性：单测
    monkeypatch ``llm_client.ChatOpenAI`` 后 ``main_model`` 仍基于替换类工作。
    """

    class ReasoningPreservingChatOpenAI(base):  # type: ignore[misc,valid-type]
        def _convert_chunk_to_generation_chunk(
            self,
            chunk: Any,
            default_chunk_class: Any,
            base_generation_info: Any,
        ) -> Any:
            generation_chunk = super()._convert_chunk_to_generation_chunk(
                chunk, default_chunk_class, base_generation_info
            )
            if generation_chunk is None:
                return None
            reasoning = _reasoning_delta(chunk)
            if reasoning and isinstance(generation_chunk.message, AIMessageChunk):
                generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
            return generation_chunk

    return ReasoningPreservingChatOpenAI


def _extract_content_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _strip_noise_and_parse_json(text: str) -> Any:
    """剥离 markdown code fence / 前后解释文字，再 json.loads。"""
    text = (text or "").strip().lstrip("\ufeff").strip()
    if not text:
        raise LLMError("LLM 返回为空，无法解析 JSON")

    fence_match = re.search(r"```(?:[a-zA-Z0-9_+\-.]*)\s*\n?(.*?)```", text, re.DOTALL)
    candidate = fence_match.group(1).strip() if fence_match else text

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = candidate[start : end + 1]

    errors: list[str] = []
    for attempt in (candidate, text):
        try:
            return json.loads(attempt)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    # 处理 JSON 前后夹带含花括号解释文字等噪声：逐位置 raw_decode。
    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(candidate[index:])
            return value
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise LLMError(f"LLM 返回不是合法 JSON: {errors[-1] if errors else 'unknown'}")


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        # 仅保留最近创建的模型，供 aclose 尽力释放底层 httpx client。
        self._last_main: Any = None
        self._last_sub: Any = None

    # ------------------------------------------------------------------ models
    def main_model(self, *, streaming: bool = True, tools: list | None = None) -> Any:
        """主 LLM：OpenAI 兼容流式模型；tools 非空时绑定工具。

        使用保留 ``reasoning_content`` 的 ChatOpenAI 子类，思考增量经
        ``additional_kwargs["reasoning_content"]`` 透出。
        """
        model = _reasoning_preserving_cls(ChatOpenAI)(
            base_url=self.settings.MAIN_LLM_API_URL,
            api_key=self.settings.MAIN_LLM_API_KEY or "not-needed",
            model=self.settings.MAIN_LLM_MODEL,
            timeout=self.settings.LANGCHAIN_CHAT_TIMEOUT_SEC,
            max_tokens=self.settings.LANGCHAIN_MAX_OUTPUT_TOKENS,
            temperature=self.settings.MAIN_LLM_TEMPERATURE,
            streaming=streaming,
        )
        self._last_main = model
        if tools:
            return model.bind_tools(tools)
        return model

    def sub_model(self) -> ChatOpenAI:
        """辅 LLM：SUB_* 优先，空值回退 MAIN_*；默认关闭 thinking。"""
        s = self.settings
        kwargs: dict[str, Any] = {
            "base_url": s.SUB_LLM_API_URL or s.MAIN_LLM_API_URL,
            "api_key": s.SUB_LLM_API_KEY or s.MAIN_LLM_API_KEY or "not-needed",
            "model": s.SUB_LLM_MODEL or s.MAIN_LLM_MODEL,
            "timeout": s.SUB_LLM_TIMEOUT_SEC,
            "max_tokens": s.LANGCHAIN_MAX_OUTPUT_TOKENS,
            "temperature": s.SUB_LLM_TEMPERATURE,
        }
        if not s.SUB_LLM_ENABLE_THINKING:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }
        model = ChatOpenAI(**kwargs)
        self._last_sub = model
        return model

    # -------------------------------------------------------------- structured
    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema_cls: type[BaseModel],
    ) -> BaseModel:
        """调用子模型并将 JSON 解析为 ``schema_cls``；失败抛 LLMError。"""
        model = self.sub_model()
        try:
            response = await model.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/SDK 异常统一转 LLMError
            raise LLMError(f"调用 SUB LLM 失败: {exc}") from exc

        try:
            raw = _extract_content_text(response)
            data = _strip_noise_and_parse_json(raw)
            if not isinstance(data, dict):
                raise LLMError("LLM 结构化输出不是 JSON 对象")
            return schema_cls.model_validate(data)
        except LLMError:
            raise
        except PydanticValidationError as exc:
            raise LLMError(f"LLM 结构化输出不符合 schema: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"LLM 结构化输出解析失败: {exc}") from exc

    async def aclose(self) -> None:
        """尽力关闭最近创建的 ChatOpenAI 底层 async client；失败不抛出。"""
        for model in (self._last_main, self._last_sub):
            if model is None:
                continue
            for attr in ("async_client", "root_async_client"):
                client = getattr(model, "__dict__", {}).get(attr)
                if client is None:
                    continue
                close = getattr(client, "aclose", None) or getattr(client, "close", None)
                if close is None:
                    continue
                try:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
                    pass
