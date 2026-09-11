"""Submit document processing batch task."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.ports.contracts.identity import CurrentUserPort
from app.ports.contracts.tasking import TaskExecutionPort, TaskStatePort
from app.ports.outbound.document_processing import DocumentProcessWorkerPort, DocumentRegistrationPort

logger = get_logger("document_processing.uc")


@dataclass
class SubmitDocumentProcessingCommand:
    files: List[Any]
    instance_id: int
    chunk_size: int
    chunk_overlap: int
    normalized_uploader: str
    current_user: CurrentUserPort


@dataclass
class SubmitDocumentProcessingResult:
    task_id: str
    status: str
    message: str
    files_count: int


class SubmitDocumentProcessingUseCase:
    def __init__(
        self,
        registration: DocumentRegistrationPort,
        task_state: TaskStatePort,
        task_execution: TaskExecutionPort,
        worker: DocumentProcessWorkerPort,
    ):
        self._registration = registration
        self._task_state = task_state
        self._task_execution = task_execution
        self._worker = worker

    async def execute(self, cmd: SubmitDocumentProcessingCommand) -> SubmitDocumentProcessingResult:
        if not cmd.files:
            raise ValidationError("至少需要上传一个文件")
        logger.info("收到文档处理请求: %s 个文件, instance_id=%s", len(cmd.files), cmd.instance_id)
        file_ids = await self._registration.register_uploaded_files(cmd.files, cmd.normalized_uploader)
        if not file_ids:
            raise ValidationError("没有成功上传任何文件")
        task_id = await self._task_state.create_task(
            task_type="doc_process",
            metadata={
                "file_ids": file_ids,
                "instance_id": cmd.instance_id,
                "chunk_size": cmd.chunk_size,
                "chunk_overlap": cmd.chunk_overlap,
                "uploader": cmd.normalized_uploader,
                "owner_id": cmd.current_user.id,
                "files_count": len(file_ids),
            },
        )
        logger.info("创建文档处理任务: %s, 文件数: %s", task_id, len(file_ids))
        self._task_execution.set_task_owner(task_id, cmd.current_user.id)
        self._worker.submit_process_documents(
            task_id,
            file_ids,
            cmd.instance_id,
            cmd.chunk_size,
            cmd.chunk_overlap,
        )
        return SubmitDocumentProcessingResult(
            task_id=task_id,
            status="pending",
            message="任务已创建，开始处理",
            files_count=len(file_ids),
        )
