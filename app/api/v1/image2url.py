from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.adapters.ocr_executor_jobs import ExecutorManagerAsyncTaskAdapter, ImageUploadJobAdapter
from app.core.exceptions import APIException
from app.core.logging import get_logger
from app.core.security import get_current_user
from app.ports.contracts.identity import CurrentUserPort
from app.usecases.async_executor.executor_task_query import (
    CancelExecutorTaskCommand,
    CancelExecutorTaskUseCase,
    ExecutorTaskStatsQuery,
    GetExecutorTaskResultQuery,
    GetExecutorTaskResultUseCase,
    GetExecutorTaskStatusQuery,
    GetExecutorTaskStatusUseCase,
    GetExecutorTaskStatsUseCase,
)
from app.usecases.async_executor.image_upload import SubmitImageUploadCommand, SubmitImageUploadUseCase

router = APIRouter()

logger = get_logger("image2url.api")


class ImageUploadRequest(BaseModel):
    file_name_prefix: Optional[str] = None
    overwrite: Optional[bool] = False


class ImageUploadResponse(BaseModel):
    task_id: str
    status: str
    message: str


def _adapters():
    return ExecutorManagerAsyncTaskAdapter(), ImageUploadJobAdapter()


@router.post("/image/upload", response_model=ImageUploadResponse)
async def upload_image(
    file: UploadFile = File(...),
    request: ImageUploadRequest = ImageUploadRequest(),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> ImageUploadResponse:
    _, jobs = _adapters()
    file_data = await file.read()
    try:
        logger.info("收到图片上传请求: %s", file.filename)
        result = SubmitImageUploadUseCase(jobs).execute(
            SubmitImageUploadCommand(
                current_user=current_user,
                file_data=file_data,
                content_type=file.content_type,
                original_filename=file.filename,
                file_name_prefix=request.file_name_prefix,
            )
        )
        logger.info("启动图片上传任务: %s", result.task_id)
        return ImageUploadResponse(**result.__dict__)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("启动图片上传任务失败: %s", e)
        raise HTTPException(status_code=500, detail="启动上传任务失败") from e


@router.get("/image/task/{task_id}")
async def get_image_upload_task_status(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _ = _adapters()
    try:
        return GetExecutorTaskStatusUseCase(ex).execute(
            GetExecutorTaskStatusQuery(
                task_id=task_id,
                current_user=current_user,
                forbidden_detail="无权查看该任务",
            )
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取图片上传任务状态失败: %s", e)
        raise HTTPException(status_code=500, detail="获取任务状态失败") from e


@router.get("/image/task/{task_id}/result")
async def get_image_upload_task_result(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _ = _adapters()
    try:
        return GetExecutorTaskResultUseCase(ex).execute(
            GetExecutorTaskResultQuery(
                task_id=task_id,
                current_user=current_user,
                forbidden_detail="无权查看该任务结果",
            )
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取图片上传任务结果失败: %s", e)
        raise HTTPException(status_code=500, detail="获取任务结果失败") from e


@router.delete("/image/task/{task_id}")
async def cancel_image_upload_task(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _ = _adapters()
    try:
        r = CancelExecutorTaskUseCase(ex).execute(
            CancelExecutorTaskCommand(
                task_id=task_id,
                current_user=current_user,
                forbidden_detail="无权取消该任务",
                done_conflict_detail="任务已完成，无法取消",
            )
        )
        return {"task_id": r.task_id, "message": r.message, "cancelled": r.cancelled}
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("取消图片上传任务失败: %s", e)
        raise HTTPException(status_code=500, detail="取消任务失败") from e


@router.get("/image/tasks/stats")
async def get_image_upload_tasks_stats(
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _ = _adapters()
    try:
        stats = GetExecutorTaskStatsUseCase(ex).execute(ExecutorTaskStatsQuery(current_user=current_user))
        return {
            "total_tasks": stats.total_tasks,
            "running_tasks": stats.running_tasks,
            "completed_tasks": stats.completed_tasks,
            "message": stats.message,
        }
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取任务统计信息失败: %s", e)
        raise HTTPException(status_code=500, detail="获取统计信息失败") from e
