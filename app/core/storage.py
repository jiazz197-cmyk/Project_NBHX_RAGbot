"""MinIO：按线程 TLS 建 client，bucket 懒检查；分块阈值见 DEFAULT_CHUNK_SIZE。"""
import tempfile
import threading
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Optional, Generator, Dict, Tuple

import urllib3
from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("storage")

MINIO_ENDPOINT = settings.MINIO_ENDPOINT
MINIO_ACCESS_KEY = settings.MINIO_ACCESS_KEY
MINIO_SECRET_KEY = settings.MINIO_SECRET_KEY
MINIO_BUCKET_NAME = settings.MINIO_BUCKET_NAME
MINIO_SECURE = settings.MINIO_SECURE

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024
STREAM_CHUNK_SIZE = 1 * 1024 * 1024


class MinioUploadError(Exception):
    """Raised when an upload to MinIO fails (service unavailable or S3/IO error)."""


def _build_http_client(connect_sec: float = 10.0, read_sec: Optional[float] = None) -> urllib3.PoolManager:
    """urllib3 PoolManager with explicit connect/read timeouts.

    Injected into Minio(http_client=...) so that stalled get_object / put_object
    reads do not block worker threads indefinitely. Without this, minio-py uses
    urllib3 defaults (no read timeout) and a hung MinIO can hang a worker forever.
    """
    read = read_sec if read_sec is not None else float(settings.MINIO_DOWNLOAD_TIMEOUT_SEC)
    return urllib3.PoolManager(
        timeout=urllib3.Timeout(connect=float(connect_sec), read=read),
        cert_reqs="CERT_REQUIRED",
        maxsize=10,
    )


