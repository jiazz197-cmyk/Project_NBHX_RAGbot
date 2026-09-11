"""Composition helpers for quotation execution use cases."""

from __future__ import annotations

from app.adapters.quotation.ocr_plain_text import OcrPlainTextAdapter
from app.adapters.quotation.spec_parse_convert import SpecParseAndConvertAdapter
from app.adapters.quotation.pdf_raster import PdfFirstPageRasterAdapter
from app.adapters.quotation.temp_object_storage import QuotationTempObjectStorageAdapter
from app.adapters.quotation.workbook import OpenpyxlQuotationWorkbookAdapter
from app.adapters.sqlserver_queries import PdmMatchQueryAdapter, U8BomInventoryQueryAdapter
from app.usecases.quotation.build_workbook import BuildQuotationWorkbookUseCase
from app.usecases.quotation.execute_phase1 import ExecuteQuotationPhase1UseCase
from app.usecases.quotation.execute_phase2 import ExecuteQuotationPhase2UseCase


def build_execute_quotation_phase1_use_case() -> ExecuteQuotationPhase1UseCase:
    return ExecuteQuotationPhase1UseCase(
        ocr_text=OcrPlainTextAdapter(),
        spec_parse=SpecParseAndConvertAdapter(),
        pdm_match=PdmMatchQueryAdapter(),
        pdf_raster=PdfFirstPageRasterAdapter(),
        temp_storage=QuotationTempObjectStorageAdapter(),
    )


def build_execute_quotation_phase2_use_case() -> ExecuteQuotationPhase2UseCase:
    return ExecuteQuotationPhase2UseCase(u8_query=U8BomInventoryQueryAdapter())


def build_quotation_workbook_use_case() -> BuildQuotationWorkbookUseCase:
    return BuildQuotationWorkbookUseCase(render_port=OpenpyxlQuotationWorkbookAdapter())
