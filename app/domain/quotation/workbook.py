"""Build workbook data for quotation summary + detail sheet export."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Mapping

from app.domain.quotation.value_objects import (
    QuotationDetailSheet,
    QuotationFixedChargeRow,
    QuotationSummaryMeta,
    QuotationSummaryRow,
    QuotationSummarySelectionItem,
    QuotationWorkbookData,
)

_FIXED_CHARGE_NAMES: tuple[str, ...] = (
    "\u6742\u9879",
    "FOB",
    "\u8fd0\u8f93\u8d39",
    "\u8bbe\u8ba1\u5de5\u65f6",
    "\u4eba\u5de5",
)
_AMOUNT_FIELD_CANDIDATES: tuple[str, ...] = (
    "\u603b\u4ef7",
    "\u91d1\u989d",
    "amount",
    "total_price",
    "totalPrice",
)


def _normalize_selection_items(items: Any) -> List[QuotationSummarySelectionItem]:
    if not isinstance(items, list):
        return []

    normalized: List[QuotationSummarySelectionItem] = []
    for raw in items:
        if isinstance(raw, QuotationSummarySelectionItem):
            normalized.append(raw)
            continue
        if not isinstance(raw, Mapping):
            continue
        normalized.append(
            QuotationSummarySelectionItem(
                selection_index=int(raw.get("selection_index") or 0),
                partid=str(raw.get("partid") or "").strip(),
                u8_parent_inv_code=str(raw.get("u8_parent_inv_code") or "").strip(),
                type_name=str(raw.get("type_name") or "").strip(),
                pdm_name=str(raw.get("pdm_name") or "").strip(),
                query_index=raw.get("query_index") if isinstance(raw.get("query_index"), int) else None,
                query_keywords=[
                    str(item).strip() for item in raw.get("query_keywords", []) if str(item).strip()
                ]
                if isinstance(raw.get("query_keywords"), list)
                else [],
                query_expanded_keywords=[
                    str(item).strip()
                    for item in raw.get("query_expanded_keywords", [])
                    if str(item).strip()
                ]
                if isinstance(raw.get("query_expanded_keywords"), list)
                else [],
                matched_pdm_row=bool(raw.get("matched_pdm_row")),
            )
        )
    return normalized


def _rows_total_amount(rows: Iterable[Mapping[str, Any]]) -> Decimal:
    total = Decimal("0")
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        for key in _AMOUNT_FIELD_CANDIDATES:
            value = row.get(key)
            if value is None:
                continue
            try:
                total += Decimal(str(value))
            except (TypeError, ValueError, ArithmeticError):
                pass
            break
    return total.quantize(Decimal("0.01"))


def _parse_amount(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return Decimal("0")


def _group_selection_by_type(
    selection_items: List[QuotationSummarySelectionItem],
) -> Dict[str, List[QuotationSummarySelectionItem]]:
    grouped: Dict[str, List[QuotationSummarySelectionItem]] = {}
    for item in sorted(selection_items, key=lambda x: x.selection_index):
        type_name = item.type_name.strip() or "Uncategorized"
        grouped.setdefault(type_name, []).append(item)
    return grouped


def _quantity_from_map(
    group: Mapping[str, Any],
    type_name: str,
    qty_map: Mapping[str, float],
) -> float:
    for key in ("partids", "u8_parent_inv_codes"):
        values = group.get(key)
        if not isinstance(values, list):
            continue
        for raw in values:
            code = str(raw or "").strip()
            if code in qty_map:
                return float(qty_map[code])
    return float(qty_map.get(type_name, 1.0))


def _merge_texts(values: List[str]) -> str:
    seen: set[str] = set()
    merged: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        merged.append(text)
    return " / ".join(merged)


def _extract_model_from_raw(raw_extracted_info: Any) -> str:
    if not isinstance(raw_extracted_info, Mapping):
        return ""
    meta = raw_extracted_info.get("meta")
    if not isinstance(meta, Mapping):
        return ""
    model = str(meta.get("model") or "").strip()
    return model.upper() if model else ""


def _extract_model_from_keywords(keywords_payload: Any) -> str:
    if not isinstance(keywords_payload, Mapping):
        return ""
    keywords = keywords_payload.get("keywords")
    if isinstance(keywords, Mapping):
        keywords = [keywords]
    if not isinstance(keywords, list):
        return ""
    for item in keywords:
        if not isinstance(item, Mapping):
            continue
        attrs = item.get("attr")
        if not isinstance(attrs, Mapping):
            continue
        model = str(attrs.get("model") or "").strip()
        if model:
            return model.upper()
    return ""


def _extract_model(raw_extracted_info: Any, keywords_payload: Any) -> str:
    return _extract_model_from_raw(raw_extracted_info) or _extract_model_from_keywords(
        keywords_payload
    )


def _format_quote_date(generated_at: datetime | None) -> str:
    if generated_at is None:
        return ""
    return generated_at.strftime("%Y/%m/%d")


def _format_quantity(qty: Any) -> str:
    """Format quantity with up to 4 decimals, stripping trailing zeros."""
    try:
        rounded = round(float(qty), 4)
    except (TypeError, ValueError):
        return "0"
    text = f"{rounded:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def build_quotation_workbook_data(
    *,
    u8_result_by_type: Dict[str, Any],
    summary_selection_items: Any,
    raw_extracted_info: Any = None,
    keywords_payload: Any = None,
    generated_at: datetime | None = None,
    partid_quantities: Dict[str, float] | None = None,
) -> QuotationWorkbookData:
    selection_items = _normalize_selection_items(summary_selection_items)
    selection_by_type = _group_selection_by_type(selection_items)

    groups = u8_result_by_type.get("items") if isinstance(u8_result_by_type, dict) else None
    detail_sheets: List[QuotationDetailSheet] = []
    summary_rows: List[QuotationSummaryRow] = []
    qty_map = partid_quantities or {}

    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, Mapping):
                continue
            type_name = str(group.get("type") or "").strip() or "Uncategorized"
            rows = [
                item
                for item in (group.get("items") if isinstance(group.get("items"), list) else [])
                if isinstance(item, Mapping)
            ]

            # 数量优先级：分组自带的 quantity > 原始编码/U8 编码/type 查找 > 默认 1
            group_qty = group.get("quantity")
            if group_qty is not None:
                try:
                    qty = float(group_qty)
                except (TypeError, ValueError):
                    qty = 1.0
            else:
                try:
                    qty = _quantity_from_map(group, type_name, qty_map)
                except (TypeError, ValueError):
                    qty = 1
            if qty <= 0:
                qty = 1
            qty = round(float(qty), 4)

            summary_only = bool(group.get("summary_only"))

            if summary_only:
                # 外购件：只在汇总页显示，不生成明细页
                # 优先使用 group 自带的价格，否则从 rows 计算
                total_amount = _parse_amount(group.get("total_price"))
                if total_amount == Decimal("0"):
                    total_amount = _rows_total_amount(rows)
                unit_price = _parse_amount(group.get("unit_price"))
                if unit_price == Decimal("0"):
                    unit_price = total_amount
            else:
                # 虚拟件/有明细的组件：正常生成明细页
                total_amount = _rows_total_amount(rows)
                unit_price = total_amount
                detail_sheets.append(
                    QuotationDetailSheet(
                        sheet_name=type_name,
                        rows=[dict(row) for row in rows],
                        total_amount=total_amount,
                    )
                )

            selected = selection_by_type.get(type_name, [])
            # part_no: prefer PDM PARTID (original input code), then U8 parent codes, then type_name
            part_no = _merge_texts([item.partid for item in selected])
            if not part_no:
                # Direct U8 flow: use original input codes from selection items
                u8_codes = group.get("u8_parent_inv_codes") if isinstance(group, Mapping) else None
                part_no = _merge_texts([str(c) for c in (u8_codes or [])]) or type_name
            name = _merge_texts([item.pdm_name for item in selected]) or type_name

            detail_sheet_ref = type_name if not summary_only else ""
            summary_rows.append(
                QuotationSummaryRow(
                    part_no=part_no,
                    name=name,
                    quantity_display=_format_quantity(qty),
                    unit_price=unit_price,
                    amount=(total_amount * Decimal(str(qty))).quantize(Decimal("0.01")),
                    detail_sheet_name=detail_sheet_ref,
                )
            )

    fixed_charge_rows = [QuotationFixedChargeRow(name=name) for name in _FIXED_CHARGE_NAMES]
    grand_total = (
        sum((row.amount for row in summary_rows), Decimal("0"))
        + sum((_parse_amount(row.amount) for row in fixed_charge_rows), Decimal("0"))
    ).quantize(Decimal("0.01"))
    model = _extract_model(raw_extracted_info, keywords_payload)
    summary_title = f"{model}\u62a5\u4ef7" if model else "\u62a5\u4ef7"

    return QuotationWorkbookData(
        summary_sheet_name="汇总",
        summary_title=summary_title,
        summary_meta=QuotationSummaryMeta(
            model=model,
            quote_date=_format_quote_date(generated_at),
            pricing_title=summary_title,
            table_title="\u5206\u9879\u8ba1\u7b97\u8868",
            tax_note="\u4e0d\u542b\u7a0e",
            currency_label="\u4eba\u6c11\u5e01\uff08\u5143\uff09",
            grand_total=grand_total,
        ),
        summary_rows=summary_rows,
        fixed_charge_rows=fixed_charge_rows,
        detail_sheets=detail_sheets,
    )
