"""Adapter: PDF first page to raster image."""

from __future__ import annotations

from app.adapters.ocr.pdf2image import pdf_to_single_image
from app.ports.outbound.quotation import CancelChecker, PdfFirstPageRasterPort, RasterPageResult


class PdfFirstPageRasterAdapter(PdfFirstPageRasterPort):
    def rasterize_first_page(
        self,
        pdf_bytes: bytes,
        *,
        cancel_checker: CancelChecker = None,
    ) -> RasterPageResult:
        if cancel_checker and cancel_checker():
            from app.domain.quotation.exceptions import QuotationPipelineCancelledError

            raise QuotationPipelineCancelledError("任务已取消")
        image_bytes, suggested_name = pdf_to_single_image(
            pdf_bytes, dpi=200, quality=85, page_number=1
        )
        return RasterPageResult(image_bytes=image_bytes, suggested_filename_suffix=suggested_name)
