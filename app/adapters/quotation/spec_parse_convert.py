"""Adapter: parse_spec_sheet + convert_all for quotation Phase1."""

from __future__ import annotations

from typing import Any, Dict

from app.domain.quotation.exceptions import QuotationPipelineCancelledError
from app.adapters.pdm_matcher.spec_converter import convert_all, parse_spec_sheet
from app.ports.outbound.quotation import CancelChecker, SpecParseAndConvertPort


class SpecParseAndConvertAdapter(SpecParseAndConvertPort):
    def parse_and_convert(
        self,
        *,
        ocr_text: str,
        cancel_checker: CancelChecker = None,
    ) -> Dict[str, Any]:
        if cancel_checker and cancel_checker():
            raise QuotationPipelineCancelledError("任务已取消")

        params = parse_spec_sheet(ocr_text)
        specs = convert_all(params)

        keywords_payload = {"keywords": specs}

        return {
            "params": params,
            "specs": specs,
            "keywords_payload": keywords_payload,
        }