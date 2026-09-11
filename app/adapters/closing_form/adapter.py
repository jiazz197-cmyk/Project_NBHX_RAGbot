"""Closing form adapters — thin infra wrappers."""

from __future__ import annotations

from typing import Any, Optional

from app.adapters.closing_form.embedding import ClosingFormEmbeddingAdapter
from app.adapters.closing_form.persistence import ClosingFormPersistence
from app.adapters.closing_form.storage import ClosingFormStorageAdapter
from app.ports.outbound.closing_form import (
    ClosingFormEmbeddingPort,
    ClosingFormImageStoragePort,
    ClosingFormPersistencePort,
)


class ClosingFormPersistenceAdapter(ClosingFormPersistencePort):
    """Thin wrapper around ClosingFormPersistence — no business logic."""

    def __init__(self):
        self._p = ClosingFormPersistence()

    async def submit_pending(self, form_text_raw, uploader, image_url_1, image_url_2):
        return await self._p.submit_pending(form_text_raw, uploader, image_url_1, image_url_2)

    async def get_pending_form(self, form_id):
        return await self._p.get_pending_form(form_id)

    async def list_pending_forms(self, *, uploader=None):
        return await self._p.list_pending_forms(uploader=uploader)

    async def list_approved_forms(self, *, uploader=None):
        return await self._p.list_approved_forms(uploader=uploader)

    async def delete_pending_form(self, form_id):
        await self._p.delete_pending_form(form_id)

    async def reject_pending_form(self, form_id):
        await self._p.reject_pending_form(form_id)

    async def get_rejected_form_status(self, form_id):
        return await self._p.get_rejected_form_status(form_id)

    async def get_approved_form(self, record_id):
        return await self._p.get_approved_form(record_id)

    async def delete_approved_form(self, record_id):
        return await self._p.delete_approved_form(record_id)

    async def list_collection2_records(self):
        return await self._p.list_collection2_records()

    async def check_collection2_exists(self, record_id):
        return await self._p.check_collection2_exists(record_id)

    async def delete_collection2_record(self, record_id):
        return await self._p.delete_collection2_record(record_id)


class ClosingFormEmbeddingAdapterPort(ClosingFormEmbeddingPort):
    """Thin wrapper — delegates to existing embedding infra."""

    def __init__(self):
        self._embedding = ClosingFormEmbeddingAdapter()

    def upsert_approved_form(self, text, uploader, upload_time, image_url_1, image_url_2):
        self._embedding.upsert_approved_form(
            text=text,
            uploader=uploader,
            upload_time=upload_time,
            image_url_1=image_url_1,
            image_url_2=image_url_2,
        )


class ClosingFormImageStorageAdapterPort(ClosingFormImageStoragePort):
    """Thin wrapper — delegates to existing storage infra."""

    def __init__(self):
        self._storage = ClosingFormStorageAdapter()

    def upload_image(self, file_stream, original_filename, content_type, uploader):
        return self._storage.upload_image(file_stream, original_filename, content_type, uploader)

    def delete_form_images(self, image_url_1, image_url_2):
        self._storage.delete_form_images(image_url_1, image_url_2)
