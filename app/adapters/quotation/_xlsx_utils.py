"""Shared xlsx rendering helpers for quotation adapters.

Constants and field-index helpers are identical across adapters; ``normalize_row``
and ``collect_fieldnames`` accept the caller's excluded-key set so each adapter
keeps its own row-filtering behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, MutableSet

EXCEL_TITLE_INVALID = set(':*?/\\[]')

DETAIL_TOTAL_FIELD_CANDIDATES = (
    "\u603b\u4ef7",
    "\u91d1\u989d",
    "amount",
    "total_price",
    "totalPrice",
)

UNIT_PRICE_FIELD_CANDIDATES = (
    "\u5355\u4ef7",
    "iInvNcost",
    "unit_price",
    "price",
)

CUM_QTY_FIELD_CANDIDATES = (
    "\u7d2f\u8ba1\u7528\u91cf",
    "CUM_QTY",
    "cum_qty",
    "quantity",
)


def excel_sheet_title(raw: str, used: MutableSet[str]) -> str:
    chars: List[str] = []
    for c in str(raw or "")[:50]:
        if c in EXCEL_TITLE_INVALID or ord(c) < 32:
            chars.append("_")
        else:
            chars.append(c)
    base = "".join(chars).strip("_") or "Sheet"
    base = base[:31]
    title = base
    n = 1
    while title in used:
        suffix = f"_{n}"
        title = (base[: 31 - len(suffix)] + suffix) if len(suffix) < 31 else f"S{n}"[:31]
        n += 1
    used.add(title)
    return title


def normalize_row(row: Mapping[str, Any], excluded: frozenset[str]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k not in excluded}


def collect_fieldnames(
    rows: Iterable[Mapping[str, Any]], excluded: frozenset[str]
) -> List[str]:
    ordered: List[str] = []
    seen: MutableSet[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        normed = normalize_row(row, excluded)
        for key in normed:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
    return ordered


def detail_total_column_index(fieldnames: List[str]) -> int | None:
    for idx, fieldname in enumerate(fieldnames):
        if fieldname in DETAIL_TOTAL_FIELD_CANDIDATES:
            return idx
    return None


def unit_price_column_index(fieldnames: List[str]) -> int | None:
    for idx, name in enumerate(fieldnames):
        if name in UNIT_PRICE_FIELD_CANDIDATES:
            return idx
    return None


def cum_qty_column_index(fieldnames: List[str]) -> int | None:
    for idx, name in enumerate(fieldnames):
        if name in CUM_QTY_FIELD_CANDIDATES:
            return idx
    return None
