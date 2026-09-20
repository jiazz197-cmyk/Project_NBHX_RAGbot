"""意图识别步骤：doc/excel/both/general，失败降级 both。"""
from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..clients.llm_client import LLMError
from ..prompts import INTENT_SYSTEM_PROMPT, build_intent_user_prompt

logger = logging.getLogger(__name__)

VALID_INTENTS = ("doc", "excel", "both", "general")

# 用户显式要求查表的措辞。LLM 判成 general/doc 时用它兜底覆盖——改写会把
# “查查表”这类线索丢掉，而意图误判 general 会让本地检索整体跳过（2026-09-18 线上）。
_EXPLICIT_TABLE_HINT_RE = re.compile(
    r"查表|查查表|查一下表|查表格|查阅表格|台账|明细表|excel表|excel表格|表格数据|数据表",
    re.IGNORECASE,
)


def normalize_intent(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in VALID_INTENTS:
        return text
    aliases = {
        "document": "doc",
        "docs": "doc",
        "制度": "doc",
        "文档": "doc",
        "excel_data": "excel",
        "table": "excel",
        "表格": "excel",
        "混合": "both",
        "all": "both",
        "general_chat": "general",
        "chat": "general",
        "闲聊": "general",
    }
    return aliases.get(text, "both")


class IntentResult(BaseModel):
    intent: Literal["doc", "excel", "both", "general"] = "both"
    reason: str = ""

    @field_validator("intent", mode="before")
    @classmethod
    def _normalize_intent(cls, value: Any) -> str:
        return normalize_intent(value)


def has_explicit_table_hint(*texts: str | None) -> bool:
    """原始问题/改写/关键词里是否出现“查表”类显式要求。"""
    return any(_EXPLICIT_TABLE_HINT_RE.search(text) for text in texts if text)


def apply_table_hint(
    result: IntentResult,
    *,
    raw_query: str = "",
    rewritten_query: str = "",
    keywords: list[str] | None = None,
) -> IntentResult:
    """显式查表线索兜底：general → excel；doc → both；excel/both 不变。

    只在 LLM 漏判方向时收窄，不会把已经正确的 excel/both 改坏；doc→both 保证
    制度类问题仍能拿到文档上下文。
    """
    if result.intent not in ("general", "doc"):
        return result
    haystack = (raw_query, rewritten_query, " ".join(keywords or []))
    if not has_explicit_table_hint(*haystack):
        return result

    intent = "excel" if result.intent == "general" else "both"
    reason = f"{result.reason}；命中显式查表线索，覆盖为 {intent}".strip("；")
    logger.info("意图被显式查表线索覆盖：%s → %s", result.intent, intent)
    return IntentResult(intent=intent, reason=reason)


async def route_intent(
    query: str,
    deps,
    *,
    raw_query: str = "",
    keywords: list[str] | None = None,
) -> IntentResult:
    """调用 sub_llm 判定意图；失败或非法值降级 both。

    ``raw_query`` / ``keywords`` 既用于补充 prompt（改写会丢显式查表线索，
    详见 :func:`app.prompts.build_intent_user_prompt`），也用于
    :func:`apply_table_hint` 的确定性兜底。
    """
    try:
        result = await deps.llm.structured(
            system=INTENT_SYSTEM_PROMPT,
            user=build_intent_user_prompt(query, raw_query=raw_query, keywords=keywords),
            schema_cls=IntentResult,
        )
        if result is None:
            # 结构化调用静默返回 None（如 method=function_calling 且网关忽略
            # tool_choice）时显式转降级路径，不走 model_validate(None)。
            raise LLMError("结构化意图识别返回 None")
        if not isinstance(result, IntentResult):
            result = IntentResult.model_validate(result)
    except Exception as exc:  # noqa: BLE001 - 单点失败降级，不阻断
        logger.warning("意图识别失败，降级为 both：%s", exc)
        return IntentResult(intent="both", reason="意图识别失败，降级 both")

    return apply_table_hint(result, raw_query=raw_query, rewritten_query=query, keywords=keywords)
