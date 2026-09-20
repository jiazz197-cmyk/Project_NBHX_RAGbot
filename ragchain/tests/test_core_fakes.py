"""core 单测共享 fake（不属于任何真实外部依赖，禁止网络/LLM）。

文件名以 test_core_ 开头只是为了让 pytest 正常导入；本文件不包含测试用例。
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable

from langchain_core.messages import AIMessageChunk

from app.steps.injection_guard import GuardResult
from app.steps.intent_router import IntentResult
from app.steps.query_rewriter import RewriteResult


# ---------------------------------------------------------------------------
# SSE 工具
# ---------------------------------------------------------------------------
def fake_sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def parse_frames(frames: list[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for frame in frames:
        match = re.match(r"^event: ([^\n]+)\ndata: (.*)\n\n$", frame, re.DOTALL)
        assert match, f"非法 SSE 帧: {frame!r}"
        payload = json.loads(match.group(2))
        assert payload["event"] == match.group(1)
        events.append(payload)
    return events


def event_names(frames: list[str]) -> list[str]:
    return [e["event"] for e in parse_frames(frames)]


# ---------------------------------------------------------------------------
# Settings / Registry
# ---------------------------------------------------------------------------
class FakeSettings:
    def __init__(self, **overrides: Any):
        self.RAGCHAIN_GUARD_STRICT = False
        self.DOC_COLLECTION = "knowledge_chunks"
        self.EXCEL_COLLECTION = "excel_db_chunks"
        self.RAG_RETRIEVE_TOP_K = 10
        self.RAG_RERANK_TOP_N = 5
        self.SEARCH_RESULT_COUNT = 5
        self.MEMORY_RECENT_TURNS = 10
        self.MEMORY_COMPRESS_THRESHOLD = 20
        self.MEMORY_COMPRESS_N_RECENT = 5
        self.EXCEL_CONTEXT_MAX_CHARS = 8000
        self.TOOL_MAX_ITERATIONS = 3
        self.TOOL_CODE_MAX_CHARS = 8000
        self.TOOL_OUTPUT_MAX_CHARS = 4000
        for key, value in overrides.items():
            setattr(self, key, value)


@dataclass
class FakeAuth:
    user_id: str = "00000000-0000-0000-0000-0000000000aa"
    token: str = "fake.jwt.token"
    claims: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeRequest:
    query: str = "去年售后费用趋势如何？"
    conversation_id: str | None = None
    search_mode: str = "本地&网络"
    inputs: dict[str, Any] = field(default_factory=dict)
    response_mode: str = "streaming"


class FakeRegistry:
    def __init__(self) -> None:
        self.tasks: dict[str, SimpleNamespace] = {}
        self._counter = 0

    def register(self, user_id: str, conversation_id: str = ""):
        self._counter += 1
        entry = SimpleNamespace(
            task_id=f"task-{self._counter}",
            user_id=user_id,
            conversation_id=conversation_id or "",
            cancel_event=asyncio.Event(),
            finished=False,
            result="running",
        )
        self.tasks[entry.task_id] = entry
        return entry

    def update_conversation(self, task_id: str, conversation_id: str) -> None:
        entry = self.tasks.get(task_id)
        if entry is not None:
            entry.conversation_id = conversation_id

    def get(self, task_id: str):
        return self.tasks.get(task_id)

    def latest(self):
        return list(self.tasks.values())[-1]

    def finish(self, task_id: str, result: str) -> None:
        entry = self.tasks.get(task_id)
        if entry is not None:
            entry.finished = True
            entry.result = result


# ---------------------------------------------------------------------------
# 外部客户端 fake
# ---------------------------------------------------------------------------
class FakeChainError(Exception):
    def __init__(self, code: str = "BACKEND_ERROR", message: str = "boom", status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class FakeBackend:
    def __init__(
        self,
        *,
        create_response: dict | None = None,
        messages: list[dict] | None = None,
        summary: str | None = None,
        compressed: str | None = None,
    ) -> None:
        self.create_response = create_response if create_response is not None else {"id": "conv-1"}
        self.messages = list(messages or [])
        self.summary = summary or ""
        self.compressed = compressed or ""
        self.create_calls: list[tuple[str, str, dict]] = []
        self.append_calls: list[tuple[str, str, list[dict]]] = []
        self.create_error: BaseException | None = None
        self.messages_error: BaseException | None = None
        self.summary_error: BaseException | None = None
        self.compress_error: BaseException | None = None
        self.append_error: BaseException | None = None
        self.compress_calls: list[tuple[str, str, str, int]] = []

    async def create_conversation(self, token: str, name: str, inputs: dict):
        self.create_calls.append((token, name, dict(inputs)))
        if self.create_error is not None:
            raise self.create_error
        return dict(self.create_response)

    async def get_messages(self, token: str, conversation_id: str, page: int = 1, limit: int = 10):
        if self.messages_error is not None:
            raise self.messages_error
        return list(self.messages)[:limit]

    async def get_latest_summary(self, token: str, user_id: str):
        if self.summary_error is not None:
            raise self.summary_error
        return self.summary or None

    async def compress_context(self, token: str, user_id: str, conversation_id: str, n_recent: int):
        self.compress_calls.append((token, user_id, conversation_id, n_recent))
        if self.compress_error is not None:
            raise self.compress_error
        return self.compressed or None

    async def append_messages(self, token: str, conversation_id: str, messages: list[dict]):
        self.append_calls.append((token, conversation_id, list(messages)))
        if self.append_error is not None:
            raise self.append_error
        return {"stored": len(messages)}


class FakeRetriever:
    def __init__(
        self,
        *,
        db_result: Any = None,
        excel_result: Any = None,
        db_error: BaseException | None = None,
        excel_error: BaseException | None = None,
    ) -> None:
        self.db_result = db_result if db_result is not None else {"chunks": []}
        self.excel_result = excel_result if excel_result is not None else {"answer": "", "sources": []}
        self.db_error = db_error
        self.excel_error = excel_error
        self.db_calls: list[dict] = []
        self.excel_calls: list[dict] = []

    async def query_db(self, token, collection, question, top_k, rerank: bool = False):
        self.db_calls.append(
            {"token": token, "collection": collection, "question": question, "top_k": top_k, "rerank": rerank}
        )
        if self.db_error is not None:
            raise self.db_error
        return self.db_result

    async def query_excel(self, token, collection, question, top_k):
        self.excel_calls.append(
            {"token": token, "collection": collection, "question": question, "top_k": top_k}
        )
        if self.excel_error is not None:
            raise self.excel_error
        return self.excel_result


class FakeReranker:
    def __init__(self, ranking: Any = None, error: BaseException | None = None) -> None:
        self.ranking = ranking if ranking is not None else []
        self.error = error
        self.calls: list[dict] = []

    async def rerank(self, query: str, documents: list[str], top_n: int):
        self.calls.append({"query": query, "documents": list(documents), "top_n": top_n})
        if self.error is not None:
            raise self.error
        return list(self.ranking)


class FakeSearch:
    def __init__(self, results: list[Any] | None = None, error: BaseException | None = None) -> None:
        self.results = list(results or [])
        self.error = error
        self.calls: list[dict] = []

    async def search(self, query: str, count: int = 5):
        self.calls.append({"query": query, "count": count})
        if self.error is not None:
            raise self.error
        return list(self.results)


@dataclass
class FakeToolResult:
    ok: bool = True
    output: str = ""
    error: str = ""
    duration_sec: float = 0.0


class FakeExecutor:
    def __init__(
        self,
        results: list[Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.results = list(results or [])
        self.error = error
        self.codes: list[str] = []

    async def execute(self, code: str):
        self.codes.append(code)
        if self.error is not None:
            raise self.error
        if self.results:
            item = self.results.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, dict):
                return FakeToolResult(**item)
            return item
        return FakeToolResult(ok=True, output=f"执行结果: {code}")


# ---------------------------------------------------------------------------
# LLM fake
# ---------------------------------------------------------------------------
def FakeChunk(
    *,
    content: str = "",
    tool_calls: list | None = None,
    tool_call_chunks: list | None = None,
    usage_metadata: dict | None = None,
    response_metadata: dict | None = None,
    reasoning: str = "",
    additional_kwargs: dict | None = None,
) -> "AIMessageChunk":
    """构造真实 ``AIMessageChunk``。

    generate 已改用 chunk 相加聚合（issue #30，手写 ToolCallAccumulator 已删），
    脚本 chunk 必须携带 langchain-core 原生语义（``__add__`` / ``.text`` /
    ``tool_calls`` / ``invalid_tool_calls`` / ``usage_metadata``），不能再是手写
    dataclass。

    ``reasoning`` 模拟 llm_client 的思考透出：挂到 ``additional_kwargs["reasoning_content"]``。
    """
    kwargs: dict[str, Any] = dict(additional_kwargs or {})
    if reasoning:
        kwargs["reasoning_content"] = reasoning
    payload: dict[str, Any] = {"content": content, "additional_kwargs": kwargs}
    if tool_calls:
        payload["tool_calls"] = tool_calls
    if tool_call_chunks:
        payload["tool_call_chunks"] = tool_call_chunks
    if usage_metadata is not None:
        payload["usage_metadata"] = usage_metadata
    if response_metadata is not None:
        payload["response_metadata"] = response_metadata
    return AIMessageChunk(**payload)


class ScriptedModel:
    """按脚本逐次 astream 的假模型；脚本项为 chunk 列表或异常。"""

    def __init__(self, scripts: list[Any] | None = None, on_before_script: Callable[[int], None] | None = None):
        self.scripts = list(scripts or [])
        self.on_before_script = on_before_script
        self.calls: list[list[Any]] = []

    async def astream(self, messages):
        self.calls.append(list(messages))
        index = len(self.calls) - 1
        if self.on_before_script:
            self.on_before_script(index)
        if index >= len(self.scripts):
            return
        script = self.scripts[index]
        if isinstance(script, BaseException):
            raise script
        if callable(script):
            script = script()
        for item in script:
            if callable(item):
                item = item()
            if isinstance(item, BaseException):
                raise item
            yield item


class FakeLLM:
    def __init__(self, settings: FakeSettings | None = None) -> None:
        self.settings = settings or FakeSettings()
        self.responses: dict[str, Any] = {}
        self.defaults: dict[str, Any] = {
            "GuardResult": GuardResult(is_malicious=False, reason="正常财务咨询"),
            "RewriteResult": RewriteResult(rewritten_query="改写后的问题", keywords=["费用"], time_range="2025 年"),
            "IntentResult": IntentResult(intent="both", reason="混合问题"),
        }
        self.structured_error: BaseException | None = None
        self.structured_returns_none: bool = False
        self.structured_calls: list[dict] = []
        self.main_scripts: list[Any] = []
        self.on_before_script: Callable[[int], None] | None = None
        self.models: list[ScriptedModel] = []
        self.main_kwargs: dict | None = None

    async def structured(self, *, system: str, user: str, schema_cls):
        self.structured_calls.append({"system": system, "user": user, "schema": schema_cls.__name__})
        if self.structured_error is not None:
            raise self.structured_error
        if self.structured_returns_none:
            # 模拟 with_structured_output 静默返回 None（function_calling + 网关
            # 忽略 tool_choice 的场景，issue #31 坑 #2）。
            return None
        response = self.responses.get(schema_cls.__name__, self.defaults.get(schema_cls.__name__))
        if response is None:
            raise RuntimeError(f"FakeLLM 未提供 {schema_cls.__name__} 响应")
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(system=system, user=user)
        return response

    def main_model(self, *, streaming: bool = True, tools: list | None = None):
        self.main_kwargs = {"streaming": streaming, "tools": tools}
        model = ScriptedModel(self.main_scripts, on_before_script=self.on_before_script)
        self.models.append(model)
        return model


# ---------------------------------------------------------------------------
# 依赖容器
# ---------------------------------------------------------------------------
class FakeDeps:
    def __init__(self, settings: FakeSettings | None = None) -> None:
        self.settings = settings or FakeSettings()
        self.backend = FakeBackend()
        self.retriever = FakeRetriever()
        self.reranker = FakeReranker()
        self.search = FakeSearch()
        self.llm = FakeLLM(self.settings)
        self.executor = FakeExecutor()
        self.registry = FakeRegistry()
        self.sse_frame = fake_sse_frame


def build_fake_deps(**attrs: Any) -> FakeDeps:
    deps = FakeDeps(settings=attrs.pop("settings", None))
    for key, value in attrs.items():
        setattr(deps, key, value)
    if getattr(deps, "llm", None) is not None:
        deps.llm.settings = deps.settings
    return deps


def make_text_script(*texts: str, usage: dict | None = None) -> list[FakeChunk]:
    chunks = [FakeChunk(content=text) for text in texts]
    if usage and chunks:
        chunks[-1] = FakeChunk(content=chunks[-1].content, usage_metadata=usage)
    elif usage:
        chunks = [FakeChunk(content="", usage_metadata=usage)]
    return chunks
