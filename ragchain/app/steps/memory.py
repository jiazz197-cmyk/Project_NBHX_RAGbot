"""记忆加载步骤：短期消息、长期画像、超阈值时压缩；单点失败降级为空。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    profile_summary: str = ""
    compressed_context: str = ""


async def _safe_get_messages(
    auth, deps, conversation_id: str
) -> tuple[list[dict[str, Any]], bool]:
    """取最近 MEMORY_RECENT_TURNS 条，并判断“总消息数 > 压缩阈值”。

    主应用 ``GET /messages`` 没有 total，只有 page/limit/has_more；这里把
    page=1 的 limit 放大到 ``max(MEMORY_RECENT_TURNS, threshold + 1)``：
    返回满 ``threshold+1`` 条即等价于消息总数 > threshold，可据此触发压缩。
    返回的 recent 只保留最新的 MEMORY_RECENT_TURNS 条（接口按写入顺序升序）。
    """
    recent_turns = max(1, int(getattr(deps.settings, "MEMORY_RECENT_TURNS", 10) or 10))
    threshold = max(0, int(getattr(deps.settings, "MEMORY_COMPRESS_THRESHOLD", 20) or 20))
    probe_limit = max(recent_turns, threshold + 1)
    try:
        messages = await deps.backend.get_messages(
            auth.token,
            conversation_id,
            page=1,
            limit=probe_limit,
        )
    except Exception as exc:  # noqa: BLE001 - 记忆单点失败不阻断
        logger.warning("短期记忆加载失败，降级为空：%s", exc)
        return [], False
    if not isinstance(messages, list):
        return [], False
    cleaned = [m for m in messages if isinstance(m, dict)]
    older_than_threshold = len(cleaned) > threshold
    recent = cleaned[-recent_turns:] if len(cleaned) > recent_turns else cleaned
    return recent, older_than_threshold


async def _safe_get_summary(auth, deps) -> str:
    try:
        summary = await deps.backend.get_latest_summary(auth.token, auth.user_id)
    except Exception as exc:  # noqa: BLE001 - 记忆单点失败不阻断
        logger.warning("长期画像加载失败，降级为空：%s", exc)
        return ""
    return str(summary or "").strip()


async def load_memory(auth, deps, conversation_id: str) -> MemoryContext:
    context = MemoryContext()
    (messages, has_more_than_threshold), summary = await asyncio.gather(
        _safe_get_messages(auth, deps, conversation_id),
        _safe_get_summary(auth, deps),
    )
    context.recent_messages = messages
    context.profile_summary = summary

    if has_more_than_threshold:
        try:
            compressed = await deps.backend.compress_context(
                auth.token,
                auth.user_id,
                conversation_id,
                deps.settings.MEMORY_COMPRESS_N_RECENT,
            )
            context.compressed_context = str(compressed or "").strip()
        except Exception as exc:  # noqa: BLE001 - 压缩失败不阻断
            logger.warning("上下文压缩失败，降级为空：%s", exc)
    return context
