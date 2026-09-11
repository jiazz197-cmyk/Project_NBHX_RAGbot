"""File manager use cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from app.core.exceptions import PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.ports.contracts.identity import ROLE_SUPERUSER
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.file_manager import FileManagerPort
from app.ports.dto.files import FileRecordDTO

logger = get_logger("file_manager.uc")


def _ensure_file_access(dto: FileRecordDTO, current_user: CurrentUserPort, *, detail: str) -> None:
    if not current_user.is_superuser() and dto.uploader != current_user.username:
        raise PermissionDeniedError(detail)


@dataclass
class UploadFileCommand:
    file_stream: Any
    original_filename: Optional[str]
    file_size: int
    content_type: str
    uploader: str
    current_user: CurrentUserPort


class UploadFileUseCase:
    def __init__(self, files: FileManagerPort):
        self._files = files

    async def execute(self, cmd: UploadFileCommand) -> FileRecordDTO:
        if not cmd.original_filename:
            raise ValidationError("文件名不能为空")
        return await self._files.upload_stream_persist(
            file_stream=cmd.file_stream,
            original_filename=cmd.original_filename,
            file_size=cmd.file_size,
            content_type=cmd.content_type,
            uploader=cmd.uploader,
            current_user=cmd.current_user,
        )


@dataclass
class GetFileByIdQuery:
    file_id: int
    current_user: CurrentUserPort
    forbidden_detail: str


class GetFileForAccessUseCase:
    def __init__(self, files: FileManagerPort):
        self._files = files

    async def execute(self, query: GetFileByIdQuery) -> FileRecordDTO:
        dto = await self._files.get_file_or_not_found(query.file_id)
        _ensure_file_access(dto, query.current_user, detail=query.forbidden_detail)
        return dto


@dataclass
class ListFilesQuery:
    current_user: CurrentUserPort
    page: int
    page_size: int
    uploader: Optional[str]


@dataclass
class SearchFilesQuery:
    current_user: CurrentUserPort
    keyword: str
    page: int
    page_size: int


class ListFilesUseCase:
    def __init__(self, files: FileManagerPort):
        self._files = files

    async def execute(self, query: ListFilesQuery) -> Tuple[int, List[FileRecordDTO]]:
        return await self._files.list_files_page(
            current_user=query.current_user,
            page=query.page,
            page_size=query.page_size,
            uploader=query.uploader,
        )


class SearchFilesUseCase:
    def __init__(self, files: FileManagerPort):
        self._files = files

    async def execute(self, query: SearchFilesQuery) -> Tuple[int, List[FileRecordDTO]]:
        return await self._files.search_files_page(
            current_user=query.current_user,
            keyword=query.keyword,
            page=query.page,
            page_size=query.page_size,
        )


@dataclass
class DeleteFileCommand:
    file_id: int
    current_user: CurrentUserPort


class DeleteFileUseCase:
    def __init__(self, files: FileManagerPort):
        self._files = files

    async def execute(self, cmd: DeleteFileCommand) -> FileRecordDTO:
        dto = await self._files.get_file_or_not_found(cmd.file_id)
        _ensure_file_access(dto, cmd.current_user, detail="无权删除该文件")
        await self._files.delete_file_and_object(dto)
        logger.info("文件删除成功: %s (ID: %s)", dto.file_name, cmd.file_id)
        return dto


@dataclass
class BatchDeleteFilesCommand:
    file_ids: List[int]
    current_user: CurrentUserPort


@dataclass
class BatchDeleteFilesResult:
    success_count: int
    failed_count: int
    failed_ids: List[int]


class BatchDeleteFilesUseCase:
    def __init__(self, files: FileManagerPort):
        self._files = files

    async def execute(self, cmd: BatchDeleteFilesCommand) -> BatchDeleteFilesResult:
        if not cmd.current_user.is_superuser():
            raise PermissionDeniedError("仅超级管理员可批量删除")
        success_count, failed_count, failed_ids = await self._files.batch_delete_ids(cmd.file_ids)
        logger.info("批量删除完成: 成功 %s, 失败 %s", success_count, failed_count)
        return BatchDeleteFilesResult(
            success_count=success_count,
            failed_count=failed_count,
            failed_ids=failed_ids,
        )
