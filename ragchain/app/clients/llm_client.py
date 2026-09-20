"""LangChain 1.x LLM 客户端（.dsh/ragchain-interfaces.md §11）。

只创建并调用 ChatOpenAI；structured 走
``with_structured_output(schema, method="json_mode")``（issue #31）：
``response_format={"type":"json_object"}`` 由 Sophnet 网关强制输出合法 JSON，
解析失败由 ``PydanticOutputParser`` 抛 ``OutputParserException``（响亮失败，
不做静默 None），调用点据此走各自降级。method 选型依据（2026-09-20 网关探针）：
``json_schema`` 网关只收参数不按 schema 强制、``function_calling`` 依赖
``tool_choice`` 遵守性且有静默 None 陷阱——结论详见
docs/langchain-rag-container-api-contract.md §10。

主 LLM（Qwen3 思考模型，经 Sophnet OpenAI 兼容网关）的思考增量走
``delta.reasoning_content``；langchain-openai 1.x 的 ``ChatOpenAI`` 只认
官方 OpenAI 规范，会丢弃该字段（见其模块 docstring），因此这里用子类在
chunk 转换层把它挂回 ``AIMessageChunk.additional_kwargs["reasoning_content"]``，
供 generate 步骤以 ``thinking`` 事件流出。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

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
            # 自定义 base_url 下 langchain-openai 不会自动开启 stream_usage（默认开启
            # 只对官方端点 / LangSmith 网关生效）→ 不传则 chunk 上没有 usage_metadata。
            stream_usage=True,
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
        """调用辅模型做结构化输出；失败一律抛 LLMError（调用点据此降级）。

        实现：``with_structured_output(schema_cls, method="json_mode")``（issue #31，
        替代手写「剥 markdown 围栏 + 逐位置 raw_decode」容错解析）：

        - ``json_mode`` 发 ``response_format={"type":"json_object"}``，Sophnet
          网关强制模型输出合法 JSON（2026-09-20 探针 R1/R5，见
          docs/langchain-rag-container-api-contract.md §10）——这是本网关唯一
          真正生效的硬保障；三个 step 的 prompt 都含 "JSON" 字样，满足网关
          「messages 必须含 json」校验。
        - 解析用 ``PydanticOutputParser``（内建 markdown 围栏容错）：JSON 解析
          或 Pydantic 校验失败抛 ``OutputParserException``——失败是「响」的，
          与各 step 的异常降级语义一致。
        - 不用 ``json_schema``：网关只收下参数、不按 schema 强制（探针 R2：
          强 schema 时问 1+1 仍回 ``{"answer": 2}``），等于没有保障还误导。
          （issue #31 里「默认是 json_schema」是旧版行为——本项目锁定的
          langchain-openai 1.6.2 默认 method 已是 ``function_calling``，见下条。）
        - 不用 ``function_calling``（langchain-openai 1.x 默认值）：其
          ``PydanticToolsParser(first_tool_only=True)`` 在网关忽略
          ``tool_choice`` 时**静默返回 None**（探针确认本网关遵守
          ``tool_choice``，但这是网关行为不是契约；换网关/升级即踩坑 #2）。
        """
        model = self.sub_model()
        runner = model.with_structured_output(schema_cls, method="json_mode")
        try:
            result = await runner.ainvoke(
                [SystemMessage(content=system), HumanMessage(content=user)]
            )
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/SDK/解析异常统一转 LLMError
            raise LLMError(f"SUB LLM 结构化输出失败: {exc}") from exc
        if result is None:
            # json_mode 解析器「要么返回实例要么抛异常」，None 不应出现；显式挡
            # 一道，防未来换 method（如 function_calling）后静默 None 漏过——
            # 防注入若把 None 当结果会直接判「通过」。
            raise LLMError("SUB LLM 结构化输出解析结果为 None")
        return result

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
