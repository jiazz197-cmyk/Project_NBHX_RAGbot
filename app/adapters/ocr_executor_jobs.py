"""OCR PDF/image background jobs and executor introspection."""

from __future__ import annotations

from typing import Optional

from app.core.executor import attach_future_result_logger, executor_manager
from app.core.task_owner_registry import task_owner_registry
from app.adapters.ocr.image_upload_tasks import background_image_upload_task
from app.adapters.ocr.pdf2image import get_pdf_page_count
from app.adapters.ocr.pdf_convert_tasks import background_pdf_convert_task
from app.ports.contracts.executor_async import ExecutorAsyncTaskPort
from app.ports.outbound.ocr_async import ImageUploadJobPort, PdfConvertJobPort, PdfPageCountPort


class ExecutorManagerAsyncTaskAdapter(ExecutorAsyncTaskPort):
    def get_task_future(self, task_id: str):
        return executor_manager.get_task_future(task_id)

    def get_task_owner(self, task_id: str) -> str:
        # Sync, cache-only lookup. OCR async jobs (pdf_convert_*, image_upload_*)
        # have no persistent source of truth; on cache miss the task is effectively gone.
        return task_owner_registry.peek_cache(task_id)

    def cancel_task(self, task_id: str) -> bool:
        return executor_manager.cancel_task(task_id)

    def get_active_task_count(self) -> int:
        return executor_manager.get_active_task_count()

    def get_running_task_count(self) -> int:
        return executor_manager.get_running_task_count()


class PdfConvertJobAdapter(PdfConvertJobPort):
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
        task_id = executor_manager.generate_task_id("pdf_convert")
        task_owner_registry.cache(task_id, owner_id)
        future = executor_manager.submit_task(
            task_id,
            background_pdf_convert_task,
            task_id,
            file_data,
            original_filename,
            dpi,
            quality,
            first_page,
            last_page,
            upload_to_minio,
            file_name_prefix,
            normalized_uploader,
        )
        attach_future_result_logger(future, task_id)
        return task_id


class ImageUploadJobAdapter(ImageUploadJobPort):
    def enqueue_image_upload(
        self,
        owner_id: str,
        file_data: bytes,
        original_filename: Optional[str],
        content_type: str,
        file_name_prefix: Optional[str],
    ) -> str:
        task_id = executor_manager.generate_task_id("image_upload")
        task_owner_registry.cache(task_id, owner_id)
        future = executor_manager.submit_task(
            task_id,
            background_image_upload_task,
            task_id,
            file_data,
            original_filename,
            content_type,
            file_name_prefix,
        )
        attach_future_result_logger(future, task_id)
        return task_id


class PdfPageCountAdapter(PdfPageCountPort):
    def get_pdf_page_count(self, file_data: bytes) -> int:
        return get_pdf_page_count(file_data)
