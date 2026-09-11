"""Async MinIO layer based on miniopy-async (replaces sync minio-py on event-loop paths)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import IO, Optional

from miniopy_async import Minio
from miniopy_async.error import S3Error

from app.core.config import settings
from app.core.logging import get_logger
from app.core.storage import (
    MINIO_BUCKET_NAME,
    STREAM_CHUNK_SIZE,
    MinioUploadError,
    _minio_host_and_secure_for_app,
    _parse_s3_endpoint_for_presign,
    resolve_bucket_for_object,
)

logger = get_logger("storage.async")

MINIO_ACCESS_KEY = settings.MINIO_ACCESS_KEY
MINIO_SECRET_KEY = settings.MINIO_SECRET_KEY
MINIO_SECURE = settings.MINIO_SECURE


class AsyncMinioClientPool:
    """Global async Minio client singleton (aiohttp under the hood)."""

    _app_client: Minio | None = None
    _presign_client: Minio | None = None
    _bucket_checked: dict[str, bool] = {}

    @classmethod
    async def get_client(cls) -> Minio:
        if cls._app_client is None:
            host, secure = _minio_host_and_secure_for_app()
            cls._app_client = Minio(
                host,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=secure,
                region=settings.MINIO_REGION,
            )
            logger.debug("Created async MinIO client: app endpoint %s", host)
        return cls._app_client

    @classmethod
    async def get_presign_client(cls) -> Minio:
        if cls._presign_client is None:
            source = (settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT or "127.0.0.1:9000").strip()
            host_port, scheme_https = _parse_s3_endpoint_for_presign(source)
            if settings.MINIO_PRESIGN_SECURE is not None:
                secure = bool(settings.MINIO_PRESIGN_SECURE)
            elif scheme_https is not None:
                secure = scheme_https
            else:
                secure = bool(MINIO_SECURE)
            cls._presign_client = Minio(
                host_port,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=secure,
                region=settings.MINIO_REGION,
            )
            logger.debug("Created async MinIO presign client: %s", host_port)
        return cls._presign_client

    @classmethod
    async def ensure_bucket(cls, bucket_name: str = MINIO_BUCKET_NAME) -> bool:
        if cls._bucket_checked.get(bucket_name, False):
            return True
        try:
            client = await cls.get_client()
            if not await client.bucket_exists(bucket_name):
                await client.make_bucket(bucket_name)
                logger.info("Created MinIO bucket: %s", bucket_name)
            cls._bucket_checked[bucket_name] = True
            return True
        except S3Error as e:
            logger.error("MinIO bucket check failed: %s", e)
            return False
        except Exception as e:
            logger.error("MinIO connection failed: %s", e)
            return False

    @classmethod
    async def close(cls) -> None:
        if cls._app_client:
            await cls._app_client.close_session()
            cls._app_client = None
        if cls._presign_client:
            await cls._presign_client.close_session()
            cls._presign_client = None
        cls._bucket_checked.clear()


async def async_get_minio_client() -> Minio:
    return await AsyncMinioClientPool.get_client()


async def async_stat_object(object_name: str, bucket: str = MINIO_BUCKET_NAME):
    client = await AsyncMinioClientPool.get_client()
    return await client.stat_object(bucket, object_name)


async def async_upload_stream_to_minio(
    file_stream: IO[bytes],
    file_name: str,
    file_size: int = -1,
    content_type: str = "application/octet-stream",
) -> str:
    try:
        if not await AsyncMinioClientPool.ensure_bucket():
            raise MinioUploadError("MinIO service unavailable")
        client = await AsyncMinioClientPool.get_client()
        if file_size > 0:
            await client.put_object(
                bucket_name=MINIO_BUCKET_NAME,
                object_name=file_name,
                data=file_stream,
                length=file_size,
                content_type=content_type,
            )
        else:
            await client.put_object(
                bucket_name=MINIO_BUCKET_NAME,
                object_name=file_name,
                data=file_stream,
                length=-1,
                content_type=content_type,
                part_size=5 * 1024 * 1024,
            )
        logger.debug("Stream upload ok: %s (size=%s)", file_name, file_size)
        return file_name
    except MinioUploadError:
        raise
    except S3Error as err:
        logger.error("Stream upload to MinIO failed: %s", err)
        raise MinioUploadError(f"流式上传到 MinIO 失败: {err}") from err
    except Exception as e:
        logger.error("Stream upload exception: %s", e)
        raise MinioUploadError(f"流式上传异常: {e}") from e


async def async_delete_from_minio(file_name: str, bucket: Optional[str] = None) -> bool:
    """remove_object; returns False on error. bucket=None infers from object_name prefix."""
    target_bucket = bucket or resolve_bucket_for_object(file_name)
    try:
        client = await AsyncMinioClientPool.get_client()
        await client.remove_object(bucket_name=target_bucket, object_name=file_name)
        logger.debug("Deleted object: %s (bucket=%s)", file_name, target_bucket)
        return True
    except S3Error as err:
        logger.error("Delete MinIO object failed: %s (bucket=%s, object=%s)", err, target_bucket, file_name)
        return False
    except Exception as e:
        logger.error("Delete object exception: %s (bucket=%s, object=%s)", e, target_bucket, file_name)
        return False


async def async_list_objects(prefix: str, bucket: Optional[str] = None) -> list[tuple[str, object]]:
    """List objects under prefix. Returns list of (object_name, last_modified).

    Used by the orphan reconciliation sweep (minio_reconcile). bucket defaults to
    the inference resolver so callers can pass a bare prefix.
    """
    target_bucket = bucket or resolve_bucket_for_object(prefix)
    client = await AsyncMinioClientPool.get_client()
    result: list[tuple[str, object]] = []
    async for obj in client.list_objects(target_bucket, prefix=prefix, recursive=True):
        result.append((obj.object_name, obj.last_modified))
    return result


async def async_presigned_get_object(
    bucket_name: str,
    object_name: str,
    expires: timedelta | int = timedelta(hours=1),
) -> str:
    client = await AsyncMinioClientPool.get_presign_client()
    return await client.presigned_get_object(bucket_name, object_name, expires=expires)


async def async_download_object_stream(object_name: str):
    """Return aiohttp response from get_object (caller must close)."""
    client = await AsyncMinioClientPool.get_client()
    return await client.get_object(MINIO_BUCKET_NAME, object_name)
