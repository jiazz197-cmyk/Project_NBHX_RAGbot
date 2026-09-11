from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app.adapters.ocr_executor_jobs import (
    ExecutorManagerAsyncTaskAdapter,
    PdfConvertJobAdapter,
    PdfPageCountAdapter,
)
from app.core.exceptions import APIException
from app.core.logging import get_logger
from app.core.security import get_current_user
from app.ports.contracts.identity import CurrentUserPort
from app.usecases.async_executor.executor_task_query import (
    CancelExecutorTaskCommand,
    CancelExecutorTaskUseCase,
    GetExecutorTaskResultQuery,
    GetExecutorTaskResultUseCase,
    GetExecutorTaskStatusQuery,
    GetExecutorTaskStatusUseCase,
)
from app.usecases.async_executor.pdf_convert import (
    PdfPageCountCommand,
    PdfPageCountUseCase,
    SubmitPdfConvertCommand,
    SubmitPdfConvertUseCase,
)

router = APIRouter()

logger = get_logger("image2url.pdf2image")


class PdfConvertRequest(BaseModel):
    dpi: Optional[int] = Field(default=200, ge=72, le=600, description="DPI resolution (72-600)")
    quality: Optional[int] = Field(default=85, ge=1, le=100, description="JPEG quality (1-100)")
    first_page: Optional[int] = Field(default=None, ge=1, description="First page to convert (1-indexed)")
    last_page: Optional[int] = Field(default=None, ge=1, description="Last page to convert (1-indexed)")
    upload_to_minio: Optional[bool] = Field(default=True, description="Upload images to MinIO")
    file_name_prefix: Optional[str] = Field(default=None, description="Filename prefix for MinIO")


class PdfConvertResponse(BaseModel):
    task_id: str
    status: str
    message: str


def _executor_adapters():
    ex = ExecutorManagerAsyncTaskAdapter()
    return ex, PdfConvertJobAdapter(), PdfPageCountAdapter()


@router.post("/pdf/convert", response_model=PdfConvertResponse)
async def convert_pdf_to_images(
    file: UploadFile = File(...),
    request: PdfConvertRequest = PdfConvertRequest(),
    uploader: str = Query(default="anonymous", description="上传者标识（仅允许传本人信息）"),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> PdfConvertResponse:
    _, jobs, _ = _executor_adapters()
    file_data = await file.read()
    try:
        result = SubmitPdfConvertUseCase(jobs).execute(
            SubmitPdfConvertCommand(
                current_user=current_user,
                file_data=file_data,
                content_type=file.content_type,
                original_filename=file.filename,
                dpi=request.dpi or 200,
                quality=request.quality or 85,
                first_page=request.first_page,
                last_page=request.last_page,
                upload_to_minio=request.upload_to_minio if request.upload_to_minio is not None else True,
                file_name_prefix=request.file_name_prefix,
                uploader_query=uploader,
            )
        )
        logger.info("启动 PDF 转图片任务: %s", result.task_id)
        return PdfConvertResponse(**result.__dict__)
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("启动 PDF 转图片任务失败: %s", e)
        raise HTTPException(status_code=500, detail="启动转换任务失败") from e


@router.get("/pdf/task/{task_id}")
async def get_pdf_convert_task_status(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _, _ = _executor_adapters()
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
        logger.error("获取 PDF 转图片任务状态失败: %s", e)
        raise HTTPException(status_code=500, detail="获取任务状态失败") from e


@router.get("/pdf/task/{task_id}/result")
async def get_pdf_convert_task_result(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _, _ = _executor_adapters()
    try:
        return GetExecutorTaskResultUseCase(ex).execute(
            GetExecutorTaskResultQuery(
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
        logger.error("获取 PDF 转图片任务结果失败: %s", e)
        raise HTTPException(status_code=500, detail="获取任务结果失败") from e


@router.delete("/pdf/task/{task_id}")
async def cancel_pdf_convert_task(
    task_id: str,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    ex, _, _ = _executor_adapters()
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
        logger.error("取消 PDF 转图片任务失败: %s", e)
        raise HTTPException(status_code=500, detail="取消任务失败") from e


@router.post("/pdf/page-count")
async def get_pdf_pages(
    file: UploadFile = File(...),
    _: CurrentUserPort = Depends(get_current_user),
) -> Dict[str, Any]:
    _, _, pages = _executor_adapters()
    file_data = await file.read()
    try:
        logger.info("获取 PDF 页数: %s", file.filename)
        result = PdfPageCountUseCase(pages).execute(
            PdfPageCountCommand(
                file_data=file_data,
                content_type=file.content_type,
                original_filename=file.filename,
            )
        )
        return {
            "filename": result.filename,
            "total_pages": result.total_pages,
            "message": result.message,
        }
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取 PDF 页数失败: %s", e)
        raise HTTPException(status_code=500, detail="获取页数失败") from e
