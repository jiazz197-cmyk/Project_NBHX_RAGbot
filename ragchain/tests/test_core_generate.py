"""主生成（有界工具循环）单测：fake LLM + fake executor，禁止真实外部调用。"""
from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from app.steps.generate import (
    CALC_FALLBACK_NOTICE,
    GenerationEvent,
    build_python_exec_tool,
    build_footer,
    stream_generation,
)

from tests.test_core_fakes import (
    FakeChunk,
    FakeExecutor,
    FakeSettings,
    FakeToolResult,
    make_text_script,
    build_fake_deps,
)


async def _collect(gen):
    return [event async for event in gen]


async def test_stream_tokens_usage_and_tool_binding():
    deps = build_fake_deps()
    deps.llm.main_scripts = [
        make_text_script("结论：", "费用为 42 元。", usage={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14})
    ]
    events = await _collect(
        stream_generation(deps, system_prompt="系统提示", user_query="费用是多少？")
    )
    assert [e.kind for e in events] == ["token", "token", "done"]
    assert "".join(e.content for e in events if e.kind == "token") == "结论：费用为 42 元。"
    done = events[-1]
    assert done.error is None
    assert done.usage == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    # main_model 绑定了 python_exec 工具，streaming=True
    assert deps.llm.main_kwargs["streaming"] is True
    assert deps.llm.main_kwargs["tools"][0].name == "python_exec"


async def test_usage_from_response_metadata():
    deps = build_fake_deps()
    deps.llm.main_scripts = [
        [FakeChunk(content="答案", response_metadata={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}})]
    ]
    events = await _collect(stream_generation(deps, system_prompt="s", user_query="q"))
    assert events[-1].usage == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def _tool_call_chunk(index: int, code: str | None = None) -> FakeChunk:
    if code is None:
        code = f"print({index})"
    return FakeChunk(
        tool_call_chunks=[
            {
                "index": 0,
                "name": "python_exec",
                "id": f"call_{index}",
                "args": json.dumps({"code": code}, ensure_ascii=False),
            }
        ]
    )


async def test_tool_loop_is_bounded_by_max_iterations():
    settings = FakeSettings(TOOL_MAX_ITERATIONS=3)
    deps = build_fake_deps(settings=settings)
    deps.executor = FakeExecutor()
    deps.llm.main_scripts = [
        [_tool_call_chunk(1)],
        [_tool_call_chunk(2)],
        [_tool_call_chunk(3)],
        [_tool_call_chunk(4)],
        make_text_script("最终回答：1"),
    ]

    events = await _collect(stream_generation(deps, system_prompt="s", user_query="算一下"))
    assert deps.executor.codes == ["print(1)", "print(2)", "print(3)"]
    assert len(deps.llm.models[0].calls) == 5  # 3 次工具后第 4 次想调工具 → 收尾再流 1 次
    text = "".join(e.content for e in events if e.kind == "token")
    assert "最终回答" in text
    assert events[-1].kind == "done"
    assert events[-1].tool_failed is False


async def test_tool_failure_falls_back_to_manual_calc_notice():
    deps = build_fake_deps(settings=FakeSettings(TOOL_MAX_ITERATIONS=3))
    deps.executor = FakeExecutor(results=[FakeToolResult(ok=False, error="timeout")])
    deps.llm.main_scripts = [[_tool_call_chunk(1)], make_text_script("手算：100+100=200")]

    events = await _collect(stream_generation(deps, system_prompt="s", user_query="计算"))
    assert deps.executor.codes == ["print(1)"]
    notices = [e for e in events if e.kind == "notice"]
    assert notices and "自动计算失败" in notices[0].content
    assert events[-1].kind == "done"
    assert events[-1].tool_failed is True
    # 回灌给模型的 ToolMessage 里包含手算提示
    tool_messages = [m for m in deps.llm.models[0].calls[1] if isinstance(m, ToolMessage)]
    assert tool_messages and "自动计算失败" in tool_messages[0].content


async def test_tool_args_streamed_in_fragments_are_merged():
    deps = build_fake_deps()
    deps.llm.main_scripts = [
        [
            FakeChunk(
                tool_call_chunks=[
                    {"index": 0, "name": "python_exec", "id": "call_1", "args": '{"code": "pri'}
                ]
            ),
            FakeChunk(tool_call_chunks=[{"index": 0, "args": 'nt(123)"}'}]),
        ],
        make_text_script("完成"),
    ]
    events = await _collect(stream_generation(deps, system_prompt="s", user_query="q"))
    assert deps.executor.codes == ["print(123)"]
    assert any("完成" in e.content for e in events if e.kind == "token")


async def test_generation_cancel_returns_partial_tokens():
    deps = build_fake_deps()
    checks = {"n": 0}

    def cancel_check() -> bool:
        checks["n"] += 1
        return checks["n"] > 2  # 放行首个 chunk，第二个 chunk 前取消

    deps.llm.main_scripts = [[FakeChunk(content="第一段"), FakeChunk(content="不应输出")]]
    events = await _collect(
        stream_generation(deps, system_prompt="s", user_query="q", cancel_check=cancel_check)
    )
    assert any(e.kind == "token" and e.content == "第一段" for e in events)
    assert events[-1].kind == "cancelled"
    assert all(e.content != "不应输出" for e in events)


async def test_python_tool_executes_through_executor():
    deps = build_fake_deps()
    deps.executor = FakeExecutor(results=[FakeToolResult(ok=True, output="42")])
    tool = build_python_exec_tool(deps)
    assert tool.name == "python_exec"
    output = await tool.ainvoke({"code": "print(42)"})
    assert output == "42"
    assert deps.executor.codes == ["print(42)"]
