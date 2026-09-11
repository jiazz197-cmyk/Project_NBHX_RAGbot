"""Usecase: create quotation generation task."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.core.exceptions import APIException
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.ports.contracts.tasking import TaskDispatchPort, TaskExecutionPort, TaskStatePort
from app.ports.outbound.quotation import FileStoragePort, QuotationTaskRepoPort, QuotationTaskRetentionPort

logger = get_logger("quotation.create_task")

SUPPORTED_PDF_TYPES = {"application/pdf"}


@dataclass
class CreateQuotationTaskCommand:
    file_name: Optional[str]
    task_name: Optional[str]
    content_type: Optional[str]
    file_bytes: bytes
    max_file_size: int
    owner_id: str
    owner_username: str
    owner_ip: Optional[str]
    role_snapshot: str


@dataclass
class CreateQuotationTaskResult:
    task_id: str
    status: str
    message: str
    queue_position: int


class CreateQuotationTaskUseCase:
    def __init__(
        self,
        task_state: TaskStatePort,
        task_repo: QuotationTaskRepoPort,
        file_storage: FileStoragePort,
        task_execution: TaskExecutionPort,
        task_dispatch: TaskDispatchPort,
        retention: QuotationTaskRetentionPort,
    ):
        self._task_state = task_state
        self._task_repo = task_repo
        self._file_storage = file_storage
        self._task_execution = task_execution
        self._task_dispatch = task_dispatch
        self._retention = retention

    async def execute(self, cmd: CreateQuotationTaskCommand) -> CreateQuotationTaskResult:
        content_type = (cmd.content_type or "application/pdf").strip()
        if content_type not in SUPPORTED_PDF_TYPES:
            raise APIException("仅支持 PDF 文件", status_code=400, error_code="INVALID_FILE_TYPE")
        if not cmd.file_bytes:
            raise APIException("上传文件为空", status_code=400, error_code="EMPTY_FILE")
        if len(cmd.file_bytes) > cmd.max_file_size:
            raise APIException("文件超过大小限制", status_code=413, error_code="FILE_TOO_LARGE")

        suffix = Path(cmd.file_name or "document.pdf").suffix or ".pdf"
        unique_name = f"{utcnow_naive().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex}{suffix}"
        minio_path = f"quotation/uploads/{unique_name}"
        display_name = str(cmd.task_name or "").strip() or (cmd.file_name or unique_name)

        await self._file_storage.upload_pdf(
            object_path=minio_path,
            file_bytes=cmd.file_bytes,
            content_type=content_type,
        )

        # After the MinIO upload succeeds, any failure before create_task commits
        # would leave the uploaded PDF as an orphan (create_file_record only
        # flushes; create_task is the commit point). Wrap the persist section so
        # we can reclaim the object on failure.
        try:
            stored_file_id = await self._task_repo.create_file_record(
                file_name=cmd.file_name or unique_name,
                unique_name=unique_name,
                minio_path=minio_path,
                content_type=content_type,
                file_size=len(cmd.file_bytes),
                uploader=cmd.owner_username,
            )

            task_id = await self._task_state.create_task(
                task_type="quotation_generation",
                # owner_id is intentionally NOT replicated here: the authoritative
                # source for quotation_generation_* tasks is the `quotation_tasks` row,
                # resolved via TaskOwnerRegistry. owner_username/owner_ip stay only as
                # human-readable context for event broadcasts and logs.
                metadata={
                    "owner_username": cmd.owner_username,
                    "owner_ip": cmd.owner_ip,
                    "file_id": stored_file_id,
                    "file_name": cmd.file_name or unique_name,
                    "task_name": display_name,
                },
            )

            task = await self._task_repo.create_task(
                task_id=task_id,
                owner_id=cmd.owner_id,
                owner_username=cmd.owner_username,
                owner_ip=cmd.owner_ip,
                role_snapshot=cmd.role_snapshot,
                uploaded_file_id=stored_file_id,
                uploaded_file_name=cmd.file_name or unique_name,
                display_name=display_name,
                uploaded_file_minio_path=minio_path,
                uploaded_file_content_type=content_type,
                uploaded_file_size=len(cmd.file_bytes),
            )
        except Exception:
            # Compensating delete: reclaim the uploaded PDF.
            try:
                from app.core.async_storage import async_delete_from_minio
                await async_delete_from_minio(minio_path)
                logger.warning("创建报价任务失败后回删已上传 PDF: %s", minio_path)
            except Exception as cleanup_err:
                logger.error("回删已上传 PDF 失败 path=%s err=%s", minio_path, cleanup_err)
            raise

        self._task_execution.set_task_owner(task_id, cmd.owner_id)
        self._task_dispatch.dispatch_owner_queue(cmd.owner_id)
        task = await self._task_repo.get_task(task_id) or task

        queue_position = 0
        if task.status == "queued":
            queue_position = await self._task_repo.count_owner_queued_before(cmd.owner_id, task.created_at)

        try:
            from app.core.config import settings

            await self._retention.purge_old_terminal_tasks_global(
                max_total=settings.QUOTATION_RETENTION_MAX_TOTAL,
                target=settings.QUOTATION_RETENTION_TARGET,
            )
        except Exception as exc:
            logger.warning("Retention after create failed: %s", exc)

        return CreateQuotationTaskResult(
            task_id=task.task_id,
            status=task.status,
            message="任务创建成功",
            queue_position=queue_position,
        )
