"""Background image upload tasks for OCR pipeline (MinIO via integrations.ocr.image2url)."""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.core.executor import CancellationToken
from app.adapters.ocr.image2url import upload_file_to_minio

logger = logging.getLogger(__name__)


class ImageUploadResult(BaseModel):
    """Serialized upload result for executor JSON payloads."""

    url: str
    filename: str
    original_filename: str
    content_type: str
    file_size: int


def background_image_upload_task(
    token: CancellationToken,
    task_id: str,
    file_data: bytes,
    original_filename: str,
    content_type: str,
    file_name_prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run image upload in worker thread: MinIO (OCR bucket) + structured result dict.
    """
    try:
        logger.info("开始图片上传任务: %s", task_id)
        if token.is_cancelled():
            logger.warning("任务 %s 在开始前被取消", task_id)
            return {"status": "cancelled", "message": "任务已取消"}

        file_ext = os.path.splitext(original_filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_ext}"
        if file_name_prefix:
            unique_filename = f"{file_name_prefix}/{unique_filename}"
        unique_filename = f"images/{unique_filename}"

        if token.is_cancelled():
            logger.warning("任务 %s 在上传前被取消", task_id)
            return {"status": "cancelled", "message": "任务已取消"}

        logger.info("上传文件 %s -> %s", original_filename, unique_filename)
        image_url = upload_file_to_minio(file_data, unique_filename)

        result = {
            "status": "success",
            "message": "图片上传成功",
            "data": ImageUploadResult(
                url=image_url,
                filename=unique_filename,
                original_filename=original_filename,
                content_type=content_type,
                file_size=len(file_data),
            ).model_dump(),
        }
        logger.info("图片上传任务 %s 成功完成", task_id)
        return result
    except Exception as e:
        error_msg = f"图片上传失败: {e!s}"
        logger.error("任务 %s 失败: %s", task_id, error_msg, exc_info=True)
        return {
            "status": "error",
            "message": error_msg,
            "error": str(e),
        }
