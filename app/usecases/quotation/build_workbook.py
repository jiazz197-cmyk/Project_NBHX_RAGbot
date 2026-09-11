"""Usecase: build quotation workbook bytes from summary selection + grouped U8 data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.domain.quotation.workbook import build_quotation_workbook_data
from app.ports.outbound.quotation_workbook import QuotationWorkbookRenderPort
from app.ports.dto.quotation_workbook import QuotationWorkbookExport


@dataclass
class BuildQuotationWorkbookCommand:
    u8_result_by_type: Dict[str, Any]
    summary_selection_items: Any = None
    raw_extracted_info: Any = None
    keywords_payload: Any = None
    generated_at: datetime | None = None
    partid_quantities: Dict[str, float] | None = None
    cancel_checker: Optional[Callable[[], bool]] = None


class BuildQuotationWorkbookUseCase:
    def __init__(self, render_port: QuotationWorkbookRenderPort):
        self._render_port = render_port

    def execute(self, cmd: BuildQuotationWorkbookCommand) -> QuotationWorkbookExport:
        workbook_data = build_quotation_workbook_data(
            u8_result_by_type=cmd.u8_result_by_type,
            summary_selection_items=cmd.summary_selection_items,
            raw_extracted_info=cmd.raw_extracted_info,
            keywords_payload=cmd.keywords_payload,
            generated_at=cmd.generated_at,
            partid_quantities=cmd.partid_quantities,
        )
        return self._render_port.export_workbook(
            workbook_data, cancel_checker=cmd.cancel_checker
        )
