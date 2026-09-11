"""File manager adapter: encapsulates ORM + MinIO behind FileManagerPort."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import normalize_self_uploader
from app.core.async_storage import (
    async_delete_from_minio,
    async_stat_object,
    async_upload_stream_to_minio,
)
from app.core.storage import MinioUploadError
from app.domain.file_manager.naming import generate_unique_filename
from app.models.orm.file_resource import FileResource
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.file_manager import FileManagerPort
from app.ports.dto.files import FileRecordDTO

logger = get_logger("file_manager")


def _to_dto(rec: FileResource) -> FileRecordDTO:
    return FileRecordDTO(
        id=rec.id,
        file_name=rec.file_name,
        unique_name=rec.unique_name,
        minio_object_path=rec.minio_object_path,
        content_type=rec.content_type,
        file_size=rec.file_size,
        uploader=rec.uploader,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


class SqlAlchemyFileManagerAdapter(FileManagerPort):
    """Encapsulates all ORM queries and MinIO operations.

    Each method manages its own session lifecycle internally.
    """

    async def upload_stream_persist(
        self,
        *,
        file_stream: Any,
        original_filename: str,
        file_size: int,
        content_type: str,
        uploader: str,
        current_user: CurrentUserPort,
    ) -> FileRecordDTO:
        if not original_filename:
            raise ValidationError("文件名不能为空")

        unique_name = generate_unique_filename(original_filename)
        minio_path = f"uploads/{unique_name}"
        try:
            await async_upload_stream_to_minio(file_stream, minio_path, file_size, content_type)
        except MinioUploadError as exc:
            raise RuntimeError(str(exc)) from exc

        if file_size == -1:
            try:
                stat = await async_stat_object(minio_path)
                file_size = stat.size
            except Exception as e:
                logger.warning("无法获取上传文件大小: %s", e)
                file_size = 0

        normalized_uploader = normalize_self_uploader(uploader, current_user)

        async with AsyncSessionLocal() as db:
            try:
                file_record = FileResource(
                    file_name=original_filename,
                    unique_name=unique_name,
                    minio_object_path=minio_path,
                    content_type=content_type,
                    file_size=file_size,
                    uploader=normalized_uploader,
                )
                db.add(file_record)
                await db.commit()
                await db.refresh(file_record)
                logger.info("文件上传成功: %s -> %s (ID: %s)", original_filename, minio_path, file_record.id)
                return _to_dto(file_record)
            except Exception:
                await db.rollback()
                # Compensating delete: the MinIO object was already uploaded but
                # the DB commit failed — reclaim it so it does not become an orphan.
                try:
                    await async_delete_from_minio(minio_path)
                    logger.warning("DB 落库失败后回删 MinIO 对象: %s", minio_path)
                except Exception as cleanup_err:
                    logger.error("回删 MinIO 对象失败 path=%s err=%s", minio_path, cleanup_err)
                raise

    async def get_file_or_not_found(self, file_id: int) -> FileRecordDTO:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(FileResource).filter(FileResource.id == file_id))
            file_record = result.scalars().first()
            if not file_record:
                raise NotFoundError(f"文件 ID {file_id} 不存在")
            return _to_dto(file_record)

    async def list_files_page(
        self,
        *,
        current_user: CurrentUserPort,
        page: int,
        page_size: int,
        uploader: Optional[str],
    ) -> Tuple[int, List[FileRecordDTO]]:
        async with AsyncSessionLocal() as db:
            count_stmt = select(func.count(FileResource.id))
            list_stmt = select(FileResource)
            if not current_user.is_superuser():
                count_stmt = count_stmt.filter(FileResource.uploader == current_user.username)
                list_stmt = list_stmt.filter(FileResource.uploader == current_user.username)
            elif uploader:
                count_stmt = count_stmt.filter(FileResource.uploader == uploader)
                list_stmt = list_stmt.filter(FileResource.uploader == uploader)

            total = (await db.execute(count_stmt)).scalar_one()
            offset = (page - 1) * page_size
            result = await db.execute(
                list_stmt.order_by(FileResource.created_at.desc()).offset(offset).limit(page_size)
            )
            items = result.scalars().all()
            return total, [_to_dto(i) for i in items]

    async def search_files_page(
        self,
        *,
        current_user: CurrentUserPort,
        keyword: str,
        page: int,
        page_size: int,
    ) -> Tuple[int, List[FileRecordDTO]]:
        async with AsyncSessionLocal() as db:
            count_stmt = select(func.count(FileResource.id)).filter(
                FileResource.file_name.ilike(f"%{keyword}%")
            )
            list_stmt = select(FileResource).filter(FileResource.file_name.ilike(f"%{keyword}%"))
            if not current_user.is_superuser():
                count_stmt = count_stmt.filter(FileResource.uploader == current_user.username)
                list_stmt = list_stmt.filter(FileResource.uploader == current_user.username)

            total = (await db.execute(count_stmt)).scalar_one()
            offset = (page - 1) * page_size
            result = await db.execute(
                list_stmt.order_by(FileResource.created_at.desc()).offset(offset).limit(page_size)
            )
            items = result.scalars().all()
            return total, [_to_dto(i) for i in items]

    async def delete_file_and_object(self, file_record: FileRecordDTO) -> None:
        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    select(FileResource).filter(FileResource.id == file_record.id)
                )
                rec = result.scalars().first()
                if not rec:
                    raise NotFoundError(f"文件 ID {file_record.id} 不存在")
                if not await async_delete_from_minio(rec.minio_object_path):
                    logger.warning("MinIO 删除失败，但继续删除数据库记录: %s", rec.minio_object_path)
                await db.delete(rec)
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def batch_delete_ids(self, file_ids: List[int]) -> Tuple[int, int, List[int]]:
        success_count = 0
        failed_count = 0
        failed_ids: List[int] = []
        async with AsyncSessionLocal() as db:
            for file_id in file_ids:
                try:
                    result = await db.execute(
                        select(FileResource).filter(FileResource.id == file_id)
                    )
                    file_record = result.scalars().first()
                    if not file_record:
                        failed_count += 1
                        failed_ids.append(file_id)
                        continue
                    await async_delete_from_minio(file_record.minio_object_path)
                    await db.delete(file_record)
                    await db.commit()
                    success_count += 1
                except Exception:
                    logger.exception("批量删除失败 (ID: %s)", file_id)
                    failed_count += 1
                    failed_ids.append(file_id)
                    await db.rollback()
        return success_count, failed_count, failed_ids
