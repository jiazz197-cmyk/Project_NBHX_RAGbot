"""Group flat U8 BOM rows by product type from keywords + PDM/U8 mapping."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

diag_logger = logging.getLogger("app.diag.u8_grouping")
logger = logging.getLogger("app.u8_grouping")


def _normalized_keywords(keywords_payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    keywords = keywords_payload.get("keywords") if isinstance(keywords_payload, Mapping) else None
    if isinstance(keywords, dict):
        keywords = [keywords]
    if not isinstance(keywords, list):
        return []
    return [entry for entry in keywords if isinstance(entry, dict)]


def _normalized_partids(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    ordered: List[str] = []
    seen: set[str] = set()
    for raw in values:
        partid = str(raw).strip()
        if not partid or partid in seen:
            continue
        seen.add(partid)
        ordered.append(partid)
    return ordered


def _query_index_to_type(keywords: List[Dict[str, Any]]) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for idx, entry in enumerate(keywords, start=1):
        mapping[idx] = str(entry.get("type") or "").strip() or "Uncategorized"
    return mapping


def _selection_to_types(
    *,
    approved_partids: List[str],
    pdm_result: Mapping[str, Any],
    query_index_to_type: Dict[int, str],
) -> Dict[str, str]:
    items = pdm_result.get("items") if isinstance(pdm_result, Mapping) else None
    if not isinstance(items, list) or not approved_partids:
        return {}

    approved_set = set(approved_partids)
    partid_to_type: Dict[str, str] = {}
    for item in items:
        if not isinstance(item, Mapping):
            continue
        partid = str(item.get("PARTID") or "").strip()
        if not partid or partid not in approved_set or partid in partid_to_type:
            continue

        query_index = item.get("QUERY_INDEX")
        if isinstance(query_index, str):
            try:
                query_index = int(query_index.strip())
            except ValueError:
                query_index = None
        if not isinstance(query_index, int):
            continue

        partid_to_type[partid] = query_index_to_type.get(query_index, "Uncategorized")
    return partid_to_type


def _selection_to_u8_codes(
    *,
    approved_partids: List[str],
    pdm_to_u8_mappings: List[Dict[str, str]],
) -> Dict[str, str]:
    if not approved_partids:
        return {}

    approved_set = set(approved_partids)
    partid_to_code: Dict[str, str] = {}
    for mapping in pdm_to_u8_mappings:
        if not isinstance(mapping, dict):
            continue
        partid = str(mapping.get("pdm_partid") or "").strip()
        code = str(mapping.get("u8_parent_inv_code") or "").strip()
        if not partid or not code or partid not in approved_set or partid in partid_to_code:
            continue
        partid_to_code[partid] = code
    return partid_to_code


def _build_selection_driven_groups(
    *,
    approved_partids: List[str],
    partid_to_type: Dict[str, str],
    partid_to_code: Dict[str, str],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], List[Dict[str, Any]]]:
    type_to_codes: Dict[str, List[str]] = {}
    type_to_partids: Dict[str, List[str]] = {}
    type_entries: List[Dict[str, Any]] = []
    skipped_type: list[str] = []
    skipped_code: list[str] = []
    skipped_both: list[str] = []

    for partid in approved_partids:
        type_name = partid_to_type.get(partid)
        code = partid_to_code.get(partid)
        matched = bool(type_name and code)
        type_entries.append(
            {
                "pdm_partid": partid,
                "type": type_name or "Uncategorized",
                "u8_parent_inv_code": code or "",
                "matched": matched,
            }
        )
        if not matched:
            if not type_name and not code:
                skipped_both.append(partid)
            elif not type_name:
                skipped_type.append(partid)
            else:
                skipped_code.append(partid)
            continue

        codes = type_to_codes.setdefault(type_name, [])
        type_to_partids.setdefault(type_name, []).append(partid)
        if code not in codes:
            codes.append(code)

    diag_logger.debug(
        "[diag_u8_grouping] _build_selection_driven_groups: matched=%s skipped_no_type=%s skipped_no_code=%s skipped_neither=%s",
        len(type_to_codes),
        len(skipped_type),
        len(skipped_code),
        len(skipped_both),
    )

    return type_to_codes, type_to_partids, type_entries


def _build_positional_groups(
    *,
    keywords: List[Dict[str, Any]],
    pdm_to_u8_mappings: List[Dict[str, str]],
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], List[Dict[str, Any]]]:
    mapped_order: List[str] = []
    mapped_set: set[str] = set()
    for mapping in pdm_to_u8_mappings:
        if not isinstance(mapping, dict):
            continue
        code = str(mapping.get("u8_parent_inv_code") or "").strip()
        if not code or code in mapped_set:
            continue
        mapped_set.add(code)
        mapped_order.append(code)

    type_entries: List[Dict[str, Any]] = []
    type_to_codes: Dict[str, List[str]] = {}
    type_to_partids: Dict[str, List[str]] = {}
    for idx, entry in enumerate(keywords, start=1):
        type_name = str(entry.get("type") or "").strip() or "Uncategorized"
        part_code = mapped_order[idx - 1] if idx - 1 < len(mapped_order) else ""
        if not part_code:
            continue
        type_to_codes.setdefault(type_name, []).append(part_code)
        # positional grouping: use u8 code as pseudo-partid
        type_to_partids.setdefault(type_name, []).append(part_code)
        type_entries.append(
            {
                "query_index": idx,
                "type": type_name,
                "u8_parent_inv_code": part_code,
                "matched": True,
            }
        )
    return type_to_codes, type_to_partids, type_entries


def group_u8_result_by_type(
    *,
    keywords_payload: Dict[str, Any],
    pdm_result: Optional[Dict[str, Any]] = None,
    approved_partids: Optional[List[str]] = None,
    u8_result: Dict[str, Any],
    pdm_to_u8_mappings: List[Dict[str, str]],
    manual_partid_types: Optional[Dict[str, str]] = None,
    code_type: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build type-grouped U8 payload.

    When approval context is available, the grouping follows the approved PARTIDs
    and their original QUERY_INDEX/type association.  ``manual_partid_types``
    supplies user-provided type names for manually-added PARTIDs that have no
    PDM row, ensuring their U8 results are included in the correct type group.
    Otherwise it falls back to the previous positional behavior.

    Note: When ``code_type == "project"``, this function should not be called.
    Project code mode is handled separately in ExecuteQuotationPhase2UseCase.
    """
    # 项目编码模式不应该调用此函数
    if code_type == "project":
        logger.warning(
            "group_u8_result_by_type called with code_type='project', returning empty result. "
            "Project code mode should be handled in ExecuteQuotationPhase2UseCase."
        )
        return {"total": 0, "items": []}, {"total_types": 0, "types": []}

    items = u8_result.get("items") if isinstance(u8_result, dict) else None
    if not isinstance(items, list):
        return {"total": 0, "items": []}, {"total_types": 0, "types": []}

    keywords = _normalized_keywords(keywords_payload)
    normalized_approved_partids = _normalized_partids(approved_partids)
    query_index_to_type = _query_index_to_type(keywords)
    partid_to_type = _selection_to_types(
        approved_partids=normalized_approved_partids,
        pdm_result=pdm_result or {},
        query_index_to_type=query_index_to_type,
    )
    # Merge manual type mapping: user-provided types for extra_partids
    # override the "no type → skip" gap in PDM-based _selection_to_types.
    if manual_partid_types:
        for partid, type_name in manual_partid_types.items():
            if partid in normalized_approved_partids and type_name:
                partid_to_type[partid] = type_name
    partid_to_code = _selection_to_u8_codes(
        approved_partids=normalized_approved_partids,
        pdm_to_u8_mappings=pdm_to_u8_mappings,
    )

    # ------------------------------------------------------------------
    # Diagnostic: log mismatches between type and code mappings.
    # Manual partids (extra_partids) exist in pdm_to_u8_mappings (they get
    # a U8 code) but NOT in pdm_result["items"] (they have no PDM row),
    # so they are absent from partid_to_type.  This causes
    # _build_selection_driven_groups to skip their U8 results.
    # ------------------------------------------------------------------
    typed_partids = set(partid_to_type.keys())
    coded_partids = set(partid_to_code.keys())
    only_typed = typed_partids - coded_partids
    only_coded = coded_partids - typed_partids
    both = typed_partids & coded_partids
    neither = set(normalized_approved_partids) - typed_partids - coded_partids
    diag_logger.debug(
        "[diag_u8_grouping] approved=%s typed=%s coded=%s both=%s only_typed=%s only_coded=%s neither=%s",
        len(normalized_approved_partids),
        len(typed_partids),
        len(coded_partids),
        len(both),
        len(only_typed),
        len(only_coded),
        len(neither),
    )

    if normalized_approved_partids and partid_to_type and partid_to_code:
        type_to_codes, type_to_partids, type_entries = _build_selection_driven_groups(
            approved_partids=normalized_approved_partids,
            partid_to_type=partid_to_type,
            partid_to_code=partid_to_code,
        )
    else:
        type_to_codes, type_to_partids, type_entries = _build_positional_groups(
            keywords=keywords,
            pdm_to_u8_mappings=pdm_to_u8_mappings,
        )

    # 按 root_inv_code 分组时，同时获取根父件名称
    root_to_rows: Dict[str, List[Dict[str, Any]]] = {}
    root_to_name: Dict[str, str] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        root_code = str(raw.get("__root_inv_code") or "").strip()
        if not root_code:
            continue
        root_to_rows.setdefault(root_code, []).append(raw)

        # 获取根父件名称
        root_name = str(raw.get("__root_inv_name") or "").strip()
        if root_name and root_code not in root_to_name:
            root_to_name[root_code] = root_name

    grouped_items: List[Dict[str, Any]] = []
    for type_name, codes in type_to_codes.items():
        rows: List[Dict[str, Any]] = []
        for code in codes:
            rows.extend(root_to_rows.get(code, []))

        # 使用根父件名称作为显示名称
        display_name = type_name
        if codes:
            # 尝试从第一个编码获取名称
            first_code = codes[0]
            if first_code in root_to_name:
                display_name = root_to_name[first_code]

        grouped_items.append(
            {
                "type": display_name,
                "u8_parent_inv_codes": codes,
                "partids": type_to_partids.get(type_name, []),
                "total": len(rows),
                "items": rows,
            }
        )

    selected_codes = [code for codes in type_to_codes.values() for code in codes]
    unmatched_codes = [code for code in selected_codes if code not in root_to_rows]
    summary = {
        "total_types": len(grouped_items),
        "total_items": len(items),
        "matched_root_codes": len(selected_codes) - len(unmatched_codes),
        "unmatched_root_codes": unmatched_codes,
        "types": [
            {
                "type": item.get("type"),
                "u8_parent_inv_codes": item.get("u8_parent_inv_codes"),
                "total": item.get("total"),
            }
            for item in grouped_items
        ],
        "mapping": type_entries,
    }

    grouped = {
        "total": len(grouped_items),
        "items": grouped_items,
    }
    return grouped, summary
