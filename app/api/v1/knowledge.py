"""
知识库管理 API（data_knowledge_chunks）
"""
from fastapi import APIRouter, Depends, HTTPException

from app.adapters.knowledge.adapter import KnowledgePersistenceAdapter
from app.adapters.web.endpoints.knowledge import (
    KnowledgeRecord,
    KnowledgeRecordDeleteResponse,
    KnowledgeRecordListResponse,
)
from app.core.exceptions import APIException, NotFoundError
from app.core.logging import get_logger
from app.core.security import require_roles
from app.ports.contracts.identity import CurrentUserPort, ROLE_ADMIN, ROLE_SUPERUSER
from app.usecases.knowledge.operations import (
    DeleteKnowledgeRecordUseCase,
    ListKnowledgeRecordsUseCase,
)

router = APIRouter()
logger = get_logger("knowledge")

try:
    _persistence = KnowledgePersistenceAdapter()
except Exception as e:
    logger.critical("KnowledgeAdapter 初始化失败: %s", e, exc_info=True)
    raise


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
