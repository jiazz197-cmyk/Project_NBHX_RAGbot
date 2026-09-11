"""Background PDF-to-image tasks: conversion, OCR MinIO upload, FileResource rows."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.core.database import SessionLocal
from app.core.executor import CancellationToken
from app.adapters.ocr.image2url import upload_file_to_minio
from app.adapters.ocr.pdf2image import get_pdf_page_count, pdf_to_images
from app.models.orm.file_resource import FileResource

logger = logging.getLogger(__name__)


class ImageInfo(BaseModel):
    page_number: int
    filename: str
    url: Optional[str] = None
    file_size: int
    file_id: Optional[int] = None


class PdfConvertResult(BaseModel):
    total_pages: int
    converted_pages: int
    images: List[dict]
    original_filename: str


def background_pdf_convert_task(
    token: CancellationToken,
    task_id: str,
    file_data: bytes,
    original_filename: str,
    dpi: int = 200,
    quality: int = 85,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
    upload_to_minio: bool = True,
    file_name_prefix: Optional[str] = None,
    uploader: str = "anonymous",
) -> Dict[str, Any]:
    """Convert PDF pages to JPEG, optional OCR-bucket MinIO + DB file rows."""
    try:
        logger.info("开始 PDF 转图片任务: %s", task_id)
        if token.is_cancelled():
            return {"status": "cancelled", "message": "任务已取消"}

        total_pages = get_pdf_page_count(file_data)
        logger.info("PDF 文件 %s 共有 %s 页", original_filename, total_pages)

        if first_page and first_page > total_pages:
            return {
                "status": "error",
                "message": f"起始页码 {first_page} 超出范围（总页数：{total_pages}）",
            }

        if last_page and last_page > total_pages:
            last_page = total_pages
            logger.warning("结束页码超出范围，自动调整为 %s", last_page)

        if token.is_cancelled():
            return {"status": "cancelled", "message": "任务已取消"}

        image_results = pdf_to_images(
            file_data=file_data,
            dpi=dpi,
            quality=quality,
            first_page=first_page,
            last_page=last_page,
        )
        images_info = []
        base_filename = os.path.splitext(original_filename)[0]
        db_session = SessionLocal()
        try:
            for idx, (img_bytes, _suggested) in enumerate(image_results, start=1):
                if token.is_cancelled():
                    return {"status": "cancelled", "message": "任务已取消"}
                page_num = (first_page or 1) + idx - 1
                filename = f"{base_filename}_page_{page_num:03d}.jpg"
                url = None
                file_id = None
                if upload_to_minio:
                    unique_filename = f"{uuid.uuid4().hex}_{filename}"
                    if file_name_prefix:
                        unique_filename = f"{file_name_prefix}/{unique_filename}"
                    unique_filename = f"temp/{unique_filename}"
                    url = upload_file_to_minio(img_bytes, unique_filename)
                    try:
                        file_record = FileResource(
                            file_name=filename,
                            unique_name=unique_filename,
                            minio_object_path=unique_filename,
                            content_type="image/jpeg",
                            file_size=len(img_bytes),
                            uploader=uploader,
                        )
                        db_session.add(file_record)
                        db_session.commit()
                        db_session.refresh(file_record)
                        file_id = file_record.id
                    except Exception as e:
                        logger.error("保存文件记录到数据库失败: %s", e, exc_info=True)
                        db_session.rollback()
                        # Compensating delete: the image was already uploaded to
                        # MinIO but the DB record failed — reclaim it so it does
                        # not orphan. upload_file_to_minio wrote to the OCR temp
                        # bucket (or default); delete_from_minio infers the bucket
                        # from the temp/ prefix.
                        try:
                            from app.core.storage import delete_from_minio
                            delete_from_minio(unique_filename)
                            logger.warning("DB 落库失败后回删 MinIO 临时图: %s", unique_filename)
                        except Exception as cleanup_err:
                            logger.error("回删 MinIO 临时图失败 path=%s err=%s", unique_filename, cleanup_err)
                        # Nullify url/file_id so downstream consumers don't
                        # reference a now-deleted MinIO object.
                        url = None
                        file_id = None
                    filename = unique_filename
                images_info.append(
                    ImageInfo(
                        page_number=page_num,
                        filename=filename,
                        url=url,
                        file_size=len(img_bytes),
                        file_id=file_id,
                    ).model_dump()
                )
        finally:
            db_session.close()

        return {
            "status": "success",
            "message": "PDF 转图片成功",
            "data": PdfConvertResult(
                total_pages=total_pages,
                converted_pages=len(image_results),
                images=images_info,
                original_filename=original_filename,
            ).model_dump(),
        }
    except Exception as e:
        error_msg = f"PDF 转图片失败: {e!s}"
        logger.error("任务 %s 失败: %s", task_id, error_msg, exc_info=True)
        return {
            "status": "error",
            "message": error_msg,
            "error": str(e),
        }
