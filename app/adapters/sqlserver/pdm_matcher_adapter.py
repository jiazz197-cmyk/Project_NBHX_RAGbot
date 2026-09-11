"""PDM Matcher 适配层 - 输入格式转换 + 输出截断。

将主项目的 PdmBomRequest 格式转换为 matcher2 格式，
截断每部件结果至 20 条（HC → MC → LC 优先，砍掉低分和 RV），
直接传递层级结构给前端。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("database.sqlserver.pdm_matcher_adapter")

_MAX_PER_COMPONENT = 20


def adapt_input_to_matcher2(keywords: Any) -> List[dict]:
    if isinstance(keywords, dict):
        keywords = [keywords]

    if not isinstance(keywords, list) or not keywords:
        return []

    specs = []
    for item in keywords:
        if not isinstance(item, dict):
            continue

        attr = dict(item.get("attr", {}) or {})
        model = attr.pop("model", "") or item.get("model", "")

        spec = {
            "type": item.get("type", ""),
            "model": model,
            "attr": attr,
        }

        if "work_no" in item:
            spec["work_no"] = item["work_no"]

        specs.append(spec)

    logger.debug("适配输入: %d 个 spec", len(specs))
    return specs


def _truncate_component(result: dict) -> dict:
    """按 HC→MC→LC 顺序取前 _MAX_PER_COMPONENT 条，跳过 RV。"""
    if "error" in result:
        return result

    truncated = {"normalized": result.get("normalized", {})}
    kept = 0
    total_candidates = 0

    for layer in ("high_confidence", "medium_confidence", "low_confidence"):
        items = result.get(layer, [])
        total_candidates += len(items)
        if kept >= _MAX_PER_COMPONENT:
            truncated[layer] = []
            continue
        take = min(len(items), _MAX_PER_COMPONENT - kept)
        truncated[layer] = items[:take]
        kept += take

    truncated["needs_review"] = []
    truncated["stats"] = result.get("stats", {})
    truncated["stats"]["returned"] = kept

    return truncated


def _flatten_matcher_result(specs: list, results: list) -> dict:
    """将 matcher2 分层结果展平为前端兼容的 items 列表。

    前端依赖 pdm_result.items，每个 item 需要 PARTID, CHINANAME,
    QUERY_INDEX, QUERY_KEYWORDS, QUERY_EXPANDED_KEYWORDS 字段。
    """
    items: list[dict] = []
    for idx, (spec, result) in enumerate(zip(specs, results)):
        if not isinstance(result, dict) or "error" in (result or {}):
            continue

        query_index = idx + 1  # 1-based
        comp_type = spec.get("type", "")
        attr = spec.get("attr", {})
        model = spec.get("model", "")

        # 从 attr 构建 QUERY_KEYWORDS
        query_keywords = [f"{k}={v}" for k, v in attr.items()] if attr else []
        if model:
            query_keywords.insert(0, f"model={model}")

        # 从 normalized.attr_keywords 构建 QUERY_EXPANDED_KEYWORDS
        expanded: list[str] = []
        normalized = result.get("normalized", {})
        attr_keywords = normalized.get("attr_keywords", {})
        for _ak, keywords in attr_keywords.items():
            if isinstance(keywords, list):
                expanded.extend(str(k) for k in keywords)
            elif isinstance(keywords, str):
                expanded.append(keywords)

        # 展平所有层级的候选
        for layer_name in ("high_confidence", "medium_confidence", "low_confidence", "needs_review"):
            for c in result.get(layer_name, []):
                item = {
                    "PARTID": c.get("PARTID", ""),
                    "CHINANAME": c.get("CHINANAME", ""),
                    "MODEL": c.get("MODEL", ""),
                    "QUERY_INDEX": query_index,
                    "QUERY_KEYWORDS": query_keywords,
                    "QUERY_EXPANDED_KEYWORDS": expanded,
                    "confidence_level": layer_name,
                    "score": c.get("score", 0),
                }
                items.append(item)

    return {"items": items, "total": len(items)}


def run_pdm_match_query(keywords: Any) -> Dict[str, Any]:
    from app.adapters.pdm_matcher.engine import query_all_parallel

    specs = adapt_input_to_matcher2(keywords)
    if not specs:
        logger.warning("输入适配后无有效 spec")
        return {"components": [], "items": [], "total": 0}

    logger.info("开始 PDM 匹配查询: %d 个 spec", len(specs))

    try:
        results = query_all_parallel(specs, max_workers=settings.SQLSERVER_QUERY_MAX_WORKERS)
    except Exception as e:
        logger.error("matcher2 查询失败: %s", e, exc_info=True)
        return {"components": [], "items": [], "total": 0}

    components = [_truncate_component(r) for r in results]

    # 扁平化结果供前端消费
    flattened = _flatten_matcher_result(specs, results)

    logger.info("PDM 匹配查询完成: %d 个部件", len(components))
    return {
        "components": components,
        "items": flattened["items"],
        "total": flattened["total"],
    }
