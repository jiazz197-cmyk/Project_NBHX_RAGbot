"""query 改写步骤：sub_llm 结构化输出，失败降级原 query。"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..clients.llm_client import LLMError
from ..prompts import render_rewriter_prompt

logger = logging.getLogger(__name__)


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [f"{k}={v}" for k, v in value.items()]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]


class RewriteResult(BaseModel):
    rewritten_query: str = ""
    keywords: list[str] = Field(default_factory=list)
    time_range: str = ""
    entities: list[str] = Field(default_factory=list)

    @field_validator("keywords", "entities", mode="before")
    @classmethod
    def _normalize_lists(cls, value: Any) -> list[str]:
        return _coerce_str_list(value)

    @field_validator("rewritten_query", "time_range", mode="before")
    @classmethod
    def _normalize_str(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


async def rewrite_query(
    query: str,
    history: list[dict[str, Any]] | None,
    deps,
    *,
    now: datetime | date | None = None,
) -> RewriteResult:
    """输入短期历史+原始 query，输出结构化改写；任何失败降级为原 query。"""
    try:
        system, user = render_rewriter_prompt(query, history, now=now)
        result = await deps.llm.structured(
            system=system,
            user=user,
            schema_cls=RewriteResult,
        )
        if result is None:
            # 结构化调用静默返回 None（如 method=function_calling 且网关忽略
            # tool_choice）时显式转降级路径，不走 model_validate(None)。
            raise LLMError("结构化改写返回 None")
        if not isinstance(result, RewriteResult):
            result = RewriteResult.model_validate(result)
        if not result.rewritten_query.strip():
            result.rewritten_query = query
        return result
    except Exception as exc:  # noqa: BLE001 - 单点失败降级，不阻断
        logger.warning("query 改写失败，降级使用原 query：%s", exc)
        return RewriteResult(rewritten_query=query)
