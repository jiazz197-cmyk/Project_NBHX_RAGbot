"""PDM BOM response helpers (pure)."""

from __future__ import annotations

from typing import Any, Dict, List


def collect_pdm_partids(pdm_result: Dict[str, Any]) -> List[str]:
    """Extract unique PARTID values from PDM BOM response while keeping order.

    Supports both the old flat format (items list) and the new matcher2
    format (components with layered results).
    """
    seen: set[str] = set()
    partids: List[str] = []

    # 新格式: components 分层结构
    components = pdm_result.get("components") if isinstance(pdm_result, dict) else None
    if isinstance(components, list):
        for comp in components:
            if not isinstance(comp, dict):
                continue
            for layer_name in ("high_confidence", "medium_confidence", "low_confidence", "needs_review"):
                for item in comp.get(layer_name, []):
                    partid = str(item.get("PARTID", "")).strip()
                    if partid and partid not in seen:
                        seen.add(partid)
                        partids.append(partid)
        if partids:
            return partids

    # 旧格式: items 扁平列表
    items = pdm_result.get("items") if isinstance(pdm_result, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            partid = item.get("PARTID")
            if partid is None:
                continue
            value = str(partid).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            partids.append(value)
    return partids


def summarize_pdm_query_params(
    keywords_payload: Dict[str, Any],
    max_attrs: int = 3,
    max_total_chars: int = 380,
) -> str:
    """Human-readable summary of keywords for progress UI."""
    keywords = keywords_payload.get("keywords") if isinstance(keywords_payload, dict) else None
    if isinstance(keywords, dict):
        keywords = [keywords]
    if not isinstance(keywords, list) or not keywords:
        return "（无参数）"

    parts: List[str] = []
    for entry in keywords:
        if not isinstance(entry, dict):
            continue
        type_name = str(entry.get("type") or "").strip() or "未命名"
        attr = entry.get("attr") if isinstance(entry.get("attr"), dict) else {}
        if attr:
            items = list(attr.items())[:max_attrs]
            attr_texts = [f"{k}={v}" for k, v in items]
            if len(attr) > max_attrs:
                attr_texts.append("…")
            parts.append(f"{type_name}[{', '.join(attr_texts)}]")
        else:
            parts.append(type_name)
    if not parts:
        return "（无参数）"

    sep = " | "
    kept: List[str] = []
    for idx, part in enumerate(parts):
        candidate = sep.join(kept + [part])
        remaining = len(parts) - idx - 1
        suffix = f"{sep}…等 {remaining} 个 type" if remaining > 0 else ""
        if len(candidate) + len(suffix) > max_total_chars and kept:
            tail = f"…等 {len(parts) - len(kept)} 个 type"
            return sep.join(kept) + sep + tail
        kept.append(part)
    return sep.join(kept)


def summarize_partid_list(partids: List[str], max_items: int = 5) -> str:
    """Short human-readable list of PARTIDs / U8 codes for progress UI."""
    if not partids:
        return "（无参数）"
    head = partids[:max_items]
    if len(partids) > max_items:
        return f"{', '.join(head)} 等共 {len(partids)} 个"
    return ", ".join(head)
