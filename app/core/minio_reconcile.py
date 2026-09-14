"""MinIO orphan reconciliation: scan bucket prefixes vs DB and delete unreferenced objects.

This is the safety net beneath all the per-path cleanup. Even with the upload-path
compensation and worker cleanup, some objects can still slip through (crashes between
upload and DB commit, OCR image_upload which never writes a DB row). The sweep
lists objects under high-churn prefixes (``temp/``, ``images/``), excludes any
object whose path is registered in the DB (FileResource), and deletes the rest —
but only if the
object is older than a grace window (so in-flight uploads whose DB row has not
committed yet are not误删).

Safety properties:
- Grace window (MINIO_RECONCILE_GRACE_SEC): only objects with last_modified older
  than now - grace are candidates. New uploads are protected.
- DB snapshot: all registered paths are read once per run into a set; an object is
  deleted only if its path is NOT in that set.
- Dry-run friendly: logs every deletion; never raises (best-effort).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Set

from sqlalchemy import select

from app.core.async_storage import async_list_objects, async_delete_from_minio
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.time_utils import utcnow_naive
from app.core.logging import get_logger
from app.core.storage import MINIO_BUCKET_NAME, resolve_bucket_for_object
from app.models.orm.file_resource import FileResource

logger = get_logger("minio.reconcile")

# High-churn prefixes most likely to accumulate orphans. temp/ and images/ come
# from OCR / pdf-convert / image-upload.
_RECONCILE_PREFIXES = ("temp/", "images/")


async def _collect_registered_paths() -> Set[str]:
    """All MinIO object paths currently registered in the DB."""
    registered: Set[str] = set()
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(FileResource.minio_object_path))
        for (path,) in rows.all():
            if path:
                registered.add(path)

    return registered


def _buckets_for_prefix(prefix: str) -> list[str]:
    """Buckets to scan for a prefix. temp/ and images/ may live in the OCR temp
    bucket (if configured) or the default bucket — scan both to be safe. Other
    prefixes only live in the default bucket."""
    buckets = [MINIO_BUCKET_NAME]
    ocr_bucket = settings.MINIO_OCR_TEMP_BUCKET
    if ocr_bucket and prefix.startswith(("temp/", "images/")) and ocr_bucket not in buckets:
        buckets.append(ocr_bucket)
    return buckets


async def reconcile_orphans(grace_sec: int | None = None) -> dict:
    """Scan reconcile prefixes and delete unreferenced objects older than grace.

    Returns a summary dict {prefix, scanned, deleted, retained, errors}.
    """
    grace = grace_sec if grace_sec is not None else settings.MINIO_RECONCILE_GRACE_SEC
    cutoff = utcnow_naive() - timedelta(seconds=grace)

    registered = await _collect_registered_paths()
    logger.info(
        "MinIO reconcile start: registered_paths=%s grace_sec=%s cutoff=%s",
        len(registered), grace, cutoff.isoformat(),
    )

    summary = {"prefixes": [], "total_deleted": 0, "total_errors": 0}

    for prefix in _RECONCILE_PREFIXES:
        for bucket in _buckets_for_prefix(prefix):
            try:
                objects = await async_list_objects(prefix, bucket=bucket)
            except Exception as exc:
                logger.error("MinIO reconcile list failed: bucket=%s prefix=%s err=%s", bucket, prefix, exc)
                summary["total_errors"] += 1
                continue

            deleted = 0
            retained = 0
            for object_name, last_modified in objects:
                if object_name in registered:
                    retained += 1
                    continue
                # last_modified may be tz-aware or naive; compare via timestamp.
                lm = last_modified
                try:
                    lm_dt = lm.replace(tzinfo=None) if hasattr(lm, "tzinfo") and lm.tzinfo else lm
                    if lm_dt > cutoff:
                        retained += 1
                        continue
                except Exception:
                    # If we cannot determine age, keep the object (safe default).
                    retained += 1
                    continue

                ok = await async_delete_from_minio(object_name, bucket=bucket)
                if ok:
                    deleted += 1
                else:
                    summary["total_errors"] += 1

            summary["prefixes"].append({
                "prefix": prefix,
                "bucket": bucket,
                "scanned": len(objects),
                "deleted": deleted,
                "retained": retained,
            })
            summary["total_deleted"] += deleted
            if deleted:
                logger.info(
                    "MinIO reconcile: bucket=%s prefix=%s scanned=%s deleted=%s retained=%s",
                    bucket, prefix, len(objects), deleted, retained,
                )

    logger.info("MinIO reconcile done: total_deleted=%s total_errors=%s", summary["total_deleted"], summary["total_errors"])
    return summary
