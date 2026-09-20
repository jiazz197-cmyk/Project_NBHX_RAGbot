"""主生成步骤：main_llm 流式 + 有界 python_exec 工具循环；来源页脚生成。

不引入 langgraph/agent 框架；工具调用通过 ``AIMessageChunk.__add__`` 聚合流式
tool_call_chunks（langchain-core 原生语义），再交给 deps.executor.execute 执行、
以 ToolMessage 回灌。

思考过程：Qwen3 主 LLM 的思考增量由 llm_client 挂在 chunk 的
``additional_kwargs["reasoning_content"]``，这里以 ``thinking`` 事件下发；
orchestrator 负责把它包成 ``<think>...</think>`` 随 message 帧流出（前端协议）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CALC_FALLBACK_NOTICE = "\n\n> 自动计算失败，结果为模型计算，请复核。"
TOOL_LOOP_LIMIT_NOTICE = "已达到 python_exec 调用次数上限，请直接基于已有信息给出最终回答，不要再次调用工具。"
TOOL_FAIL_INSTRUCTION = (
    "自动计算失败，不要再次调用 python_exec。请根据已有数据手算并列明算式与过程，"
    "并在正文中注明“自动计算失败，结果为模型计算，请复核”。"
)


class PythonExecArgs(BaseModel):
    code: str = Field(description="完整可执行的 Python 代码字符串，使用 print() 输出计算结果")


@dataclass
class GenerationEvent:
    kind: str  # token | thinking | notice | cancelled | done
    content: str = ""
    usage: dict[str, int] | None = None
    error: BaseException | None = None
    tool_failed: bool = False


def build_python_exec_tool(deps) -> StructuredTool:
    """构造暴露给 main_llm 的 python_exec 工具（执行仍由 deps.executor 完成）。"""

    async def _run(code: str) -> str:
        result = await deps.executor.execute(code)
        if getattr(result, "ok", False):
            output = str(getattr(result, "output", "") or "")
            return output or "(无输出)"
        return f"执行失败：{getattr(result, 'error', '未知错误')}"

    return StructuredTool.from_function(
        coroutine=_run,
        name="python_exec",
        description=(
            "执行 Python 代码进行财务数据计算。仅允许基于上下文已提供的表格数据进行计算，"
            "必须使用 print() 输出中间与最终结果；禁止网络、文件读写、系统操作。"
        ),
        args_schema=PythonExecArgs,
    )


def _chunk_reasoning(chunk: Any) -> str:
    """提取思考增量（llm_client 已把网关 ``reasoning_content`` 挂到 additional_kwargs）。"""
    kwargs = getattr(chunk, "additional_kwargs", None)
    if not isinstance(kwargs, dict):
        return ""
    value = kwargs.get("reasoning_content")
    return value if isinstance(value, str) else ""


def _usage_from_chunk(acc: AIMessageChunk | None) -> dict[str, int] | None:
    """聚合 chunk 的 usage_metadata → GenerationEvent.usage 契约键（prompt/completion/total）。

    stream_usage=True 后网关在流末尾 chunk 上带标准 usage_metadata；同轮多个 usage
    chunk 经 add_usage 累加，聚合结果即该轮 usage（标准协议只有最后一个 chunk 带 usage）。
    """
    usage = acc.usage_metadata if acc is not None else None
    if not isinstance(usage, dict) or not usage:
        return None
    mapped = {
        "prompt_tokens": usage.get("input_tokens"),
        "completion_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    return {key: int(value) for key, value in mapped.items() if value is not None} or None


def _collect_tool_calls(acc: AIMessageChunk | None) -> list[dict[str, Any]]:
    """聚合结果 → 统一调用列表；invalid_tool_calls 显式转失败标记。

    langchain-core 按 parse_partial_json 派生：可解析（含截断修复）→ ``tool_calls``
    （截断可能修复成空 dict）；仍非法 → ``invalid_tool_calls``。派生结果只有累积
    完成后才可信，所以只在整轮聚合结束后调用一次。
    """
    if acc is None:
        return []
    calls: list[dict[str, Any]] = []
    for invalid, chunks in ((False, acc.tool_calls), (True, acc.invalid_tool_calls)):
        for tc in chunks or []:
            calls.append(
                {
                    "name": tc.get("name") or "python_exec",
                    "args": {} if invalid else (tc.get("args") or {}),
                    "id": tc.get("id"),
                    "invalid": invalid,
                    "raw_args": tc.get("args") if invalid else None,
                }
            )
    for i, call in enumerate(calls):
        if not call["id"]:
            call["id"] = f"call_{i}"
    return calls


def _extract_code(args: Any) -> str:
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        for key in ("code", "input", "script", "source", "python"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for value in args.values():
            if isinstance(value, str) and value.strip():
                return value
    return ""


async def stream_generation(
    deps,
    *,
    system_prompt: str,
    user_query: str,
    cancel_check: Callable[[], bool] | None = None,
) -> AsyncIterator[GenerationEvent]:
    """流式生成，yield token/notice/cancelled/done 事件。

    工具循环上限 = settings.TOOL_MAX_ITERATIONS（按实际执行次数计）。
    """
    settings = deps.settings
    max_iterations = max(0, int(getattr(settings, "TOOL_MAX_ITERATIONS", 3) or 0))
    messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_query),
    ]
    usage: dict[str, int] | None = None
    tool_failed = False
    executions = 0
    # 达到上限后的收尾轮：只透传流（token/thinking/usage），忽略其间任何工具调用
    force_answer = False

    try:
        model = deps.llm.main_model(streaming=True, tools=[build_python_exec_tool(deps)])
    except Exception as exc:  # noqa: BLE001 - LLM 不可用由 orchestrator 映射
        logger.warning("main_model 初始化失败：%s", exc)
        yield GenerationEvent(kind="done", error=exc)
        return

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    while True:
        if _cancelled():
            yield GenerationEvent(kind="cancelled", usage=usage, tool_failed=tool_failed)
            return

        # 整轮聚合：AIMessageChunk.__add__ 合并 tool_call_chunks、累加 usage_metadata；
        # 「是否有工具调用」只在整轮聚合结束后判一次（中间碎片的派生结果不可信）
        acc: AIMessageChunk | None = None
        try:
            async for chunk in model.astream(messages):
                if _cancelled():
                    yield GenerationEvent(kind="cancelled", usage=usage, tool_failed=tool_failed)
                    return
                reasoning = _chunk_reasoning(chunk)
                if reasoning:
                    yield GenerationEvent(kind="thinking", content=reasoning)
                if chunk.text:
                    yield GenerationEvent(kind="token", content=chunk.text)
                acc = chunk if acc is None else acc + chunk
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 主 LLM 失败（收尾轮不发 notice，锁原行为）
            logger.warning("主 LLM %s生成失败：%s", "收尾" if force_answer else "流式", exc)
            if tool_failed and not force_answer:
                yield GenerationEvent(kind="notice", content=CALC_FALLBACK_NOTICE, tool_failed=True)
            yield GenerationEvent(kind="done", usage=usage, error=exc, tool_failed=tool_failed)
            return

        round_usage = _usage_from_chunk(acc)
        if round_usage:
            usage = round_usage
        calls = [] if force_answer else _collect_tool_calls(acc)
        if not calls:
            break

        if executions >= max_iterations:
            # 达到上限：不再执行工具，要求模型直接作答
            messages.append(HumanMessage(content=TOOL_LOOP_LIMIT_NOTICE))
            force_answer = True
            continue

        # 回灌 assistant tool_calls：invalid 以空参数入历史（下一条 ToolMessage 必须
        # 能对到 assistant 里的 tool_call id），失败原因由 ToolMessage 文本说明。
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {"name": call["name"], "args": call["args"], "id": call["id"]}
                    for call in calls
                ],
            )
        )
        for call in calls:
            call_id = call["id"]
            if executions >= max_iterations:
                messages.append(
                    ToolMessage(
                        content="已达到 python_exec 调用次数上限，该调用未执行。请直接给出最终回答。",
                        tool_call_id=call_id,
                    )
                )
                continue
            executions += 1
            if _cancelled():
                yield GenerationEvent(kind="cancelled", usage=usage, tool_failed=tool_failed)
                return
            if call["invalid"]:
                # 坏 JSON 显式失败：不执行（原手写 {"__raw__": ...} 分支的替代语义）
                tool_failed = True
                messages.append(
                    ToolMessage(
                        content=f"工具调用参数无法解析：{call['raw_args']}。{TOOL_FAIL_INSTRUCTION}",
                        tool_call_id=call_id,
                    )
                )
                continue
            code = _extract_code(call.get("args"))
            if not code:
                tool_failed = True
                messages.append(
                    ToolMessage(
                        content=f"工具调用参数缺失：未提供 code。{TOOL_FAIL_INSTRUCTION}",
                        tool_call_id=call_id,
                    )
                )
                continue
            try:
                result = await deps.executor.execute(code)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 工具异常按失败降级
                logger.warning("python_exec 执行异常：%s", exc)
                tool_failed = True
                messages.append(
                    ToolMessage(
                        content=f"工具执行异常：{exc}。{TOOL_FAIL_INSTRUCTION}",
                        tool_call_id=call_id,
                    )
                )
                continue
            logger.info(
                "python_exec 工具调用: ok=%s code_len=%d output_len=%d",
                bool(getattr(result, "ok", False)),
                len(code),
                len(str(getattr(result, "output", "") or "")),
            )
            if _cancelled():
                yield GenerationEvent(kind="cancelled", usage=usage, tool_failed=tool_failed)
                return
            if getattr(result, "ok", False):
                output = str(getattr(result, "output", "") or "").strip()
                messages.append(
                    ToolMessage(content=output or "(无输出)", tool_call_id=call_id)
                )
            else:
                tool_failed = True
                error = str(getattr(result, "error", "") or "未知错误")
                logger.warning("python_exec 执行失败：%s", error)
                messages.append(
                    ToolMessage(
                        content=f"工具执行失败：{error}。{TOOL_FAIL_INSTRUCTION}",
                        tool_call_id=call_id,
                    )
                )

    if tool_failed:
        yield GenerationEvent(kind="notice", content=CALC_FALLBACK_NOTICE, tool_failed=True)
    yield GenerationEvent(kind="done", usage=usage, tool_failed=tool_failed)


# ---------------------------------------------------------------------------
# 来源页脚
# ---------------------------------------------------------------------------
def build_footer(doc_sources: list[str] | None, web_results: list[Any] | None) -> str:
    """按实际命中生成两段式页脚；无来源/对应段为空则不生成对应段。"""
    names: list[str] = []
    for source in doc_sources or []:
        name = str(source or "").strip()
        if name and name not in names:
            names.append(name)

    websites: list[tuple[str, str]] = []
    seen_web: set[str] = set()
    for item in web_results or []:
        title = str(getattr(item, "title", "") or "").strip()
        url = str(getattr(item, "url", "") or "").strip()
        if not title and not url:
            continue
        key = url or title
        if key in seen_web:
            continue
        seen_web.add(key)
        websites.append((title or url, url))

    sections: list[str] = []
    if names:
        lines = ["**参考文档**"]
        lines.extend(f"{i}. {name}" for i, name in enumerate(names, start=1))
        sections.append("\n".join(lines))
    if websites:
        lines = ["**参考网页**"]
        for i, (title, url) in enumerate(websites, start=1):
            lines.append(f"{i}. [{title}]({url})" if url else f"{i}. {title}")
        sections.append("\n".join(lines))

    if not sections:
        return ""
    return "\n\n---\n" + "\n".join(sections)
