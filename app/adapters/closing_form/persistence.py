"""Closing form persistence: raw SQL operations behind a port-free interface."""

from __future__ import annotations

from typing import List, Optional

from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.time_utils import utcnow_naive
from app.core.logging import get_logger
from app.adapters.closing_form.constants import (
    CLOSING_FORM_TABLE,
    COLLECTION2_TABLE,
    PENDING_TABLE,
)
from app.adapters.doc_processing.pipeline import clean_text_for_postgres

logger = get_logger("closing_form.persistence")


class ClosingFormPersistence:

    async def submit_pending(self, form_text_raw: str, uploader: str, image_url_1: Optional[str], image_url_2: Optional[str]) -> dict:
        form_text = clean_text_for_postgres(form_text_raw)
        upload_time = utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    f"INSERT INTO {PENDING_TABLE} "
                    "(text, uploader, upload_time, status, image_url_1, image_url_2)"
                    " VALUES (:t, :u, :ts, :st, :img1, :img2)"
                ),
                {
                    "t": form_text, "u": uploader, "ts": upload_time, "st": "pending",
                    "img1": image_url_1,
                    "img2": image_url_2,
                },
            )
            await db.commit()
            return {"form_text": form_text, "upload_time": upload_time}

    async def get_pending_form(self, form_id: int) -> Optional[dict]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    f"SELECT id, text, uploader, upload_time, status, image_url_1, image_url_2"
                    f" FROM {PENDING_TABLE}"
                    f" WHERE id = :id"
                ),
                {"id": form_id},
            )
            row = result.first()
            if row is None:
                return None
            return {
                "id": row.id,
                "text": row.text,
                "uploader": row.uploader,
                "upload_time": row.upload_time,
                "status": row.status,
                "image_url_1": row.image_url_1,
                "image_url_2": row.image_url_2,
            }

    async def list_pending_forms(self, *, uploader: Optional[str] = None) -> List[dict]:
        async with AsyncSessionLocal() as db:
            if uploader:
                result = await db.execute(
                    text(
                        f"SELECT id, text, uploader, upload_time, status, image_url_1, image_url_2"
                        f" FROM {PENDING_TABLE}"
                        f" WHERE uploader = :uploader"
                        f" ORDER BY upload_time DESC"
                    ),
                    {"uploader": uploader},
                )
            else:
                result = await db.execute(
                    text(
                        f"SELECT id, text, uploader, upload_time, status, image_url_1, image_url_2"
                        f" FROM {PENDING_TABLE}"
                        f" ORDER BY upload_time DESC"
                    )
                )
            rows = result.fetchall()
            return [
                {
                    "id": str(row.id),
                    "text": row.text or "",
                    "upload_time": row.upload_time,
                    "uploader": row.uploader or "",
                    "status": getattr(row, "status", "pending"),
                    "image_url_1": getattr(row, "image_url_1", None) or None,
                    "image_url_2": getattr(row, "image_url_2", None) or None,
                }
                for row in rows
            ]

    async def list_approved_forms(self, *, uploader: Optional[str] = None) -> List[dict]:
        async with AsyncSessionLocal() as db:
            if uploader:
                result = await db.execute(
                    text(
                        f"SELECT id, text,"
                        f" metadata_->>'upload_time' AS upload_time,"
                        f" metadata_->>'uploader'   AS uploader,"
                        f" metadata_->>'image_url_1' AS image_url_1,"
                        f" metadata_->>'image_url_2' AS image_url_2"
                        f" FROM {CLOSING_FORM_TABLE}"
                        f" WHERE metadata_->>'uploader' = :uploader"
                        f" ORDER BY metadata_->>'upload_time' DESC"
                    ),
                    {"uploader": uploader},
                )
            else:
                result = await db.execute(
                    text(
                        f"SELECT id, text,"
                        f" metadata_->>'upload_time' AS upload_time,"
                        f" metadata_->>'uploader'   AS uploader,"
                        f" metadata_->>'image_url_1' AS image_url_1,"
                        f" metadata_->>'image_url_2' AS image_url_2"
                        f" FROM {CLOSING_FORM_TABLE}"
                        f" ORDER BY metadata_->>'upload_time' DESC"
                    )
                )
            rows = result.fetchall()
            return [
                {
                    "id": str(row.id),
                    "text": row.text or "",
                    "upload_time": row.upload_time,
                    "uploader": row.uploader or "",
                    "status": "approved",
                    "image_url_1": getattr(row, "image_url_1", None) or None,
                    "image_url_2": getattr(row, "image_url_2", None) or None,
                }
                for row in rows
            ]

    async def delete_pending_form(self, form_id: int) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(text(f"DELETE FROM {PENDING_TABLE} WHERE id = :id"), {"id": form_id})
            await db.commit()

    async def update_pending_form(self, form_id: int, form_text: str, image_url_1: Optional[str], image_url_2: Optional[str]) -> None:
        form_text_clean = clean_text_for_postgres(form_text)
        upload_time = utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    f"UPDATE {PENDING_TABLE} SET text = :t, image_url_1 = :img1,"
                    f" image_url_2 = :img2, status = 'pending', upload_time = :ts"
                    f" WHERE id = :id AND status = 'pending_revision'"
                ),
                {"t": form_text_clean, "img1": image_url_1, "img2": image_url_2, "ts": upload_time, "id": form_id},
            )
            await db.commit()

    async def reject_pending_form(self, form_id: int) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    f"UPDATE {PENDING_TABLE} SET status = 'pending_revision',"
                    f" image_url_1 = NULL, image_url_2 = NULL WHERE id = :id"
                ),
                {"id": form_id},
            )
            await db.commit()

    async def get_rejected_form_status(self, form_id: int) -> Optional[str]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"SELECT id, status FROM {PENDING_TABLE} WHERE id = :id"),
                {"id": form_id},
            )
            row = result.first()
            return getattr(row, "status", None) if row else None

    async def get_approved_form(self, record_id: int) -> Optional[dict]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    f"SELECT id, metadata_->>'image_url_1' AS image_url_1,"
                    f" metadata_->>'image_url_2' AS image_url_2"
                    f" FROM {CLOSING_FORM_TABLE} WHERE id = :id"
                ),
                {"id": record_id},
            )
            row = result.first()
            if row is None:
                return None
            return {
                "id": row.id,
                "image_url_1": row.image_url_1,
                "image_url_2": row.image_url_2,
            }

    async def delete_approved_form(self, record_id: int) -> int:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"DELETE FROM {CLOSING_FORM_TABLE} WHERE id = :id"),
                {"id": record_id},
            )
            await db.commit()
            return result.rowcount or 0

    async def list_collection2_records(self) -> List[dict]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    f"SELECT id, text,"
                    f" COALESCE(metadata_->>'file_name', metadata_->>'source', metadata_->>'title') AS file_name,"
                    f" metadata_->>'upload_time' AS upload_time,"
                    f" metadata_->>'uploader'   AS uploader"
                    f" FROM {COLLECTION2_TABLE}"
                    f" ORDER BY metadata_->>'upload_time' DESC NULLS LAST, id DESC"
                )
            )
            rows = result.fetchall()
            return [
                {
                    "id": str(row.id),
                    "text": row.text or "",
                    "file_name": getattr(row, "file_name", None),
                    "upload_time": row.upload_time,
                    "uploader": row.uploader or "",
                    "status": "approved",
                }
                for row in rows
            ]

    async def check_collection2_exists(self, record_id: int) -> bool:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"SELECT id FROM {COLLECTION2_TABLE} WHERE id = :id"),
                {"id": record_id},
            )
            return result.first() is not None

    async def delete_collection2_record(self, record_id: int) -> int:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(f"DELETE FROM {COLLECTION2_TABLE} WHERE id = :id"),
                {"id": record_id},
            )
            await db.commit()
            return result.rowcount or 0
