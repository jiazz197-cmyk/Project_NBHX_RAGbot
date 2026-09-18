"""联网搜索步骤：按 search_mode 是否启用决定，失败仅告警。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def search_web(*, query: str, enabled: bool, deps) -> list:
    if not enabled:
        return []
    settings = deps.settings
    try:
        results = await deps.search.search(query, count=settings.SEARCH_RESULT_COUNT)
    except Exception as exc:  # noqa: BLE001 - 联网失败不影响主流程
        logger.warning("联网搜索失败，降级继续：%s", exc)
        return []
    if not results:
        return []
    try:
        items = list(results)
    except TypeError:
        logger.warning("联网搜索返回格式异常：%r", results)
        return []
    return items[: settings.SEARCH_RESULT_COUNT]
