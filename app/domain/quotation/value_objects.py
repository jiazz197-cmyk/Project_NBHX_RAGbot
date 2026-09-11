"""Quotation domain value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass
class QuotationSummarySelectionItem:
    """A single approved selection item bridging Phase1 result to Phase2 query."""

    selection_index: int
    partid: str
    u8_parent_inv_code: str
    type_name: str
    pdm_name: str
    query_index: Optional[int]
    query_keywords: list[str]
    query_expanded_keywords: list[str]
    matched_pdm_row: bool


@dataclass(frozen=True)
class QuotationSummaryRow:
    part_no: str
    name: str
    quantity_display: str
    unit_price: Decimal
    amount: Decimal
    remark: str = ""
    detail_sheet_name: str = ""


@dataclass(frozen=True)
class QuotationFixedChargeRow:
    name: str
    quantity_display: str = ""
    unit_price: str = ""
    amount: str = ""
    remark: str = ""


@dataclass(frozen=True)
class QuotationDetailSheet:
    sheet_name: str
    rows: List[Dict[str, Any]]
    total_amount: Decimal


@dataclass(frozen=True)
class QuotationSummaryMeta:
    model: str = ""
    quote_date: str = ""
    pricing_title: str = ""
    table_title: str = ""
    tax_note: str = ""
    currency_label: str = ""
    grand_total: Decimal = Decimal("0")


@dataclass(frozen=True)
class QuotationWorkbookData:
    summary_sheet_name: str
    summary_title: str
    summary_meta: QuotationSummaryMeta
    summary_rows: List[QuotationSummaryRow] = field(default_factory=list)
    fixed_charge_rows: List[QuotationFixedChargeRow] = field(default_factory=list)
    detail_sheets: List[QuotationDetailSheet] = field(default_factory=list)


@dataclass(frozen=True)
class QuotationWorkbookExport:
    content: bytes
