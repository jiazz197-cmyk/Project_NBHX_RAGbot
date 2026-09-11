"""Document async processing API."""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.document_processing import DocumentProcessWorkerAdapter, SqlAlchemyDocumentRegistrationAdapter
from app.adapters.ocr_executor_jobs import ExecutorManagerAsyncTaskAdapter
from app.adapters.tasking import TaskManagerStateAdapter, ThreadPoolTaskExecutionAdapter
from app.core.dependencies import get_async_db
from app.core.exceptions import APIException, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import get_current_user, normalize_self_uploader
from app.ports.contracts.identity import CurrentUserPort
from app.usecases.document_processing.lifecycle import (
    CancelDocumentTaskCommand,
    CancelDocumentTaskUseCase,
    DeleteDocumentTaskCommand,
    DeleteDocumentTaskUseCase,
)
from app.usecases.document_processing.queries import (
    GetDocumentTaskStatusQuery,
    GetDocumentTaskStatusUseCase,
    ListDocumentTasksQuery,
    ListDocumentTasksUseCase,
)
from app.usecases.document_processing.submit import (
    SubmitDocumentProcessingCommand,
    SubmitDocumentProcessingUseCase,
)

router = APIRouter()
logger = get_logger("document_processing")


class DocumentProcessRequest(BaseModel):
    """（表单/文档用）处理参数模型。"""
    instance_id: int = Field(..., description="知识库实例ID")
    chunk_size: int = Field(500, ge=100, le=2000, description="文本块大小")
    chunk_overlap: int = Field(50, ge=0, le=500, description="文本块重叠大小")
    uploader: str = Field("anonymous", description="上传者标识")


class TaskSubmitResponse(BaseModel):
    """提交任务后的即时响应。"""
    task_id: str
    status: str = "pending"
    message: str = "任务已创建，开始处理"
    files_count: int


class TaskStatusResponse(BaseModel):
    """单任务状态。"""
    task_id: str
    status: str
    progress: int
    message: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None


class TaskListResponse(BaseModel):
    """任务列表。"""
    tasks: List[TaskStatusResponse]
    total: int


def _submit_usecase(db: AsyncSession):
    return SubmitDocumentProcessingUseCase(
        registration=SqlAlchemyDocumentRegistrationAdapter(db),
        task_state=TaskManagerStateAdapter(),
        task_execution=ThreadPoolTaskExecutionAdapter(),
        worker=DocumentProcessWorkerAdapter(),
    )


@router.post("/process", response_model=TaskSubmitResponse, summary="提交文档处理任务")
async def submit_document_processing(
    files: List[UploadFile] = File(..., description="要处理的文档文件"),
    instance_id: int = Query(..., description="知识库实例ID"),
    chunk_size: int = Query(500, ge=100, le=2000, description="文本块大小"),
    chunk_overlap: int = Query(50, ge=0, le=500, description="文本块重叠大小"),
    uploader: str = Query("anonymous", description="上传者标识（仅允许传本人信息）"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        normalized_uploader = normalize_self_uploader(uploader, current_user)
        result = await _submit_usecase(db).execute(
            SubmitDocumentProcessingCommand(
                files=files,
                instance_id=instance_id,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                normalized_uploader=normalized_uploader,
                current_user=current_user,
            )
        )
        return TaskSubmitResponse(**result.__dict__)
    except ValidationError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("提交文档处理任务失败: %s", e, exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail="提交任务失败") from e


@router.get("/status/{task_id}", response_model=TaskStatusResponse, summary="查询任务状态")
async def get_task_status(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        dto = await GetDocumentTaskStatusUseCase(TaskManagerStateAdapter()).execute(
            GetDocumentTaskStatusQuery(task_id=task_id, current_user=current_user)
        )
        return TaskStatusResponse(**dto.__dict__)
    except NotFoundError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("查询任务状态失败 %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="查询任务状态失败") from e


@router.get("/tasks", response_model=TaskListResponse, summary="获取任务列表")
async def list_tasks(
    status: Optional[str] = Query(
        None, description="按状态筛选 (pending/running/completed/failed)"
    ),
    limit: int = Query(10, ge=1, le=100, description="返回数量限制"),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        out = await ListDocumentTasksUseCase(TaskManagerStateAdapter()).execute(
            ListDocumentTasksQuery(current_user=current_user, status_filter=status, limit=limit)
        )
        return TaskListResponse(
            tasks=[TaskStatusResponse(**t.__dict__) for t in out.tasks],
            total=out.total,
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取任务列表失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="获取任务列表失败") from e


@router.post("/tasks/{task_id}/cancel", summary="取消任务")
async def cancel_task_endpoint(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        return await CancelDocumentTaskUseCase(
            TaskManagerStateAdapter(),
            ExecutorManagerAsyncTaskAdapter(),
        ).execute(CancelDocumentTaskCommand(task_id=task_id, current_user=current_user))
    except NotFoundError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("取消任务失败 %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="取消任务失败") from e


@router.delete("/tasks/{task_id}", summary="删除任务记录")
async def delete_task_endpoint(
    task_id: str,
    cancel_if_running: bool = Query(True, description="是否取消正在运行的任务"),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        return await DeleteDocumentTaskUseCase(
            TaskManagerStateAdapter(),
            ExecutorManagerAsyncTaskAdapter(),
        ).execute(
            DeleteDocumentTaskCommand(
                task_id=task_id,
                current_user=current_user,
                cancel_if_running=cancel_if_running,
            )
        )
    except NotFoundError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("删除任务失败 %s: %s", task_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="删除任务失败") from e
