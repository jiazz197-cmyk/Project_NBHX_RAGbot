"""OCR / PDF async job outbound ports (enqueue + PDF introspection)."""

from __future__ import annotations

from typing import Optional, Protocol


class PdfConvertJobPort(Protocol):
    def enqueue_pdf_convert(
        self,
        owner_id: str,
        file_data: bytes,
        original_filename: Optional[str],
        dpi: int,
        quality: int,
        first_page: Optional[int],
        last_page: Optional[int],
        upload_to_minio: bool,
        file_name_prefix: Optional[str],
        normalized_uploader: str,
    ) -> str:
        ...


class ImageUploadJobPort(Protocol):
    def enqueue_image_upload(
        self,
        owner_id: str,
        file_data: bytes,
        original_filename: Optional[str],
        content_type: str,
        file_name_prefix: Optional[str],
    ) -> str:
        ...


class PdfPageCountPort(Protocol):
    def get_pdf_page_count(self, file_data: bytes) -> int:
        ...
