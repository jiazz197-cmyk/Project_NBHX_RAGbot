"""Upload bytes or streams to MinIO and return a presigned (or opt-in public) object URL."""

import io
from datetime import timedelta

from app.core.async_storage import (
    S3Error,
    async_get_minio_client,
    async_presigned_get_object,
)
from app.core.config import settings
from app.core.logging import get_logger

_logger = get_logger("image2url")

MINIO_PUBLIC_ENDPOINT = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
MINIO_SECURE = settings.MINIO_SECURE

_DEFAULT_BUCKET = settings.MINIO_BUCKET_NAME


def _target_bucket() -> str:
    return (settings.MINIO_OCR_TEMP_BUCKET or _DEFAULT_BUCKET).strip() or _DEFAULT_BUCKET


def _public_http_url(bucket: str, file_name: str) -> str:
    protocol = "https" if MINIO_SECURE else "http"
    return f"{protocol}://{MINIO_PUBLIC_ENDPOINT}/{bucket}/{file_name}"


def _is_anonymous_read(bucket: str) -> bool:
    return bool(
        settings.MINIO_OCR_ENABLE_ANONYMOUS_BUCKET
        and settings.MINIO_OCR_ANONYMOUS_BUCKET
        and bucket == settings.MINIO_OCR_ANONYMOUS_BUCKET.strip()
    )


async def async_upload_file_to_minio(file_data, file_name: str) -> str:
    """put_object, then return presigned GetObject URL (default) or opt-in public URL."""
    client = await async_get_minio_client()
    bucket = _target_bucket()

    anonymous_read = _is_anonymous_read(bucket)

    if not await client.bucket_exists(bucket):
        await client.make_bucket(bucket)
        if anonymous_read:
            _logger.warning(
                "Applying anonymous s3:GetObject on bucket %s — ensure this is intentional",
                bucket,
            )
            await client.set_bucket_policy(
                bucket,
                '{"Version":"2012-10-17","Statement":[{'
                '"Effect":"Allow","Principal":"*","Action":"s3:GetObject",'
                '"Resource":"arn:aws:s3:::%s/*"}]}' % bucket,
            )
    elif anonymous_read and settings.MINIO_OCR_ENABLE_ANONYMOUS_BUCKET:
        try:
            _logger.warning(
                "Refreshing anonymous GetObject policy on bucket %s",
                bucket,
            )
            await client.set_bucket_policy(
                bucket,
                '{"Version":"2012-10-17","Statement":[{'
                '"Effect":"Allow","Principal":"*","Action":"s3:GetObject",'
                '"Resource":"arn:aws:s3:::%s/*"}]}' % bucket,
            )
        except S3Error:
            pass

    file_stream = None
    try:
        if isinstance(file_data, bytes):
            file_stream = io.BytesIO(file_data)
            file_size = len(file_data)
        else:
            file_stream = file_data
            file_stream.seek(0, 2)
            file_size = file_stream.tell()
            file_stream.seek(0)

        await client.put_object(
            bucket_name=bucket,
            object_name=file_name,
            data=file_stream,
            length=file_size,
            content_type="application/octet-stream",
        )

        if anonymous_read:
            return _public_http_url(bucket, file_name)

        expires = timedelta(hours=settings.MINIO_PRESIGN_EXPIRES_HOURS)
        return await async_presigned_get_object(bucket, file_name, expires=expires)

    except S3Error as e:
        raise Exception(f"MinIO upload failed: {e}") from e
    except Exception as e:
        raise Exception(f"Upload exception: {str(e)}") from e
    finally:
        if file_stream is not None and hasattr(file_stream, "close"):
            file_stream.close()


def upload_file_to_minio(file_data, file_name: str) -> str:
    """Sync upload for worker threads — uses thread-local MinIO client."""
    from app.core.storage import (
        MinioClientPool,
        get_minio_client_for_presign,
        upload_buffer_to_minio,
    )

    bucket = _target_bucket()
    file_stream = io.BytesIO(file_data) if isinstance(file_data, bytes) else file_data

    pool = MinioClientPool()
    pool.ensure_bucket(bucket)

    upload_buffer_to_minio(file_stream, file_name, bucket=bucket)

    if _is_anonymous_read(bucket):
        return _public_http_url(bucket, file_name)

    presign_client = get_minio_client_for_presign()
    expires = timedelta(hours=settings.MINIO_PRESIGN_EXPIRES_HOURS)
    return presign_client.presigned_get_object(bucket, file_name, expires=expires)
