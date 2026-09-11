"""Closing form ports — fine-grained interfaces."""

from __future__ import annotations

from typing import Any, Optional, Protocol


class ClosingFormPersistencePort(Protocol):
    """Atomic persistence operations for closing forms (no business logic)."""

    async def submit_pending(self, form_text_raw: str, uploader: str, image_url_1: Optional[str], image_url_2: Optional[str]) -> dict:
        ...

    async def get_pending_form(self, form_id: int) -> Optional[dict]:
        ...

    async def list_pending_forms(self, *, uploader: Optional[str] = None) -> list[dict]:
        ...

    async def list_approved_forms(self, *, uploader: Optional[str] = None) -> list[dict]:
        ...

    async def delete_pending_form(self, form_id: int) -> None:
        ...

    async def update_pending_form(self, form_id: int, form_text: str, image_url_1: Optional[str], image_url_2: Optional[str]) -> None:
        ...

    async def reject_pending_form(self, form_id: int) -> None:
        ...

    async def get_rejected_form_status(self, form_id: int) -> Optional[str]:
        ...

    async def get_approved_form(self, record_id: int) -> Optional[dict]:
        ...

    async def delete_approved_form(self, record_id: int) -> int:
        ...

    async def list_collection2_records(self) -> list[dict]:
        ...

    async def check_collection2_exists(self, record_id: int) -> bool:
        ...

    async def delete_collection2_record(self, record_id: int) -> int:
        ...


class ClosingFormEmbeddingPort(Protocol):
    """Vector embedding operations for approved closing forms."""

    def upsert_approved_form(
        self,
        text: str,
        uploader: str,
        upload_time: str,
        image_url_1: Optional[str],
        image_url_2: Optional[str],
    ) -> None:
        ...


class ClosingFormImageStoragePort(Protocol):
    """Image upload / deletion for closing form attachments."""

    def upload_image(self, file_stream: Any, original_filename: str, content_type: str, uploader: str) -> str:
        ...

    def delete_form_images(self, image_url_1: Optional[str], image_url_2: Optional[str]) -> None:
        ...