class MinioClientPool:
    """单例：每线程一个 Minio；bucket 存在性按名缓存。"""
    _instance = None
    _initialized = False
    
    def __new__(cls):
        """单例。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """仅首次初始化。"""
        if self._initialized:
            return
        
        self._local = threading.local()
        self._bucket_checked: Dict[str, bool] = {}
        self._bucket_lock = threading.Lock()
        
        self.__class__._initialized = True
    
    def get_client(self) -> Minio:
        """当前线程懒创建 Minio。仅使用 MINIO_APP_ENDPOINT（默认 127.0.0.1:9000），与预签名里的 MINIO_PUBLIC_ENDPOINT 分离。"""
        if not hasattr(self._local, 'client'):
            host, secure = _minio_host_and_secure_for_app()
            self._local.client = Minio(
                host,
                access_key=MINIO_ACCESS_KEY,
                secret_key=MINIO_SECRET_KEY,
                secure=secure,
                region=settings.MINIO_REGION,
                http_client=_build_http_client(),
            )
            logger.debug(f"创建 MinIO 客户端: 线程 {threading.current_thread().name}")

        return self._local.client
    
    def ensure_bucket(self, bucket_name: str = MINIO_BUCKET_NAME) -> bool:
        """无则创建；异常只打日志并返回 False。同一 bucket 只检查一次。"""
        if self._bucket_checked.get(bucket_name, False):
            return True
        
        with self._bucket_lock:
            if self._bucket_checked.get(bucket_name, False):
                return True
            
            try:
                client = self.get_client()
                if not client.bucket_exists(bucket_name):
                    client.make_bucket(bucket_name)
                    logger.info(f"创建 MinIO bucket: {bucket_name}")
                else:
                    logger.debug(f"MinIO bucket 已存在: {bucket_name}")
                
                self._bucket_checked[bucket_name] = True
                return True
                
            except S3Error as e:
                logger.error(f"MinIO bucket 检查失败: {e}，服务降级运行")
                return False
            except Exception as e:
                logger.error(f"MinIO 连接失败: {e}，服务降级运行")
                return False


_minio_pool = MinioClientPool()


def get_minio_client() -> Minio:
    """取当前线程 client；勿模块级缓存以免跨线程复用。"""
    return _minio_pool.get_client()


def _parse_s3_endpoint_for_presign(raw: str) -> Tuple[str, Optional[bool]]:
    """Return (host:port, inferred_https_or_none). If URL has no scheme, second is None (use settings)."""
    r = (raw or "").strip()
    if not r:
        return "127.0.0.1:9000", None
    if "://" in r:
        p = urllib.parse.urlparse(r)
        if not p.hostname:
            return r, None
        default_port = 443 if p.scheme == "https" else 80
        port = p.port if p.port is not None else default_port
        return f"{p.hostname}:{port}", p.scheme == "https"
    return r, None


def _minio_host_and_secure_for_app() -> Tuple[str, bool]:
    """SDK upload/bucket: only MINIO_APP_ENDPOINT (default 127.0.0.1:9000). Do not use MINIO_ENDPOINT or minio:9000 here unless explicitly set in MINIO_APP_ENDPOINT."""
    raw = (settings.MINIO_APP_ENDPOINT or "127.0.0.1:9000").strip() or "127.0.0.1:9000"
    host_port, scheme_https = _parse_s3_endpoint_for_presign(raw)
    if scheme_https is not None:
        return host_port, scheme_https
    return host_port, bool(MINIO_SECURE)


def get_minio_client_for_presign() -> Minio:
    """Minio client for presigned_get_object only.

    Presigned URLs: host must be whatever OCR fetches (e.g. ``http://minio:9000``). Upload uses
    ``MINIO_APP_ENDPOINT`` only, never ``MINIO_PUBLIC_ENDPOINT`` or ``minio`` unless you set
    ``MINIO_APP_ENDPOINT`` to that.
    """
    source = (settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT or "127.0.0.1:9000").strip()
    host_port, scheme_https = _parse_s3_endpoint_for_presign(source)
    if settings.MINIO_PRESIGN_SECURE is not None:
        secure = bool(settings.MINIO_PRESIGN_SECURE)
    elif scheme_https is not None:
        secure = scheme_https
    else:
        secure = bool(settings.MINIO_SECURE)
    return Minio(
        host_port,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=secure,
        # Without region, minio-py calls GET /bucket?location= against this endpoint (breaks if host is e.g. minio and not resolvable here)
        region=settings.MINIO_REGION,
        http_client=_build_http_client(),
    )


def upload_to_minio(file_path: str, file_name: str) -> str:
    """fput 上传本地文件；失败抛 :class:`MinioUploadError`。"""
    try:
        if not _minio_pool.ensure_bucket():
            raise MinioUploadError("MinIO service unavailable")

        client = get_minio_client()
        client.fput_object(MINIO_BUCKET_NAME, file_name, file_path)
        logger.debug(f"上传文件成功: {file_name}")
        return file_name
    except MinioUploadError:
        raise
    except S3Error as err:
        logger.error(f"上传文件到 MinIO 失败: {err}")
        raise MinioUploadError(f"上传文件到 MinIO 失败: {err}") from err
    except Exception as e:
        logger.error(f"上传文件异常: {e}")
        raise MinioUploadError(f"上传文件异常: {e}") from e


def upload_buffer_to_minio(
    buffer: IO[bytes],
    file_name: str,
    content_type: str = "application/octet-stream",
    bucket: Optional[str] = None,
) -> str:
    """能 seek 则带 length 直传，否则分块；不要用 read 全长估大小。失败抛 :class:`MinioUploadError`。"""
    target_bucket = bucket or MINIO_BUCKET_NAME
    try:
        if not _minio_pool.ensure_bucket(target_bucket):
            raise MinioUploadError("MinIO service unavailable")

        size = -1
        try:
            current_pos = buffer.tell()
            buffer.seek(0, 2)
            size = buffer.tell()
            buffer.seek(current_pos)
        except (OSError, AttributeError):
            pass

        client = get_minio_client()

        if size > 0:
            client.put_object(
                bucket_name=target_bucket,
                object_name=file_name,
                data=buffer,
                length=size,
                content_type=content_type,
            )
        else:
            client.put_object(
                bucket_name=target_bucket,
                object_name=file_name,
                data=buffer,
                length=-1,
                content_type=content_type,
                part_size=DEFAULT_CHUNK_SIZE,
            )

        logger.debug(f"上传缓冲区成功: {file_name} (size={size})")
        return file_name

    except MinioUploadError:
        raise
    except S3Error as err:
        logger.error(f"上传缓冲区到 MinIO 失败: {err}")
        raise MinioUploadError(f"上传缓冲区到 MinIO 失败: {err}") from err
    except Exception as e:
        logger.error(f"上传缓冲区异常: {e}")
        raise MinioUploadError(f"上传缓冲区异常: {e}") from e


def upload_stream_to_minio(
    file_stream: IO[bytes],
    file_name: str,
    file_size: int = -1,
    content_type: str = "application/octet-stream",
    bucket: Optional[str] = None,
) -> str:
    """file_size>0 直传；否则分块。失败抛 :class:`MinioUploadError`。"""
    target_bucket = bucket or MINIO_BUCKET_NAME
    try:
        if not _minio_pool.ensure_bucket(target_bucket):
            raise MinioUploadError("MinIO service unavailable")

        client = get_minio_client()

        if file_size > 0:
            client.put_object(
                bucket_name=target_bucket,
                object_name=file_name,
                data=file_stream,
                length=file_size,
                content_type=content_type,
            )
        else:
            client.put_object(
                bucket_name=target_bucket,
                object_name=file_name,
                data=file_stream,
                length=-1,
                content_type=content_type,
                part_size=DEFAULT_CHUNK_SIZE,
            )

        logger.debug(f"流式上传成功: {file_name} (size={file_size})")
        return file_name

    except MinioUploadError:
        raise
    except S3Error as err:
        logger.error(f"流式上传到 MinIO 失败: {err}")
        raise MinioUploadError(f"流式上传到 MinIO 失败: {err}") from err
    except Exception as e:
        logger.error(f"流式上传异常: {e}")
        raise MinioUploadError(f"流式上传异常: {e}") from e


def resolve_bucket_for_object(object_name: str) -> str:
    """Infer the MinIO bucket for an object by its key prefix.

    OCR temp artifacts (``temp/...``, ``images/...``) are uploaded via
    ``_target_bucket()`` (i.e. ``MINIO_OCR_TEMP_BUCKET`` when configured, else
    the default bucket). All other objects live in the default bucket. This
    mirrors the upload-side routing in app/integrations/ocr/image2url.py so that
    deletes hit the same bucket the object was written to — without requiring a
    bucket column on FileResource.
    """
    if not object_name:
        return MINIO_BUCKET_NAME
    ocr_bucket = settings.MINIO_OCR_TEMP_BUCKET
    if ocr_bucket and (object_name.startswith("temp/") or object_name.startswith("images/")):
        return ocr_bucket
    return MINIO_BUCKET_NAME


def delete_from_minio(file_name: str, bucket: Optional[str] = None) -> bool:
    """remove_object；异常返回 False。bucket 为 None 时按 object_name 前缀推断。"""
    target_bucket = bucket or resolve_bucket_for_object(file_name)
    try:
        client = get_minio_client()
        client.remove_object(
            bucket_name=target_bucket,
            object_name=file_name,
        )
        logger.debug(f"删除对象成功: {file_name} (bucket={target_bucket})")
        return True
    except S3Error as err:
        logger.error(f"删除 MinIO 对象失败: {err} (bucket={target_bucket}, object={file_name})")
        return False
    except Exception as e:
        logger.error(f"删除对象异常: {e} (bucket={target_bucket}, object={file_name})")
        return False


@contextmanager
def download_object_stream(object_name: str) -> Generator[IO[bytes], None, None]:
    """get_object 流；finally 关闭并 release_conn。"""
    client = get_minio_client()
    response = None
    try:
        response = client.get_object(MINIO_BUCKET_NAME, object_name)
        yield response
    finally:
        if response:
            try:
                response.close()
                response.release_conn()
            except Exception as e:
                logger.warning(f"关闭 MinIO 响应失败: {e}")


def save_file_from_minio(
    object_name: str, 
    temp_prefix: str = "minio_download_"
) -> Path:
    """NamedTemporaryFile(delete=False) 写入；失败删临时文件。路径需调用方自行清理。"""
    temp_file = None
    try:
        temp_file = tempfile.NamedTemporaryFile(
            prefix=temp_prefix, 
            delete=False,
            suffix=Path(object_name).suffix
        )
        temp_path = Path(temp_file.name)
        temp_file.close()
        
        with download_object_stream(object_name) as response:
            with temp_path.open("wb") as file_handle:
                for chunk in response.stream(STREAM_CHUNK_SIZE):
                    file_handle.write(chunk)
        
        logger.debug(f"下载到临时文件: {temp_path}")
        return temp_path
        
    except Exception as exc:
        if temp_file and Path(temp_file.name).exists():
            try:
                Path(temp_file.name).unlink()
            except Exception as unlink_err:
                logger.warning(f"清理下载临时文件失败: {unlink_err}")
        logger.error(f"下载文件失败: {exc}")
        raise RuntimeError(f"下载文件失败: {exc}") from exc
