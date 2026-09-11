"""Usecase: execute quotation Phase1 (PDF → OCR text → parse → PDM match)."""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from app.core.config import settings
from app.core.logging import get_logger
from app.core.storage import delete_from_minio
from app.domain.quotation.exceptions import QuotationPipelineCancelledError
from app.domain.quotation.pdm_result import collect_pdm_partids, summarize_pdm_query_params
from app.domain.quotation.results import Phase1Result
from app.ports.outbound.quotation import (
    CancelChecker,
    OcrPlainTextPort,
    PdfFirstPageRasterPort,
    ProgressCallback,
    QuotationTempObjectStoragePort,
    SpecParseAndConvertPort,
)
from app.domain.exceptions import QueryCancelledError
from app.ports.outbound.sqlserver_queries import PdmMatchQueryPort
from app.ports.dto.sqlserver_queries import PdmMatchCommand
from app.usecases.quotation._utils import response_to_dict

logger = get_logger("quotation.phase1")


@dataclass
class ExecuteQuotationPhase1Command:
    pdf_bytes: bytes
    original_filename: str
    progress_callback: ProgressCallback = None
    cancel_checker: CancelChecker = None


class ExecuteQuotationPhase1UseCase:
    def __init__(
        self,
        ocr_text: OcrPlainTextPort,
        spec_parse: SpecParseAndConvertPort,
        pdm_match: PdmMatchQueryPort,
        pdf_raster: PdfFirstPageRasterPort,
        temp_storage: QuotationTempObjectStoragePort,
    ):
        self._ocr_text = ocr_text
        self._spec_parse = spec_parse
        self._pdm_match = pdm_match
        self._pdf_raster = pdf_raster
        self._temp_storage = temp_storage

    def _emit_progress(self, cb: ProgressCallback, progress: int, message: str) -> None:
        if cb:
            cb(progress, message)

    def _check_cancel(self, cancel_checker: CancelChecker) -> None:
        if cancel_checker and cancel_checker():
            raise QuotationPipelineCancelledError("任务已取消")

    def execute(self, cmd: ExecuteQuotationPhase1Command) -> Phase1Result:
        cb = cmd.progress_callback
        cancel = cmd.cancel_checker

        # Step 1: 提取 PDF 首页截图并上传 MinIO（前端预览用）
        self._emit_progress(cb, 10, "正在将PDF第1页转换为图片")
        self._check_cancel(cancel)
        raster = self._pdf_raster.rasterize_first_page(cmd.pdf_bytes, cancel_checker=cancel)

        self._emit_progress(cb, 15, "正在上传中间图片到MinIO")
        self._check_cancel(cancel)
        temp_image_minio_path = f"temp/quotation/{uuid.uuid4().hex}_{cmd.original_filename}_page_001.jpg"
        upload = self._temp_storage.upload_temp_image(
            image_bytes=raster.image_bytes,
            object_path=temp_image_minio_path,
            cancel_checker=cancel,
        )

        # Steps 2-4 may fail AFTER the temp image is already on MinIO. If they do,
        # the worker's cleanup cannot recover the path (Phase1Result is never
        # returned), so the temp image would orphan. Delete it here on failure and
        # re-raise so the worker still records the failure.
        try:
            # Step 2: OCR 纯文本提取
            self._emit_progress(cb, 25, "正在提取PDF文字")
            self._check_cancel(cancel)
            text_result = self._ocr_text.extract_text(
                pdf_bytes=cmd.pdf_bytes,
                cancel_checker=cancel,
            )

            if not text_result.text:
                raise QuotationPipelineCancelledError(
                    f"PDF 文字提取失败 (method={text_result.extract_method})"
                )

            # Step 3: 解析 + 转换
            self._emit_progress(cb, 35, "正在解析规格参数并生成部件列表")
            self._check_cancel(cancel)
            parse_result = self._spec_parse.parse_and_convert(
                ocr_text=text_result.text,
                cancel_checker=cancel,
            )

            specs = parse_result["specs"]
            keywords_payload = parse_result["keywords_payload"]

            # Step 4: PDM 匹配查询
            pdm_query_summary = summarize_pdm_query_params(keywords_payload)
            self._emit_progress(
                cb, 40, f"正在执行 PDM 匹配查询 | 参数: {pdm_query_summary}"
            )
            self._check_cancel(cancel)

            try:
                response = self._pdm_match.run(
                    PdmMatchCommand(keywords=specs),
                    cancel_checker=cancel,
                )
            except QueryCancelledError as exc:
                raise QuotationPipelineCancelledError("PDM 查询已取消") from exc

            self._check_cancel(cancel)
            pdm_result = response_to_dict(response)
            pdm_partids = collect_pdm_partids(pdm_result)
        except Exception:
            # Best-effort cleanup of the just-uploaded temp image; never mask the
            # original exception.
            try:
                delete_from_minio(upload.object_path)
            except Exception as cleanup_err:
                logger.warning(
                    "Phase1 失败后清理临时图失败 path=%s err=%s",
                    upload.object_path, cleanup_err,
                )
            raise


        self._emit_progress(cb, 50, "PDM 查询完成，等待用户审核")

        return Phase1Result(
            keywords_payload=keywords_payload,
            pdm_result=pdm_result,
            pdm_partids=pdm_partids,
            temp_image_minio_path=upload.object_path,
            temp_image_url=upload.public_url,
            raw_extracted_info=parse_result.get("params", {}),
        )
