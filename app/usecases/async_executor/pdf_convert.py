"""PDF convert submission and page count."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.core.exceptions import APIException
from app.core.security import normalize_self_uploader
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.ocr_async import PdfConvertJobPort, PdfPageCountPort

SUPPORTED_PDF_TYPES = frozenset({"application/pdf"})


@dataclass
class SubmitPdfConvertCommand:
    current_user: CurrentUserPort
    file_data: bytes
    content_type: Optional[str]
    original_filename: Optional[str]
    dpi: int
    quality: int
    first_page: Optional[int]
    last_page: Optional[int]
    upload_to_minio: bool
    file_name_prefix: Optional[str]
    uploader_query: str


@dataclass
class SubmitPdfConvertResult:
    task_id: str
    status: str
    message: str


class SubmitPdfConvertUseCase:
    def __init__(self, jobs: PdfConvertJobPort):
        self._jobs = jobs

    def execute(self, cmd: SubmitPdfConvertCommand) -> SubmitPdfConvertResult:
        if cmd.content_type not in SUPPORTED_PDF_TYPES:
            raise APIException(
                f"不支持的文件类型: {cmd.content_type}，仅支持 PDF 文件",
                status_code=400,
                error_code="INVALID_FILE_TYPE",
            )
        file_size_mb = len(cmd.file_data) / (1024 * 1024)
        if file_size_mb > 100:
            raise APIException(
                f"文件过大: {file_size_mb:.2f} MB，最大支持 100 MB",
                status_code=400,
                error_code="FILE_TOO_LARGE",
            )
        normalized_uploader = normalize_self_uploader(cmd.uploader_query, cmd.current_user)
        task_id = self._jobs.enqueue_pdf_convert(
            owner_id=cmd.current_user.id,
            file_data=cmd.file_data,
            original_filename=cmd.original_filename,
            dpi=cmd.dpi,
            quality=cmd.quality,
            first_page=cmd.first_page,
            last_page=cmd.last_page,
            upload_to_minio=cmd.upload_to_minio,
            file_name_prefix=cmd.file_name_prefix,
            normalized_uploader=normalized_uploader,
        )
        return SubmitPdfConvertResult(
            task_id=task_id,
            status="started",
            message="PDF 转图片任务已启动，请通过 task_id 查询结果",
        )


@dataclass
class PdfPageCountCommand:
    file_data: bytes
    content_type: Optional[str]
    original_filename: Optional[str]


@dataclass
class PdfPageCountResult:
    filename: Optional[str]
    total_pages: int
    message: str


class PdfPageCountUseCase:
    def __init__(self, pdf_pages: PdfPageCountPort):
        self._pdf_pages = pdf_pages

    def execute(self, cmd: PdfPageCountCommand) -> PdfPageCountResult:
        if cmd.content_type not in SUPPORTED_PDF_TYPES:
            raise APIException(
                f"不支持的文件类型: {cmd.content_type}，仅支持 PDF 文件",
                status_code=400,
                error_code="INVALID_FILE_TYPE",
            )
        max_size = settings.MAX_FILE_SIZE
        if len(cmd.file_data) > max_size:
            raise APIException("文件过大", status_code=413, error_code="FILE_TOO_LARGE")
        count = self._pdf_pages.get_pdf_page_count(cmd.file_data)
        return PdfPageCountResult(
            filename=cmd.original_filename,
            total_pages=count,
            message=f"PDF 文件共有 {count} 页",
        )
