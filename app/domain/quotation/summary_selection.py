"""Build approval-driven summary selection snapshots from existing task data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from app.domain.quotation.partid_mapping import map_parent_inv_code
from app.domain.quotation.value_objects import QuotationSummarySelectionItem


def _normalized_keywords(keywords_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    keywords = keywords_payload.get("keywords") if isinstance(keywords_payload, dict) else None
    if isinstance(keywords, dict):
        keywords = [keywords]
    if not isinstance(keywords, list):
        return []
    return [entry for entry in keywords if isinstance(entry, dict)]


def _normalized_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text:
            normalized.append(text)
    return normalized


def _parse_query_index(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _query_index_to_type(keywords_payload: Dict[str, Any]) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for idx, entry in enumerate(_normalized_keywords(keywords_payload), start=1):
        type_name = str(entry.get("type") or "").strip() or "Uncategorized"
        mapping[idx] = type_name
    return mapping


def build_summary_selection_items(
    *,
    approved_partids: List[str],
    pdm_result: Dict[str, Any],
    keywords_payload: Dict[str, Any],
) -> list[QuotationSummarySelectionItem]:
    query_index_to_type = _query_index_to_type(keywords_payload)
    pdm_items = pdm_result.get("items") if isinstance(pdm_result, dict) else None

    row_by_partid: Dict[str, Mapping[str, Any]] = {}
    if isinstance(pdm_items, list):
        for item in pdm_items:
            if not isinstance(item, Mapping):
                continue
            partid = str(item.get("PARTID") or "").strip()
            if partid and partid not in row_by_partid:
                row_by_partid[partid] = item

    selection_items: list[QuotationSummarySelectionItem] = []
    seen: set[str] = set()

    for selection_index, raw_partid in enumerate(approved_partids, start=1):
        partid = str(raw_partid).strip()
        if not partid or partid in seen:
            continue
        seen.add(partid)

        row = row_by_partid.get(partid)
        query_index = _parse_query_index(row.get("QUERY_INDEX")) if row else None
        query_keywords = _normalized_string_list(row.get("QUERY_KEYWORDS")) if row else []
        query_expanded_keywords = (
            _normalized_string_list(row.get("QUERY_EXPANDED_KEYWORDS")) if row else []
        )
        fallback_type_name = query_keywords[0] if query_keywords else "Uncategorized"
        type_name = query_index_to_type.get(query_index, fallback_type_name)
        pdm_name = str(row.get("CHINANAME") or "").strip() if row else ""

        selection_items.append(
            QuotationSummarySelectionItem(
                selection_index=selection_index,
                partid=partid,
                u8_parent_inv_code=map_parent_inv_code(partid),
                type_name=type_name,
                pdm_name=pdm_name,
                query_index=query_index,
                query_keywords=query_keywords,
                query_expanded_keywords=query_expanded_keywords,
                matched_pdm_row=row is not None,
            )
        )

    return selection_items
