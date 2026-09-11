"""
File management API routes.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.adapters.file_manager import SqlAlchemyFileManagerAdapter
from app.core.exceptions import APIException, NotFoundError, ValidationError
from app.core.http_headers import build_content_disposition
from app.core.logging import get_logger
from app.core.security import get_current_user
from app.core.async_storage import STREAM_CHUNK_SIZE, async_download_object_stream
from app.ports.contracts.identity import CurrentUserPort
from app.usecases.file_manager.operations import (
    BatchDeleteFilesCommand,
    BatchDeleteFilesUseCase,
    DeleteFileCommand,
    DeleteFileUseCase,
    GetFileByIdQuery,
    GetFileForAccessUseCase,
    ListFilesQuery,
    ListFilesUseCase,
    SearchFilesQuery,
    SearchFilesUseCase,
    UploadFileCommand,
    UploadFileUseCase,
)

router = APIRouter()
logger = get_logger("file_manager")


class FileUploadResponse(BaseModel):
    """文件上传响应"""
    id: int
    file_name: str
    unique_name: str
    minio_path: str
    content_type: str
    file_size: int
    uploader: str
    created_at: datetime
    message: str = "文件上传成功"


class FileInfoResponse(BaseModel):
    """文件信息"""
    id: int
    file_name: str
    unique_name: str
    content_type: str
    file_size: int
    uploader: str
    created_at: datetime
    updated_at: datetime


class FileListResponse(BaseModel):
    """文件列表"""
    total: int
    page: int
    page_size: int
    items: List[FileInfoResponse]


class DeleteResponse(BaseModel):
    """删除响应"""
    success: bool
    message: str
    deleted_id: int


class BatchDeleteResponse(BaseModel):
    """批量删除响应"""
    success_count: int
    failed_count: int
    failed_ids: List[int] = []
    message: str


_fm_adapter = SqlAlchemyFileManagerAdapter()


def _fm_port() -> SqlAlchemyFileManagerAdapter:
    return _fm_adapter


@router.post("/upload", response_model=FileUploadResponse, summary="上传文件")
async def upload_file(
    file: UploadFile = File(..., description="要上传的文件"),
    uploader: str = Query(default="anonymous", description="上传者标识（仅允许传本人信息）"),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        file_size = getattr(file, "size", None) or -1
        content_type = file.content_type or "application/octet-stream"
        rec = await UploadFileUseCase(_fm_port()).execute(
            UploadFileCommand(
                file_stream=file.file,
                original_filename=file.filename,
                file_size=file_size,
                content_type=content_type,
                uploader=uploader,
                current_user=current_user,
            )
        )
        return FileUploadResponse(
            id=rec.id,
            file_name=rec.file_name,
            unique_name=rec.unique_name,
            minio_path=rec.minio_object_path,
            content_type=rec.content_type,
            file_size=rec.file_size,
            uploader=rec.uploader,
            created_at=rec.created_at,
        )
    except ValidationError:
        raise
    except NotFoundError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("文件上传失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="文件上传失败") from e


@router.get("/download/{file_id}", summary="下载文件")
async def download_file(
    file_id: int,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        file_record = await GetFileForAccessUseCase(_fm_port()).execute(
            GetFileByIdQuery(
                file_id=file_id,
                current_user=current_user,
                forbidden_detail="无权下载该文件",
            )
        )

        async def file_stream_generator():
            response = await async_download_object_stream(file_record.minio_object_path)
            try:
                async for chunk in response.content.iter_chunked(STREAM_CHUNK_SIZE):
                    yield chunk
            finally:
                try:
                    response.close()
                except Exception:
                    pass

        logger.info("文件下载: %s (ID: %s)", file_record.file_name, file_id)
        return StreamingResponse(
            file_stream_generator(),
            media_type=file_record.content_type,
            headers={"Content-Disposition": build_content_disposition(file_record.file_name)},
        )
    except NotFoundError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("文件下载失败 (ID: %s): %s", file_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="文件下载失败") from e


@router.get("/info/{file_id}", response_model=FileInfoResponse, summary="获取文件信息")
async def get_file_info(
    file_id: int,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    file_record = await GetFileForAccessUseCase(_fm_port()).execute(
        GetFileByIdQuery(
            file_id=file_id,
            current_user=current_user,
            forbidden_detail="无权查看该文件信息",
        )
    )
    return FileInfoResponse(
        id=file_record.id,
        file_name=file_record.file_name,
        unique_name=file_record.unique_name,
        content_type=file_record.content_type,
        file_size=file_record.file_size,
        uploader=file_record.uploader,
        created_at=file_record.created_at,
        updated_at=file_record.updated_at,
    )


@router.get("/list", response_model=FileListResponse, summary="获取文件列表")
async def list_files(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    uploader: Optional[str] = Query(None, description="按上传者筛选"),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        total, items = await ListFilesUseCase(_fm_port()).execute(
            ListFilesQuery(
                current_user=current_user,
                page=page,
                page_size=page_size,
                uploader=uploader,
            )
        )
        file_list = [
            FileInfoResponse(
                id=item.id,
                file_name=item.file_name,
                unique_name=item.unique_name,
                content_type=item.content_type,
                file_size=item.file_size,
                uploader=item.uploader,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in items
        ]
        return FileListResponse(
            total=total,
            page=page,
            page_size=page_size,
            items=file_list,
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("获取文件列表失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="获取文件列表失败") from e


@router.delete("/delete/{file_id}", response_model=DeleteResponse, summary="删除文件")
async def delete_file(
    file_id: int,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        await DeleteFileUseCase(_fm_port()).execute(
            DeleteFileCommand(file_id=file_id, current_user=current_user)
        )
        return DeleteResponse(success=True, message="文件删除成功", deleted_id=file_id)
    except NotFoundError:
        raise
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("文件删除失败 (ID: %s): %s", file_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="文件删除失败") from e


@router.post("/batch-delete", response_model=BatchDeleteResponse, summary="批量删除文件")
async def batch_delete_files(
    file_ids: List[int] = Query(..., description="要删除的文件 ID 列表"),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    result = await BatchDeleteFilesUseCase(_fm_port()).execute(
        BatchDeleteFilesCommand(file_ids=file_ids, current_user=current_user)
    )
    return BatchDeleteResponse(
        success_count=result.success_count,
        failed_count=result.failed_count,
        failed_ids=result.failed_ids,
        message=f"批量删除完成: 成功 {result.success_count} 个, 失败 {result.failed_count} 个",
    )


@router.get("/search", response_model=FileListResponse, summary="搜索文件")
async def search_files(
    keyword: str = Query(..., min_length=1, description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    try:
        total, items = await SearchFilesUseCase(_fm_port()).execute(
            SearchFilesQuery(
                current_user=current_user,
                keyword=keyword,
                page=page,
                page_size=page_size,
            )
        )
        file_list = [
            FileInfoResponse(
                id=item.id,
                file_name=item.file_name,
                unique_name=item.unique_name,
                content_type=item.content_type,
                file_size=item.file_size,
                uploader=item.uploader,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in items
        ]
        return FileListResponse(
            total=total, page=page, page_size=page_size, items=file_list
        )
    except APIException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error("搜索文件失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="搜索文件失败") from e
