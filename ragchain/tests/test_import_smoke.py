"""LangChain agent 栈 import / 工具执行冒烟测试。

背景（GitLab issue #29）：langgraph-prebuilt 1.0.13 与 langgraph 1.0.10 不兼容——
prebuilt 的 tool_node.py 模块级 `from langgraph.runtime import ExecutionInfo`，而
langgraph 1.0.10 的 runtime 没有该名字，会连带打死 `langgraph.prebuilt` /
`langchain.agents` / `langchain.agents.middleware` 三处 import；且即使 shim 掉名字，
ToolNode 内部的 `runtime.execution_info` 也会在工具执行时 AttributeError。

app/ 目前不 import agent 栈，所以回归不会自然暴露，必须在这里显式冒烟。
改 ragchain/requirements.in / requirements.txt 后必须跑本测试（见 docs/operations.md §3.3）。
"""

from __future__ import annotations

import importlib.metadata

import pytest


# 已验证可用的 (langgraph, langgraph-prebuilt) 组合。升级 langgraph 解除
# requirements.in 里的 pin 后，在干净环境验证 create_agent / ToolNode 可用，
# 再把新组合追加到这里。
_KNOWN_GOOD_PAIRS: set[tuple[str, str]] = {
    ("1.0.10", "1.0.8"),
}


def _installed_version(distro: str) -> str:
    return importlib.metadata.version(distro)


def test_langgraph_pair_is_known_good() -> None:
    """版本守卫：防止 prebuilt 被解析器抬到与 langgraph 不兼容的版本而不自知。"""
    langgraph_v = _installed_version("langgraph")
    prebuilt_v = _installed_version("langgraph-prebuilt")
    pair = (langgraph_v, prebuilt_v)
    assert pair in _KNOWN_GOOD_PAIRS, (
        f"langgraph {langgraph_v} + langgraph-prebuilt {prebuilt_v} 未经验证："
        "prebuilt>=1.0.9 要求 langgraph.runtime.ExecutionInfo，与 langgraph<1.1 不兼容。"
        "请在干净 venv 验证 create_agent/ToolNode 可用后，把新组合加入 "
        "tests/test_import_smoke.py::_KNOWN_GOOD_PAIRS。"
    )


def test_langchain_agents_importable() -> None:
    """issue #29 的核心回归：三处被连带的 import 必须全部可用。"""
    from langchain.agents import create_agent
    from langchain.agents.middleware import SummarizationMiddleware, ToolCallLimitMiddleware
    from langgraph.prebuilt import ToolNode, tools_condition

    assert callable(create_agent)
    assert ToolNode is not None
    assert callable(tools_condition)
    assert SummarizationMiddleware is not None
    assert ToolCallLimitMiddleware is not None


@pytest.mark.asyncio
async def test_tool_node_executes_tool() -> None:
    """不只 import：工具要能真跑通（issue #29 里 shim 掉名字后仍 AttributeError 的那条路）。"""
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode

    @tool
    def add(a: int, b: int) -> int:
        """add two ints"""
        return a + b

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode([add]))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    compiled = graph.compile()

    out = await compiled.ainvoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "add",
                            "args": {"a": 2, "b": 3},
                            "id": "call_smoke_1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        }
    )
    reply = out["messages"][-1]
    assert reply.content == "5"
    assert getattr(reply, "status", None) == "success"
