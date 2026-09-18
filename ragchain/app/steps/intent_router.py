"""意图识别步骤：doc/excel/both/general，失败降级 both。"""
from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..prompts import INTENT_SYSTEM_PROMPT, build_intent_user_prompt

logger = logging.getLogger(__name__)

VALID_INTENTS = ("doc", "excel", "both", "general")


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


async def route_intent(query: str, deps) -> IntentResult:
    """调用 sub_llm 判定意图；失败或非法值降级 both。"""
    try:
        result = await deps.llm.structured(
            system=INTENT_SYSTEM_PROMPT,
            user=build_intent_user_prompt(query),
            schema_cls=IntentResult,
        )
        if not isinstance(result, IntentResult):
            result = IntentResult.model_validate(result)
        return result
    except Exception as exc:  # noqa: BLE001 - 单点失败降级，不阻断
        logger.warning("意图识别失败，降级为 both：%s", exc)
        return IntentResult(intent="both", reason="意图识别失败，降级 both")
