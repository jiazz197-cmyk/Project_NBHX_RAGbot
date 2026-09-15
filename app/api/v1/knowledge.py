"""
知识库管理 API（data_knowledge_chunks / data_excel_db_chunks）
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.document_processing import (
    DocumentProcessWorkerAdapter,
    SqlAlchemyDocumentRegistrationAdapter,
)
from app.adapters.knowledge.adapter import KnowledgePersistenceAdapter
from app.adapters.knowledge.metadata import VectorMetadataAdapter
from app.adapters.tasking import TaskManagerStateAdapter, ThreadPoolTaskExecutionAdapter
from app.adapters.web.endpoints.knowledge import (
    KnowledgeRecord,
    KnowledgeRecordDeleteResponse,
    KnowledgeRecordListResponse,
    KnowledgeUploadResponse,
)
from app.core.dependencies import get_async_db
from app.core.exceptions import APIException, NotFoundError
from app.core.logging import get_logger
from app.core.security import get_current_user, require_roles
from app.ports.contracts.identity import CurrentUserPort, ROLE_ADMIN, ROLE_SUPERUSER
from app.ports.dto.knowledge_upload import (
    ExcelDbUploadCommand,
    KnowledgeUploadCommand,
)
from app.usecases.document_processing.submit import SubmitDocumentProcessingUseCase
from app.usecases.knowledge.operations import (
    DeleteKnowledgeRecordUseCase,
    ListKnowledgeRecordsUseCase,
)
from app.usecases.knowledge.upload import (
    UploadExcelDbUseCase,
    UploadKnowledgeDocumentUseCase,
)

router = APIRouter()
logger = get_logger("knowledge")

try:
    _persistence = KnowledgePersistenceAdapter()
except Exception as e:
    logger.critical("KnowledgeAdapter 初始化失败: %s", e, exc_info=True)
    raise


def _submit_usecase(db: AsyncSession) -> SubmitDocumentProcessingUseCase:
    """组合根装配：文档处理异步流水线（与 document_processing 端点一致）。"""
    return SubmitDocumentProcessingUseCase(
        registration=SqlAlchemyDocumentRegistrationAdapter(db),
        task_state=TaskManagerStateAdapter(),
        task_execution=ThreadPoolTaskExecutionAdapter(),
        worker=DocumentProcessWorkerAdapter(),
    )


@router.get(
    "/records",
    response_model=KnowledgeRecordListResponse,
    summary="获取知识库记录列表（admin / superuser）",
)
async def list_knowledge_records(
    current_user: CurrentUserPort = Depends(require_roles(ROLE_ADMIN, ROLE_SUPERUSER)),
):
    _ = current_user
    try:
        result = await ListKnowledgeRecordsUseCase(_persistence).execute()
        return KnowledgeRecordListResponse(
            success=result.success,
            total=result.total,
            records=[KnowledgeRecord(**row) for row in result.records],
        )
    except Exception:
        logger.exception("查询知识库记录列表失败")
        raise HTTPException(status_code=500, detail="查询失败，请稍后重试")


@router.delete(
    "/records/{record_id}",
    response_model=KnowledgeRecordDeleteResponse,
    summary="删除知识库记录（admin / superuser）",
)
async def delete_knowledge_record(
    record_id: int,
    current_user: CurrentUserPort = Depends(require_roles(ROLE_ADMIN, ROLE_SUPERUSER)),
):
    try:
        result = await DeleteKnowledgeRecordUseCase(_persistence).execute(record_id, current_user)
        return KnowledgeRecordDeleteResponse(
            success=result.success, message=result.message, deleted_id=result.deleted_id
        )
    except (NotFoundError, APIException):
        raise
    except Exception:
        logger.exception("删除知识库记录失败: record_id=%s", record_id)
        raise HTTPException(status_code=500, detail="删除失败，请稍后重试")


@router.post(
    "/documents",
    response_model=KnowledgeUploadResponse,
    summary="上传文档知识（所有登录用户）",
)
async def upload_knowledge_documents(
    files: List[UploadFile] = File(..., description="文档文件（txt/md/pdf/doc/docx/ppt/pptx/html/json）"),
    on_conflict: Optional[str] = Query(
        None, pattern="^(replace|append)$",
        description="同名处理：replace=替换，append=追加；不传时同名返回 409",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        result = await UploadKnowledgeDocumentUseCase(
            _submit_usecase(db), VectorMetadataAdapter()
        ).execute(
            KnowledgeUploadCommand(
                files=files,
                on_conflict=on_conflict,
                current_user=current_user,
            )
        )
        return KnowledgeUploadResponse(**result.__dict__)
    except APIException:
        raise
    except Exception as e:
        logger.exception("上传文档知识失败: %s", e)
        await db.rollback()
        raise HTTPException(status_code=500, detail="上传失败，请稍后重试") from e


@router.post(
    "/excel-db",
    response_model=KnowledgeUploadResponse,
    summary="上传 Excel 类数据库文件（所有登录用户）",
)
async def upload_knowledge_excel_db(
    files: List[UploadFile] = File(..., description="Excel 文件（仅 xlsx / xls）"),
    on_conflict: Optional[str] = Query(
        None, pattern="^(replace|append)$",
        description="同名处理：replace=替换，append=追加；不传时同名返回 409",
    ),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        result = await UploadExcelDbUseCase(
            _submit_usecase(db), VectorMetadataAdapter()
        ).execute(
            ExcelDbUploadCommand(
                files=files,
                on_conflict=on_conflict,
                current_user=current_user,
            )
        )
        return KnowledgeUploadResponse(**result.__dict__)
    except APIException:
        raise
    except Exception as e:
        logger.exception("上传 Excel 数据库文件失败: %s", e)
        await db.rollback()
        raise HTTPException(status_code=500, detail="上传失败，请稍后重试") from e
