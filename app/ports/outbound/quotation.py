"""Quotation generation outbound ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol

from app.ports.dto.quotation import QuotationSummarySelectionItem, QuotationTaskSnapshot

CancelChecker = Optional[Callable[[], bool]]
ProgressCallback = Optional[Callable[[int, str], None]]


# ── OCR 纯文本提取 ──────────────────────────────────────────

@dataclass
class OcrTextExtractionResult:
    text: str
    extract_method: str  # "pdftotext" | "dotsocr" | "failed"
    pdftotext_chars: int = 0
    dotsocr_chars: int = 0


class OcrPlainTextPort(Protocol):
    def extract_text(
        self, *, pdf_bytes: bytes, cancel_checker: CancelChecker = None, ocr_dpi: int = 200,
    ) -> OcrTextExtractionResult: ...


# ── Spec 解析 + 转换 ──────────────────────────────────────

class SpecParseAndConvertPort(Protocol):
    def parse_and_convert(
        self, *, ocr_text: str, cancel_checker: CancelChecker = None,
    ) -> Dict[str, Any]: ...


@dataclass
class RasterPageResult:
    """First-page rasterization output."""

    image_bytes: bytes
    suggested_filename_suffix: str


@dataclass
class TempObjectUploadResult:
    """Public URL and storage path for a temporary uploaded object."""

    public_url: str
    object_path: str


class FileStoragePort(Protocol):
    """Object storage abstraction for quotation files."""

    async def upload_pdf(
        self,
        *,
        object_path: str,
        file_bytes: bytes,
        content_type: str,
    ) -> None:
        ...


class QuotationTaskRepoPort(Protocol):
    """Persistence boundary for quotation task + file metadata."""

    async def create_file_record(
        self,
        *,
        file_name: str,
        unique_name: str,
        minio_path: str,
        content_type: str,
        file_size: int,
        uploader: str,
    ) -> int:
        ...

    async def create_task(
        self,
        *,
        task_id: str,
        owner_id: str,
        owner_username: str,
        owner_ip: Optional[str],
        role_snapshot: str,
        uploaded_file_id: int,
        uploaded_file_name: str,
        display_name: str,
        uploaded_file_minio_path: str,
        uploaded_file_content_type: str,
        uploaded_file_size: int,
    ) -> QuotationTaskSnapshot:
        ...

    async def get_task(self, task_id: str) -> Optional[QuotationTaskSnapshot]:
        ...

    async def patch_task(self, task_id: str, updates: Dict[str, Any]) -> QuotationTaskSnapshot:
        ...

    async def count_owner_queued_before(self, owner_id: str, created_at: datetime) -> int:
        ...

    async def cleanup_task_files(self, task_id: str) -> Dict[str, Any]:
        ...


class QuotationApprovalSelectionPort(Protocol):
    """Persistence boundary for approval-driven summary selection snapshots."""

    async def save_approved_selection(
        self,
        *,
        task_id: str,
        approved_partids: list[str],
        summary_selection_items: list[QuotationSummarySelectionItem],
    ) -> None:
        ...


class PdfFirstPageRasterPort(Protocol):
    """Rasterize page 1 of a PDF to image bytes."""

    def rasterize_first_page(
        self,
        pdf_bytes: bytes,
        *,
        cancel_checker: CancelChecker = None,
    ) -> RasterPageResult:
        ...


class QuotationTempObjectStoragePort(Protocol):
    """Upload temporary quotation artifacts (e.g. page-1 JPEG) and return a fetchable URL."""

    def upload_temp_image(
        self,
        *,
        image_bytes: bytes,
        object_path: str,
        cancel_checker: CancelChecker = None,
    ) -> TempObjectUploadResult:
        ...


@dataclass
class DispatchCandidate:
    """Task payload required by executor submission."""

    task_id: str
    owner_id: str


class QuotationDispatchPort(Protocol):
    """Abstraction for dispatching queued quotation tasks to running state."""

    async def dequeue_for_owner(self, owner_id: str) -> list[DispatchCandidate]:
        ...

    async def list_queued_owners(self) -> list[str]:
        """Distinct owner_ids with queued tasks (for cross-owner IP-quota sweep)."""
        ...


class QuotationTaskPurgePort(Protocol):
    """Abstraction for purging quotation task records and associated resources."""

    async def purge_task(self, task_id: str, *, allow_non_terminal: bool = False) -> dict:
        ...


class QuotationTaskRetentionPort(Protocol):
    """Abstraction for quota-driven retention of quotation tasks."""

    async def purge_old_terminal_tasks_global(self, max_total: int = 100, target: int = 50) -> int:
        ...

    async def expire_awaiting_approval_tasks(self, ttl_hours: int = 24) -> int:
        ...

    async def reclaim_stale_running_tasks(self, timeout_sec: int = 1800) -> int:
        ...
