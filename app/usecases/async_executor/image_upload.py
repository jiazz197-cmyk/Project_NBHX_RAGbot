"""Image upload background job submission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.exceptions import APIException
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.ocr_async import ImageUploadJobPort

SUPPORTED_IMAGE_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/webp",
    }
)


@dataclass
class SubmitImageUploadCommand:
    current_user: CurrentUserPort
    file_data: bytes
    content_type: Optional[str]
    original_filename: Optional[str]
    file_name_prefix: Optional[str]


@dataclass
class SubmitImageUploadResult:
    task_id: str
    status: str
    message: str


class SubmitImageUploadUseCase:
    def __init__(self, jobs: ImageUploadJobPort):
        self._jobs = jobs

    def execute(self, cmd: SubmitImageUploadCommand) -> SubmitImageUploadResult:
        if cmd.content_type not in SUPPORTED_IMAGE_TYPES:
            raise APIException(
                f"不支持的图片类型: {cmd.content_type}，支持类型: {sorted(SUPPORTED_IMAGE_TYPES)}",
                status_code=400,
                error_code="INVALID_FILE_TYPE",
            )
        task_id = self._jobs.enqueue_image_upload(
            owner_id=cmd.current_user.id,
            file_data=cmd.file_data,
            original_filename=cmd.original_filename,
            content_type=cmd.content_type or "application/octet-stream",
            file_name_prefix=cmd.file_name_prefix,
        )
        return SubmitImageUploadResult(
            task_id=task_id,
            status="started",
            message="图片上传任务已启动，请通过 task_id 查询结果",
        )
