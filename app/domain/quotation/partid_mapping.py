"""PDM PARTID to U8 parent inventory code mapping (pure)."""

from __future__ import annotations

from typing import Any, Dict, List


def map_parent_inv_code(partid: Any) -> str:
    """Map PDM PARTID to U8 ParentInvCode prefix."""
    if partid is None:
        return ""
    code = str(partid).strip()
    if not code:
        return ""
    if code.startswith("50GB"):
        return f"Z{code[4:]}"
    if code.startswith("50CB"):
        return f"X{code[4:]}"
    if code.startswith("50JC"):
        return f"P{code[4:]}"
    return code


def convert_partids_to_u8_codes(partids: List[str]) -> tuple[List[str], List[Dict[str, str]]]:
    """Convert PDM PARTIDs to U8 query codes with deduplicated code list order preserved."""
    converted_codes: List[str] = []
    mappings: List[Dict[str, str]] = []
    seen_codes: set[str] = set()

    for partid in partids:
        source = str(partid).strip()
        if not source:
            continue
        mapped = map_parent_inv_code(source)
        if not mapped:
            continue
        mappings.append({"pdm_partid": source, "u8_parent_inv_code": mapped})
        if mapped in seen_codes:
            continue
        seen_codes.add(mapped)
        converted_codes.append(mapped)

    return converted_codes, mappings
