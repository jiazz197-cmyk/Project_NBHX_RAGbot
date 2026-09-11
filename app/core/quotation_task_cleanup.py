"""Quotation task file cleanup (MinIO objects + file_resource rows)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.async_storage import async_delete_from_minio
from app.core.logging import get_logger
from app.core.storage import delete_from_minio
from app.models.orm.file_resource import FileResource
from app.models.orm.quotation_task import QuotationTask

logger = get_logger("quotation_generation")


def _cleanup_task_files(
    db: Session,
    task: QuotationTask,
    extra_minio_paths: Optional[list] = None,
) -> Dict[str, Any]:
    cleanup_result = {
        "uploaded_file_deleted": False,
        "temp_image_deleted": False,
        "file_record_deleted": False,
        "xlsx_deleted": False,
    }

    if task.uploaded_file_minio_path:
        cleanup_result["uploaded_file_deleted"] = delete_from_minio(task.uploaded_file_minio_path)

    if task.temp_image_minio_path:
        cleanup_result["temp_image_deleted"] = delete_from_minio(task.temp_image_minio_path)

    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    xlsx_path = payload.get("u8_result_by_type_xlsx_minio_path")
    if isinstance(xlsx_path, str) and xlsx_path.strip():
        cleanup_result["xlsx_deleted"] = delete_from_minio(xlsx_path.strip())

    # Extra paths not yet persisted to DB (e.g. temp image uploaded right before a
    # crash). Dedup against the DB-sourced paths already attempted so we don't
    # double-delete. delete_from_minio infers the bucket from the path prefix.
    attempted: set = {
        task.uploaded_file_minio_path,
        task.temp_image_minio_path,
        xlsx_path.strip() if isinstance(xlsx_path, str) else None,
    }
    extra_deleted: list = []
    for path in extra_minio_paths or []:
        if isinstance(path, str) and path.strip() and path not in attempted:
            attempted.add(path)
            if delete_from_minio(path):
                extra_deleted.append(path)
    if extra_deleted:
        cleanup_result["extra_deleted"] = extra_deleted

    if task.uploaded_file_id:
        file_record = db.query(FileResource).filter(FileResource.id == task.uploaded_file_id).first()
        if file_record:
            # Break FK reference first, otherwise deleting file_resource can rollback the whole tx.
            task.uploaded_file_id = None
            db.flush()
            db.delete(file_record)
            cleanup_result["file_record_deleted"] = True

    return cleanup_result


def safe_cleanup_quotation_task_files(
    db: Session, task: QuotationTask, task_id: str, extra_minio_paths: Optional[list] = None
) -> Dict[str, Any]:
    try:
        return _cleanup_task_files(db, task, extra_minio_paths=extra_minio_paths)
    except Exception as exc:
        logger.error(f"清理报价任务文件失败 {task_id}: {exc}", exc_info=True)
        try:
            db.rollback()
        except Exception as e:
            logger.debug(f"cleanup rollback 失败 {task_id}: {e}")
        return {
            "uploaded_file_deleted": False,
            "temp_image_deleted": False,
            "file_record_deleted": False,
            "cleanup_error": str(exc),
        }


async def _cleanup_task_files_async(
    db: AsyncSession, task: QuotationTask, extra_minio_paths: Optional[list] = None
) -> Dict[str, Any]:
    cleanup_result = {
        "uploaded_file_deleted": False,
        "temp_image_deleted": False,
        "file_record_deleted": False,
        "xlsx_deleted": False,
    }

    if task.uploaded_file_minio_path:
        cleanup_result["uploaded_file_deleted"] = await async_delete_from_minio(task.uploaded_file_minio_path)

    if task.temp_image_minio_path:
        cleanup_result["temp_image_deleted"] = await async_delete_from_minio(task.temp_image_minio_path)

    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    xlsx_path = payload.get("u8_result_by_type_xlsx_minio_path")
    if isinstance(xlsx_path, str) and xlsx_path.strip():
        cleanup_result["xlsx_deleted"] = await async_delete_from_minio(xlsx_path.strip())

    attempted: set = {
        task.uploaded_file_minio_path,
        task.temp_image_minio_path,
        xlsx_path.strip() if isinstance(xlsx_path, str) else None,
    }
    extra_deleted: list = []
    for path in extra_minio_paths or []:
        if isinstance(path, str) and path.strip() and path not in attempted:
            attempted.add(path)
            if await async_delete_from_minio(path):
                extra_deleted.append(path)
    if extra_deleted:
        cleanup_result["extra_deleted"] = extra_deleted

    if task.uploaded_file_id:
        result = await db.execute(
            select(FileResource).where(FileResource.id == task.uploaded_file_id)
        )
        file_record = result.scalars().first()
        if file_record:
            task.uploaded_file_id = None
            await db.flush()
            await db.delete(file_record)
            cleanup_result["file_record_deleted"] = True

    return cleanup_result


async def safe_cleanup_quotation_task_files_async(
    db: AsyncSession, task: QuotationTask, task_id: str, extra_minio_paths: Optional[list] = None
) -> Dict[str, Any]:
    try:
        return await _cleanup_task_files_async(db, task, extra_minio_paths=extra_minio_paths)
    except Exception as exc:
        logger.error(f"清理报价任务文件失败 {task_id}: {exc}", exc_info=True)
        try:
            await db.rollback()
        except Exception as e:
            logger.debug(f"cleanup rollback 失败 {task_id}: {e}")
        return {
            "uploaded_file_deleted": False,
            "temp_image_deleted": False,
            "file_record_deleted": False,
            "cleanup_error": str(exc),
        }


async def cleanup_task_files_by_id(
    task_id: str, extra_minio_paths: Optional[list] = None
) -> Dict[str, Any]:
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(QuotationTask).where(QuotationTask.task_id == task_id)
            )
            task = result.scalars().first()
            if not task:
                return {}
            cleanup_result = await safe_cleanup_quotation_task_files_async(
                db, task, task_id, extra_minio_paths=extra_minio_paths
            )
            await db.commit()
            return cleanup_result
        except Exception:
            await db.rollback()
            raise


def cleanup_task_files_by_id_sync(
    task_id: str, extra_minio_paths: Optional[list] = None
) -> Dict[str, Any]:
    """Sync wrapper for worker threads — uses thread-local sync session."""
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        try:
            result = db.execute(
                select(QuotationTask).where(QuotationTask.task_id == task_id)
            )
            task = result.scalars().first()
            if not task:
                return {}
            cleanup_result = safe_cleanup_quotation_task_files(
                db, task, task_id, extra_minio_paths=extra_minio_paths
            )
            db.commit()
            return cleanup_result
        except Exception:
            db.rollback()
            raise
