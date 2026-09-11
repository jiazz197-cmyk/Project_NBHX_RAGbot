"""Adapters for document processing (registration + executor)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.doc_processing.document_task_runner import process_documents_background
from app.core.async_storage import (
    async_delete_from_minio,
    async_stat_object,
    async_upload_stream_to_minio,
)
from app.core.executor import attach_future_result_logger, executor_manager
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.models.orm.file_resource import FileResource
from app.ports.outbound.document_processing import (
    DocumentProcessWorkerPort,
    DocumentRegistrationPort,
)

logger = get_logger("document_processing")


class SqlAlchemyDocumentRegistrationAdapter(DocumentRegistrationPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def register_uploaded_files(self, files: Any, normalized_uploader: str) -> List[int]:
        """Stream uploads to MinIO under documents/ and persist FileResource rows.

        files: iterable of Starlette UploadFile-like (filename, file, content_type, size).
        """
        file_ids: List[int] = []
        for file in files:
            if not getattr(file, "filename", None):
                logger.warning("跳过没有文件名的文件")
                continue
            minio_path: Optional[str] = None
            try:
                suffix = Path(file.filename).suffix
                unique_id = uuid.uuid4().hex
                timestamp = utcnow_naive().strftime("%Y%m%d_%H%M%S")
                unique_name = f"{timestamp}_{unique_id}{suffix}"
                minio_path = f"documents/{unique_name}"
                file_size = getattr(file, "size", None) or -1
                content_type = file.content_type or "application/octet-stream"
                await async_upload_stream_to_minio(file.file, minio_path, file_size, content_type)
                if file_size == -1:
                    try:
                        stat = await async_stat_object(minio_path)
                        file_size = stat.size
                    except Exception:
                        file_size = 0
                file_record = FileResource(
                    file_name=file.filename,
                    unique_name=unique_name,
                    minio_object_path=minio_path,
                    content_type=content_type,
                    file_size=file_size,
                    uploader=normalized_uploader,
                )
                self._db.add(file_record)
                await self._db.commit()
                await self._db.refresh(file_record)
                file_ids.append(file_record.id)
                logger.info("文件上传成功: %s (ID: %s)", file.filename, file_record.id)
            except Exception as e:
                logger.error("上传文件失败 %s: %s", getattr(file, "filename", ""), e, exc_info=True)
                await self._db.rollback()
                # Compensating delete: if the MinIO upload succeeded but the DB commit
                # failed, reclaim the object so it does not orphan. minio_path is only
                # defined after the upload assignment above.
                try:
                    await async_delete_from_minio(minio_path)
                    logger.warning("DB 落库失败后回删 MinIO 对象: %s", minio_path)
                except Exception as cleanup_err:
                    logger.error("回删 MinIO 对象失败 path=%s err=%s", minio_path, cleanup_err)
        return file_ids


class DocumentProcessWorkerAdapter(DocumentProcessWorkerPort):
    def submit_process_documents(
        self,
        task_id: str,
        file_ids: List[int],
        instance_id: int,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        executor_manager.submit_task(
            task_id,
            process_documents_background,
            task_id,
            file_ids,
            instance_id,
            chunk_size,
            chunk_overlap,
        )
        future = executor_manager.get_task_future(task_id)
        if future is not None:
            attach_future_result_logger(future, task_id)
