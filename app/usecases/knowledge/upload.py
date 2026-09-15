"""Knowledge upload use cases — document & Excel-db ingestion orchestration."""

from __future__ import annotations

from typing import List, Optional

from app.core.config import settings
from app.core.exceptions import KnowledgeFileNameConflictError, ValidationError
from app.core.logging import get_logger
from app.domain.knowledge.collections import (
    EXCEL_DB_COLLECTION_NAME,
    KNOWLEDGE_COLLECTION_NAME,
)
from app.domain.knowledge.upload_rules import (
    CONFLICT_REPLACE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    VALID_ON_CONFLICT_VALUES,
    is_document_allowed,
    is_excel_db_allowed,
)
from app.ports.dto.knowledge_upload import (
    ExcelDbUploadCommand,
    KnowledgeUploadCommand,
    KnowledgeUploadResult,
)
from app.ports.outbound.knowledge_metadata import KnowledgeMetadataPort
from app.usecases.document_processing.submit import (
    SubmitDocumentProcessingCommand,
    SubmitDocumentProcessingUseCase,
)

logger = get_logger("knowledge.upload")


def _file_name(upload_file) -> str:
    name = getattr(upload_file, "filename", None)
    return str(name or "").strip()


def _file_size(upload_file) -> int:
    """返回上传文件字节数；读取失败返回 -1（不阻塞流程，由其他校验兜底）。"""
    try:
        inner = upload_file.file
        pos = inner.tell()
        inner.seek(0, 2)
        size = inner.tell()
        inner.seek(pos)
        return int(size)
    except Exception:  # noqa: BLE001 - 大小读取失败不应阻塞上传
        return -1


def _uploader_of(cmd) -> str:
    user = cmd.current_user
    if user is None:
        raise ValidationError("缺少当前用户信息")
    username = getattr(user, "username", "") or getattr(user, "id", "")
    if not username:
        raise ValidationError("当前用户缺少 username")
    return str(username)


def _validate_on_conflict(on_conflict: Optional[str]) -> None:
    if on_conflict is not None and on_conflict not in VALID_ON_CONFLICT_VALUES:
        raise ValidationError(
            f"on_conflict 仅支持: {sorted(VALID_ON_CONFLICT_VALUES)}"
        )


async def _resolve_conflicts(
    metadata_port: KnowledgeMetadataPort,
    collection: str,
    files: List,
    on_conflict: Optional[str],
) -> None:
    """按 on_conflict 处理同名文件：未指定且同名 → 409；replace → 删旧块；append → 直接继续。"""
    for upload_file in files:
        name = _file_name(upload_file)
        if not name:
            continue
        conflict = await metadata_port.find_conflict(collection, name)
        if conflict is None:
            continue
        if on_conflict is None:
            raise KnowledgeFileNameConflictError(
                f"已存在同名文件: {name}",
                details={
                    "file_name": conflict.file_name,
                    "uploader": conflict.uploader,
                    "upload_time": conflict.upload_time,
                    "chunk_count": conflict.chunk_count,
                },
            )
        if on_conflict == CONFLICT_REPLACE:
            deleted = await metadata_port.delete_chunks_by_file_name(collection, name)
            logger.info(
                "替换同名文件旧块: collection=%s file=%s deleted_chunks=%s",
                collection, name, deleted,
            )
        # append：不删旧块，直接写入新块


class UploadKnowledgeDocumentUseCase:
    """文档知识上传：白名单校验 → 同名预检 → 复用文档处理异步流水线。"""

    def __init__(
        self,
        submit_use_case: SubmitDocumentProcessingUseCase,
        metadata_port: KnowledgeMetadataPort,
    ):
        self._submit = submit_use_case
        self._metadata = metadata_port

    async def execute(self, cmd: KnowledgeUploadCommand) -> KnowledgeUploadResult:
        if not cmd.files:
            raise ValidationError("至少需要上传一个文件")
        _validate_on_conflict(cmd.on_conflict)

        for upload_file in cmd.files:
            name = _file_name(upload_file)
            if not is_document_allowed(name):
                if is_excel_db_allowed(name):
                    raise ValidationError(
                        f"文件「{name}」为 Excel 类文件，请使用 Excel 数据库上传入口"
                    )
                raise ValidationError(f"不支持的文件类型: {name}")
            size = _file_size(upload_file)
            max_document_bytes = settings.KNOWLEDGE_MAX_DOCUMENT_FILE_SIZE_MB * 1024 * 1024
            if size > max_document_bytes:
                raise ValidationError(
                    f"文件「{name}」超过大小上限 {settings.KNOWLEDGE_MAX_DOCUMENT_FILE_SIZE_MB}MB"
                )

        await _resolve_conflicts(
            self._metadata, KNOWLEDGE_COLLECTION_NAME, cmd.files, cmd.on_conflict
        )

        result = await self._submit.execute(
            SubmitDocumentProcessingCommand(
                files=cmd.files,
                collection=KNOWLEDGE_COLLECTION_NAME,
                chunk_size=DEFAULT_CHUNK_SIZE,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                normalized_uploader=_uploader_of(cmd),
                current_user=cmd.current_user,
            )
        )
        logger.info(
            "知识库文档上传任务已提交: files=%s collection=%s task=%s",
            len(cmd.files), KNOWLEDGE_COLLECTION_NAME, result.task_id,
        )
        return KnowledgeUploadResult(
            task_id=result.task_id,
            status=result.status,
            message=result.message,
            files_count=result.files_count,
            collection=KNOWLEDGE_COLLECTION_NAME,
        )


class UploadExcelDbUseCase:
    """Excel 类数据库上传：类型校验 → 同名预检 → 复用流水线（多 sheet 由 collection 驱动）。"""

    def __init__(
        self,
        submit_use_case: SubmitDocumentProcessingUseCase,
        metadata_port: KnowledgeMetadataPort,
    ):
        self._submit = submit_use_case
        self._metadata = metadata_port

    async def execute(self, cmd: ExcelDbUploadCommand) -> KnowledgeUploadResult:
        if not cmd.files:
            raise ValidationError("至少需要上传一个文件")
        _validate_on_conflict(cmd.on_conflict)

        for upload_file in cmd.files:
            name = _file_name(upload_file)
            if not is_excel_db_allowed(name):
                raise ValidationError(f"仅支持 xlsx / xls 文件，收到: {name}")
            size = _file_size(upload_file)
            max_excel_bytes = settings.KNOWLEDGE_MAX_EXCEL_FILE_SIZE_MB * 1024 * 1024
            if size > max_excel_bytes:
                raise ValidationError(
                    f"文件「{name}」超过大小上限 {settings.KNOWLEDGE_MAX_EXCEL_FILE_SIZE_MB}MB"
                )

        await _resolve_conflicts(
            self._metadata, EXCEL_DB_COLLECTION_NAME, cmd.files, cmd.on_conflict
        )

        result = await self._submit.execute(
            SubmitDocumentProcessingCommand(
                files=cmd.files,
                collection=EXCEL_DB_COLLECTION_NAME,
                chunk_size=DEFAULT_CHUNK_SIZE,
                chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                normalized_uploader=_uploader_of(cmd),
                current_user=cmd.current_user,
            )
        )
        logger.info(
            "Excel 数据库上传任务已提交: files=%s collection=%s task=%s",
            len(cmd.files), EXCEL_DB_COLLECTION_NAME, result.task_id,
        )
        return KnowledgeUploadResult(
            task_id=result.task_id,
            status=result.status,
            message=result.message,
            files_count=result.files_count,
            collection=EXCEL_DB_COLLECTION_NAME,
        )
