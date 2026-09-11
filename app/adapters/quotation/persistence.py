"""Quotation adapters: MinIO upload, ORM repo."""

from __future__ import annotations

from datetime import datetime
from dataclasses import asdict
from io import BytesIO
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIException
from app.core.async_storage import async_upload_stream_to_minio
from app.core.storage import MinioUploadError
from app.core.quotation_task_cleanup import (
    safe_cleanup_quotation_task_files_async,
)
from app.domain.quotation.entities import QuotationTaskStatus
from app.models.orm.file_resource import FileResource
from app.models.orm.quotation_task import QuotationTask
from app.ports.outbound.quotation import (
    FileStoragePort,
    QuotationApprovalSelectionPort,
    QuotationTaskRepoPort,
)
from app.ports.dto.quotation import QuotationSummarySelectionItem, QuotationTaskSnapshot


class MinioFileStorageAdapter(FileStoragePort):
    async def upload_pdf(self, *, object_path: str, file_bytes: bytes, content_type: str) -> None:
        try:
            await async_upload_stream_to_minio(
                file_stream=BytesIO(file_bytes),
                file_name=object_path,
                file_size=len(file_bytes),
                content_type=content_type,
            )
        except MinioUploadError as exc:
            raise APIException(
                "上传文件到 MinIO 失败",
                status_code=500,
                error_code="MINIO_UPLOAD_FAILED",
            ) from exc


class SqlAlchemyQuotationTaskRepoAdapter(QuotationTaskRepoPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    @staticmethod
    def _to_snapshot(task: QuotationTask) -> QuotationTaskSnapshot:
        return QuotationTaskSnapshot(
            task_id=task.task_id,
            owner_id=task.owner_id,
            owner_username=task.owner_username,
            owner_ip=task.owner_ip,
            role_snapshot=task.role_snapshot,
            status=task.status,
            progress=task.progress,
            message=task.message,
            uploaded_file_id=task.uploaded_file_id,
            uploaded_file_name=task.uploaded_file_name,
            display_name=task.display_name,
            uploaded_file_minio_path=task.uploaded_file_minio_path,
            uploaded_file_content_type=task.uploaded_file_content_type,
            uploaded_file_size=task.uploaded_file_size,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            result_payload=task.result_payload,
            error=task.error,
        )

    async def _get_task_entity(self, task_id: str) -> Optional[QuotationTask]:
        result = await self._db.execute(
            select(QuotationTask).where(QuotationTask.task_id == task_id)
        )
        return result.scalars().first()

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
        file_record = FileResource(
            file_name=file_name,
            unique_name=unique_name,
            minio_object_path=minio_path,
            content_type=content_type,
            file_size=file_size,
            uploader=uploader,
        )
        self._db.add(file_record)
        await self._db.flush()
        return int(file_record.id)

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
        quotation_task = QuotationTask(
            task_id=task_id,
            owner_id=owner_id,
            owner_username=owner_username,
            owner_ip=(owner_ip or None),
            role_snapshot=role_snapshot,
            status=QuotationTaskStatus.queued.value,
            progress=0,
            message="任务已排队",
            uploaded_file_id=uploaded_file_id,
            uploaded_file_name=uploaded_file_name,
            display_name=display_name,
            uploaded_file_minio_path=uploaded_file_minio_path,
            uploaded_file_content_type=uploaded_file_content_type,
            uploaded_file_size=uploaded_file_size,
        )
        self._db.add(quotation_task)
        await self._db.commit()
        await self._db.refresh(quotation_task)
        return self._to_snapshot(quotation_task)

    async def get_task(self, task_id: str) -> Optional[QuotationTaskSnapshot]:
        task = await self._get_task_entity(task_id)
        if task is None:
            return None
        return self._to_snapshot(task)

    async def patch_task(self, task_id: str, updates: Dict[str, Any]) -> QuotationTaskSnapshot:
        task = await self._get_task_entity(task_id)
        if task is None:
            raise APIException("任务不存在", status_code=404, error_code="NOT_FOUND")

        for key, value in updates.items():
            setattr(task, key, value)

        await self._db.commit()
        await self._db.refresh(task)
        return self._to_snapshot(task)

    async def count_owner_queued_before(self, owner_id: str, created_at: datetime) -> int:
        result = await self._db.execute(
            select(func.count())
            .select_from(QuotationTask)
            .where(
                QuotationTask.owner_id == owner_id,
                QuotationTask.status == QuotationTaskStatus.queued.value,
                QuotationTask.created_at <= created_at,
            )
        )
        return int(result.scalar_one())

    async def cleanup_task_files(self, task_id: str) -> Dict[str, Any]:
        task = await self._get_task_entity(task_id)
        if task is None:
            raise APIException("任务不存在", status_code=404, error_code="NOT_FOUND")
        return await safe_cleanup_quotation_task_files_async(self._db, task, task_id)


class ResultPayloadQuotationApprovalSelectionAdapter(QuotationApprovalSelectionPort):
    def __init__(self, db: AsyncSession):
        self._db = db

    async def _get_task_entity(self, task_id: str) -> Optional[QuotationTask]:
        result = await self._db.execute(
            select(QuotationTask).where(QuotationTask.task_id == task_id)
        )
        return result.scalars().first()

    async def save_approved_selection(
        self,
        *,
        task_id: str,
        approved_partids: list[str],
        summary_selection_items: list[QuotationSummarySelectionItem],
        manual_partid_types: dict[str, str] | None = None,
    ) -> None:
        task = await self._get_task_entity(task_id)
        if task is None:
            raise APIException("任务不存在", status_code=404, error_code="NOT_FOUND")

        payload = dict(task.result_payload or {})
        payload["approved_partids"] = approved_partids
        payload["summary_selection_items"] = [asdict(item) for item in summary_selection_items]
        if manual_partid_types:
            payload["manual_partid_types"] = manual_partid_types
        task.result_payload = payload
        await self._db.commit()
