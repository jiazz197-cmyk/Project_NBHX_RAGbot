"""主生成步骤：main_llm 流式 + 手写有界 python_exec 工具循环；来源页脚生成。

不引入 langgraph/agent 框架；工具调用通过汇总 AIMessageChunk.tool_call_chunks 后
自行交给 deps.executor.execute 执行，再以 ToolMessage 回灌。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
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
    kind: str  # token | notice | cancelled | done
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


def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    return ""


def _extract_usage(chunk: Any) -> dict[str, int] | None:
    usage = getattr(chunk, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
        completion = usage.get("output_tokens", usage.get("completion_tokens"))
        total = usage.get("total_tokens")
        normalized: dict[str, int] = {}
        if prompt is not None:
            normalized["prompt_tokens"] = int(prompt)
        if completion is not None:
            normalized["completion_tokens"] = int(completion)
        if total is not None:
            normalized["total_tokens"] = int(total)
        if normalized:
            return normalized
    meta = getattr(chunk, "response_metadata", None)
    if isinstance(meta, dict):
        raw = meta.get("token_usage") or meta.get("usage")
        if isinstance(raw, dict) and raw:
            normalized = {
                key: int(raw[key])
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if raw.get(key) is not None
            }
            if normalized:
                return normalized
    return None


def _normalize_call(tc: Any) -> dict[str, Any]:
    if isinstance(tc, dict):
        return {
            "name": str(tc.get("name") or ""),
            "args": tc.get("args"),
            "id": str(tc.get("id") or ""),
        }
    return {
        "name": str(getattr(tc, "name", "") or ""),
        "args": getattr(tc, "args", None),
        "id": str(getattr(tc, "id", "") or ""),
    }


class ToolCallAccumulator:
    """汇总流式 chunk 中的 tool_call_chunks（兼容完整 tool_calls）。"""

    def __init__(self) -> None:
        self._fragments: dict[int, dict[str, str]] = {}
        self._direct: list[dict[str, Any]] = []
        self._direct_keys: set[str] = set()

    def add(self, chunk: Any) -> None:
        raw_chunks = getattr(chunk, "tool_call_chunks", None)
        if raw_chunks:
            for item in raw_chunks:
                tc = _normalize_call(item)
                index = item.get("index", 0) if isinstance(item, dict) else getattr(item, "index", 0)
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    index = 0
                entry = self._fragments.setdefault(index, {"name": "", "args": "", "id": ""})
                if tc["name"]:
                    entry["name"] = tc["name"]
                if tc["id"]:
                    entry["id"] = tc["id"]
                args = tc["args"]
                if isinstance(args, str):
                    entry["args"] += args
                elif args is not None:
                    try:
                        entry["args"] = json.dumps(args, ensure_ascii=False)
                    except (TypeError, ValueError):
                        entry["args"] = str(args)
            return

        direct = getattr(chunk, "tool_calls", None)
        if direct:
            for i, item in enumerate(direct):
                tc = _normalize_call(item)
                key = tc["id"] or f"direct-{i}"
                if key in self._direct_keys:
                    continue
                self._direct_keys.add(key)
                self._direct.append(tc)

    def finalize(self) -> list[dict[str, Any]]:
        if self._direct:
            return [
                {"name": c["name"], "args": c["args"], "id": c["id"] or f"call_{i}"}
                for i, c in enumerate(self._direct)
            ]

        calls: list[dict[str, Any]] = []
        for index in sorted(self._fragments):
            fragment = self._fragments[index]
            raw_args = fragment["args"]
            if isinstance(raw_args, str):
                raw_args = raw_args.strip()
                if not raw_args:
                    args: Any = {}
                else:
                    try:
                        args = json.loads(raw_args)
                    except (TypeError, ValueError):
                        args = {"__raw__": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            calls.append(
                {
                    "name": fragment["name"] or "python_exec",
                    "args": args,
                    "id": fragment["id"] or f"call_{index}",
                }
            )
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

        accumulator = ToolCallAccumulator()
        try:
            async for chunk in model.astream(messages):
                if _cancelled():
                    yield GenerationEvent(kind="cancelled", usage=usage, tool_failed=tool_failed)
                    return
                text = _chunk_text(chunk)
                if text:
                    yield GenerationEvent(kind="token", content=text)
                chunk_usage = _extract_usage(chunk)
                if chunk_usage:
                    usage = chunk_usage
                accumulator.add(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 主 LLM 失败
            logger.warning("主 LLM 流式生成失败：%s", exc)
            if tool_failed:
                yield GenerationEvent(kind="notice", content=CALC_FALLBACK_NOTICE, tool_failed=True)
            yield GenerationEvent(kind="done", usage=usage, error=exc, tool_failed=tool_failed)
            return

        calls = accumulator.finalize()
        if not calls:
            break

        if executions >= max_iterations:
            # 达到上限：不再执行工具，要求模型直接作答；再流一次，忽略其间任何工具调用
            messages.append(HumanMessage(content=TOOL_LOOP_LIMIT_NOTICE))
            try:
                async for chunk in model.astream(messages):
                    if _cancelled():
                        yield GenerationEvent(kind="cancelled", usage=usage, tool_failed=tool_failed)
                        return
                    text = _chunk_text(chunk)
                    if text:
                        yield GenerationEvent(kind="token", content=text)
                    chunk_usage = _extract_usage(chunk)
                    if chunk_usage:
                        usage = chunk_usage
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("主 LLM 收尾生成失败：%s", exc)
                yield GenerationEvent(kind="done", usage=usage, error=exc, tool_failed=tool_failed)
                return
            break

        messages.append(AIMessage(content="", tool_calls=calls))
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
