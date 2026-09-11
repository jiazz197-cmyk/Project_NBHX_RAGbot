"""Adapter: upload temporary quotation images to object storage."""

from __future__ import annotations

from app.adapters.ocr.image2url import upload_file_to_minio
from app.ports.outbound.quotation import (
    CancelChecker,
    QuotationTempObjectStoragePort,
    TempObjectUploadResult,
)


class QuotationTempObjectStorageAdapter(QuotationTempObjectStoragePort):
    def upload_temp_image(
        self,
        *,
        image_bytes: bytes,
        object_path: str,
        cancel_checker: CancelChecker = None,
    ) -> TempObjectUploadResult:
        if cancel_checker and cancel_checker():
            from app.domain.quotation.exceptions import QuotationPipelineCancelledError

            raise QuotationPipelineCancelledError("任务已取消")
        public_url = upload_file_to_minio(image_bytes, object_path)
        return TempObjectUploadResult(public_url=public_url, object_path=object_path)
