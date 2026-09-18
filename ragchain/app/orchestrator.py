"""核心链编排：SSE 事件流、记忆加载、防注入、改写、意图、检索、主生成、落库。

设计约束：
- run_chat 为异步生成器，yield 完整 SSE 帧字符串；
- 业务模块（schemas/security/deps/sse/errors）以鸭子类型使用，便于 core 单测脱离平台层；
- 生产环境通过 app.deps.AppDeps 注入全部外部依赖，无真实网络/LLM 的隐式调用。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing
from typing import TYPE_CHECKING, Any, AsyncIterator

from .steps.generate import build_footer, stream_generation
from .steps.injection_guard import check_injection
from .steps.intent_router import route_intent
from .steps.memory import MemoryContext, load_memory
from .steps.query_rewriter import rewrite_query
from .steps.retrieval import LocalRetrievalResult, build_query, retrieve_local
from .steps.web_search import search_web

if TYPE_CHECKING:  # pragma: no cover
    from .deps import AppDeps
    from .schemas import ChatMessageRequest
    from .security import AuthContext

logger = logging.getLogger(__name__)

SEARCH_MODE_WEB = "联网搜索"
SEARCH_MODE_LOCAL = "本地检索"
SEARCH_MODE_BOTH = "本地&网络"

# 前端思考协议（MessageItem.splitAssistantContent）：思考内容以 <think> 开头、
# </think> 闭合，内嵌在 message 帧的 content 里随流下发；<think> 未闭合时前端
# 实时展示思考，闭合后自动切换到答案流并折叠思考区（仍可手动展开）。
THINK_OPEN_TAG = "<think>"
THINK_CLOSE_TAG = "</think>"

FRIENDLY_INJECTION_MESSAGE = "抱歉，您的请求包含可能危及系统安全的内容，已被拒绝。请调整后重新提问。"
FRIENDLY_LLM_MESSAGE = "模型服务暂不可用，请稍后重试。"
GENERAL_NOTE = "\n\n（本回答未参考内部财务资料）"


class _CancelledFlow(Exception):
    """内部信号：检测到 stop 取消。"""


class _LLMFlowError(Exception):
    """内部信号：主 LLM 生成失败。"""

    def __init__(self, cause: BaseException | None = None):
        super().__init__(str(cause) if cause else "LLM unavailable")
        self.cause = cause


class _FlowError(Exception):
    def __init__(self, code: str, message: str, status: int = 500):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# SSE / registry 小工具
# ---------------------------------------------------------------------------
def _get_sse_frame(deps):
    frame = getattr(deps, "sse_frame", None)
    if callable(frame):
        return frame
    from .sse import sse_frame as real_frame

    return real_frame


def _frame(deps, event: str, data: dict[str, Any]) -> str:
    return _get_sse_frame(deps)(event, data)


def _message_frame(deps, task_id: str, conversation_id: str, content: str) -> str:
    return _frame(
        deps,
        "message",
        {
            "event": "message",
            "task_id": task_id,
            "conversation_id": conversation_id,
            "content": content,
        },
    )


def _error_frame(deps, task_id: str, conversation_id: str, code: str, message: str, status: int) -> str:
    return _frame(
        deps,
        "error",
        {
            "event": "error",
            "task_id": task_id,
            "conversation_id": conversation_id,
            "content": "",
            "error": {"code": code, "message": message, "status": status},
        },
    )


def _end_frame(
    deps,
    task_id: str,
    conversation_id: str,
    *,
    content: str = "",
    usage: dict[str, int] | None = None,
) -> str:
    data: dict[str, Any] = {
        "event": "message_end",
        "task_id": task_id,
        "conversation_id": conversation_id,
        "content": content,
    }
    if usage:
        data["usage"] = usage
    return _frame(deps, "message_end", data)


def _is_cancelled(deps, task_id: str) -> bool:
    entry = deps.registry.get(task_id)
    if entry is None:
        return False
    event = getattr(entry, "cancel_event", None)
    try:
        return bool(event is not None and event.is_set())
    except Exception:  # noqa: BLE001 - 注册表实现异常不阻断
        return False


def _ensure_not_cancelled(deps, task_id: str) -> None:
    if _is_cancelled(deps, task_id):
        raise _CancelledFlow()


# ---------------------------------------------------------------------------
# 落库 payload
# ---------------------------------------------------------------------------
def _collect_sources(local: LocalRetrievalResult, web_results: list[Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(item: dict[str, Any], key: str) -> None:
        value = str(item.get(key) or "").strip()
        dedupe = (str(item.get("type")), value)
        if value and dedupe not in seen:
            seen.add(dedupe)
            sources.append(item)

    for document in local.documents:
        _add({"type": "document", "name": document.source}, "name")
    for name in local.excel_sources:
        _add({"type": "excel", "name": str(name)}, "name")
    for item in web_results or []:
        title = str(getattr(item, "title", "") or "").strip()
        url = str(getattr(item, "url", "") or "").strip()
        key = url or title
        if key and ("web", key) not in seen:
            seen.add(("web", key))
            sources.append({"type": "web", "title": title, "url": url})
    return sources


async def _safe_persist(deps, token: str, conversation_id: str, messages: list[dict[str, Any]]) -> None:
    try:
        await deps.backend.append_messages(token, conversation_id, messages)
    except Exception as exc:  # noqa: BLE001 - 落库失败只告警，不改变已发出流
        logger.warning("消息落库失败（不影响已输出内容）：%s", exc)


def _user_message(query: str, task_id: str, search_mode: str, *, rejected: str | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"task_id": task_id, "search_mode": search_mode}
    if rejected:
        metadata["rejected"] = rejected
    return {"role": "user", "content": query, "query": query, "metadata": metadata}


def _assistant_message(
    content: str,
    *,
    task_id: str,
    usage: dict[str, int] | None,
    sources: list[dict[str, Any]],
    intent: str,
    rewritten_query: str,
    status: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "task_id": task_id,
        "sources": sources,
        "intent": intent,
        "rewritten_query": rewritten_query,
    }
    if usage:
        metadata["usage"] = usage
    if status:
        metadata["status"] = status
    return {"role": "assistant", "content": content, "answer": content, "metadata": metadata}


def _map_exception(exc: BaseException) -> tuple[str, str, int]:
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    message = getattr(exc, "message", None)
    if isinstance(exc, _FlowError):
        return exc.code, exc.message, exc.status
    if exc.__class__.__name__.find("LLM") >= 0:
        return "LLM_UNAVAILABLE", FRIENDLY_LLM_MESSAGE, 503
    if code:
        try:
            status_int = int(status) if status is not None else 500
        except (TypeError, ValueError):
            status_int = 500
        return str(code), str(message or "请求处理失败"), status_int
    return "INTERNAL_ERROR", "服务内部错误，请稍后重试。", 500


def _finish_body(
    body_parts: list[str],
    intent: str,
    local: LocalRetrievalResult,
    web_results: list[Any],
) -> str:
    parts = list(body_parts)
    text = "".join(parts)
    if intent == "general" and "本回答未参考内部财务资料" not in text:
        parts.append(GENERAL_NOTE)
    footer = build_footer(local.doc_sources + local.excel_sources, web_results)
    if footer and footer not in text:
        parts.append(footer)
    return "".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def run_chat(request: "ChatMessageRequest", auth: "AuthContext", deps: "AppDeps") -> AsyncIterator[str]:
    registry = deps.registry
    query = str(getattr(request, "query", "") or "").strip()
    search_mode = str(getattr(request, "search_mode", SEARCH_MODE_BOTH) or SEARCH_MODE_BOTH)
    requested_conversation_id = str(getattr(request, "conversation_id", "") or "").strip()

    entry = registry.register(auth.user_id, requested_conversation_id)
    task_id = entry.task_id
    conversation_id = requested_conversation_id

    body_parts: list[str] = []
    usage: dict[str, int] | None = None
    intent = "both"
    rewritten_query = query
    local = LocalRetrievalResult()
    web_results: list[Any] = []
    memory = MemoryContext()
    # 思考块状态：open=思考块已开启未闭合；closed=已闭合（此后 thinking 不再下发）。
    # 前端只支持单个思考块：多轮工具循环的思考增量合并进同一个块，首个可见回答时闭合。
    thought_open = False
    thought_closed = False

    try:
        # 1) 建会话（JWT/search_mode 已由 server 校验）
        if not conversation_id:
            created = await deps.backend.create_conversation(
                auth.token,
                query[:20],
                {"search_mode": search_mode},
            )
            if not isinstance(created, dict):
                created = {}
            conversation_id = str(created.get("id") or created.get("conversation_id") or "")
            if not conversation_id:
                raise _FlowError("INTERNAL_ERROR", "创建会话失败：响应缺少会话 ID", 500)
            registry.update_conversation(task_id, conversation_id)

        _ensure_not_cancelled(deps, task_id)

        # 2) SSE 开流：首帧带 task_id + conversation_id
        yield _message_frame(deps, task_id, conversation_id, "")

        # 3) 记忆加载（并行，单点失败降级）
        memory = await load_memory(auth, deps, conversation_id)
        _ensure_not_cancelled(deps, task_id)

        # 4) 防注入
        guard = await check_injection(query, deps)
        _ensure_not_cancelled(deps, task_id)
        if guard.is_malicious:
            await _safe_persist(
                deps,
                auth.token,
                conversation_id,
                [_user_message(query, task_id, search_mode, rejected="injection")],
            )
            yield _error_frame(deps, task_id, conversation_id, "QUERY_REJECTED", FRIENDLY_INJECTION_MESSAGE, 400)
            yield _end_frame(deps, task_id, conversation_id, content="")
            registry.finish(task_id, "failed")
            return

        # 5) query 改写
        rewrite = await rewrite_query(query, memory.recent_messages, deps)
        _ensure_not_cancelled(deps, task_id)
        rewritten_query = rewrite.rewritten_query or query
        retrieval_query = build_query(rewritten_query, rewrite.keywords)

        # 6) 意图识别
        intent_result = await route_intent(retrieval_query, deps)
        _ensure_not_cancelled(deps, task_id)
        intent = intent_result.intent

        # 7) 本地检索（intent≠general；doc/excel 并行，全失败带空资料继续）
        local = await retrieve_local(
            intent=intent,
            rewritten_query=rewritten_query,
            keywords=rewrite.keywords,
            token=auth.token,
            deps=deps,
        )
        _ensure_not_cancelled(deps, task_id)

        # 8) 联网搜索（search_mode 含联网时）
        web_results = await search_web(
            query=retrieval_query,
            enabled=search_mode in (SEARCH_MODE_WEB, SEARCH_MODE_BOTH),
            deps=deps,
        )
        _ensure_not_cancelled(deps, task_id)

        # 9) 组装 system prompt + 主生成（有界 python_exec 工具循环，流式）
        system_prompt = _build_system_prompt(
            intent=intent,
            local=local,
            rewrite=rewrite,
            memory=memory,
            web_results=web_results,
        )
        generation = stream_generation(
            deps,
            system_prompt=system_prompt,
            user_query=query,
            cancel_check=lambda: _is_cancelled(deps, task_id),
        )
        async with aclosing(generation):
            async for event in generation:
                _ensure_not_cancelled(deps, task_id)
                if event.kind in ("token", "notice"):
                    prefix = ""
                    if thought_open:
                        # 首个可见回答内容：闭合思考块，前端据此切到答案流并折叠思考
                        prefix = THINK_CLOSE_TAG
                        thought_open = False
                        thought_closed = True
                    payload = prefix + event.content
                    body_parts.append(payload)
                    yield _message_frame(deps, task_id, conversation_id, payload)
                elif event.kind == "thinking":
                    if thought_closed or not event.content:
                        # 答案已开始后到达的后续思考不再下发（前端只支持单个思考块）
                        continue
                    prefix = "" if thought_open else THINK_OPEN_TAG
                    thought_open = True
                    payload = prefix + event.content
                    body_parts.append(payload)
                    yield _message_frame(deps, task_id, conversation_id, payload)
                elif event.kind == "cancelled":
                    raise _CancelledFlow()
                elif event.kind == "done":
                    usage = event.usage or usage
                    if event.error is not None:
                        raise _LLMFlowError(event.error)
        _ensure_not_cancelled(deps, task_id)

        if thought_open:
            # 模型只产生了思考、没有任何可见回答：补闭合标签，保证流出/落库内容成对
            body_parts.append(THINK_CLOSE_TAG)
            yield _message_frame(deps, task_id, conversation_id, THINK_CLOSE_TAG)

        # 10) general 注明 + 来源页脚（独立 message 帧）
        if intent == "general":
            current = "".join(body_parts)
            if "本回答未参考内部财务资料" not in current:
                body_parts.append(GENERAL_NOTE)
                yield _message_frame(deps, task_id, conversation_id, GENERAL_NOTE)

        footer = build_footer(local.doc_sources + local.excel_sources, web_results)
        if footer:
            body_parts.append(footer)
            yield _message_frame(deps, task_id, conversation_id, footer)

        # 11) message_end → 落库
        body_text = "".join(body_parts)
        sources = _collect_sources(local, web_results)
        _ensure_not_cancelled(deps, task_id)
        await _safe_persist(
            deps,
            auth.token,
            conversation_id,
            [
                _user_message(query, task_id, search_mode),
                _assistant_message(
                    body_text,
                    task_id=task_id,
                    usage=usage,
                    sources=sources,
                    intent=intent,
                    rewritten_query=rewritten_query,
                    status=None,
                ),
            ],
        )
        yield _end_frame(deps, task_id, conversation_id, usage=usage)
        registry.finish(task_id, "success")
        return

    except _CancelledFlow:
        if thought_open:
            # 取消时补闭合标签：既流出（前端累积内容成对）也落库
            thought_open = False
            body_parts.append(THINK_CLOSE_TAG)
            yield _message_frame(deps, task_id, conversation_id, THINK_CLOSE_TAG)
        body_text = _finish_body(body_parts, intent, local, web_results)
        sources = _collect_sources(local, web_results)
        await _safe_persist(
            deps,
            auth.token,
            conversation_id,
            [
                _user_message(query, task_id, search_mode),
                _assistant_message(
                    body_text,
                    task_id=task_id,
                    usage=usage,
                    sources=sources,
                    intent=intent,
                    rewritten_query=rewritten_query,
                    status="cancelled",
                ),
            ],
        )
        yield _end_frame(deps, task_id, conversation_id, content=body_text, usage=usage)
        registry.finish(task_id, "cancelled")
        return

    except _LLMFlowError as exc:
        logger.warning("主 LLM 失败：%s", exc.cause)
        yield _error_frame(deps, task_id, conversation_id, "LLM_UNAVAILABLE", FRIENDLY_LLM_MESSAGE, 503)
        yield _end_frame(deps, task_id, conversation_id, content="")
        registry.finish(task_id, "failed")
        return

    except Exception as exc:  # noqa: BLE001 - 统一映射为 SSE error + message_end
        if isinstance(exc, asyncio.CancelledError):
            raise
        code, message, status = _map_exception(exc)
        logger.exception("核心链未预期异常：%s", exc)
        yield _error_frame(deps, task_id, conversation_id, code, message, status)
        yield _end_frame(deps, task_id, conversation_id, content="")
        registry.finish(task_id, "failed")
        return

    finally:
        # 客户端断连/生成器被提前关闭时兜底结束注册表任务（正常路径均已显式 finish）
        try:
            current_entry = registry.get(task_id)
            if current_entry is not None and not getattr(current_entry, "finished", False):
                registry.finish(task_id, "failed")
        except Exception:  # noqa: BLE001 - 兜底清理不抛出
            pass


def _build_system_prompt(
    *,
    intent: str,
    local: LocalRetrievalResult,
    rewrite: Any,
    memory: MemoryContext,
    web_results: list[Any],
) -> str:
    from .prompts import build_main_system_prompt

    return build_main_system_prompt(
        intent=intent,
        doc_chunks=local.documents,
        excel_answer=local.excel_answer,
        excel_sources=local.excel_sources,
        time_range=getattr(rewrite, "time_range", "") or "",
        web_results=web_results,
        compressed_context=memory.compressed_context,
        profile_summary=memory.profile_summary,
        recent_messages=memory.recent_messages,
        retrieval_available=local.available,
        rewritten_query=getattr(rewrite, "rewritten_query", "") or "",
        keywords=getattr(rewrite, "keywords", []) or [],
    )
