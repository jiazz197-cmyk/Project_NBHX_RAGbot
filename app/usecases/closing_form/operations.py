"""Closing form use cases — owns business logic and orchestration."""

from __future__ import annotations

from app.core.exceptions import APIException, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.closing_form import (
    ClosingFormEmbeddingPort,
    ClosingFormImageStoragePort,
    ClosingFormPersistencePort,
)
from app.ports.dto.closing_form import ClosingFormCommand
from app.usecases.closing_form.results import (
    ClosingFormApproveResult,
    ClosingFormDeleteResult,
    ClosingFormListResult,
    ClosingFormRejectResult,
    ClosingFormReviseResult,
    ClosingFormSubmitResult,
    Collection2ListResult,
)

logger = get_logger("closing_form.usecase")


class SubmitClosingFormUseCase:
    def __init__(self, persistence: ClosingFormPersistencePort):
        self._persistence = persistence

    async def execute(
        self, current_user: CurrentUserPort, cmd: ClosingFormCommand
    ) -> ClosingFormSubmitResult:
        formatted_text = cmd.to_formatted_text()
        result = await self._persistence.submit_pending(
            form_text_raw=formatted_text,
            uploader=current_user.username,
            image_url_1=cmd.image_url_1,
            image_url_2=cmd.image_url_2,
        )
        logger.info("填表已暂存至待审批队列: uploader=%s", current_user.username)
        return ClosingFormSubmitResult(
            success=True,
            message="提交成功，等待审批",
            form_text=result["form_text"],
            image_url_1=cmd.image_url_1,
            image_url_2=cmd.image_url_2,
        )


class ListClosingFormsUseCase:
    def __init__(self, persistence: ClosingFormPersistencePort):
        self._persistence = persistence

    async def execute(self, current_user: CurrentUserPort) -> ClosingFormListResult:
        is_privileged = current_user.is_admin_like()
        uploader_filter = None if is_privileged else current_user.username
        pending = await self._persistence.list_pending_forms(uploader=uploader_filter)
        approved = await self._persistence.list_approved_forms(uploader=uploader_filter)
        records = list(pending) + list(approved)
        records.sort(key=lambda r: r.get("upload_time") or "", reverse=True)
        logger.info(
            "查询填表记录: user=%s, pending=%d, approved=%d",
            current_user.username, len(pending), len(approved),
        )
        return ClosingFormListResult(success=True, total=len(records), records=records)


class ApproveClosingFormUseCase:
    """Owns the approval business flow: validate → embed → cleanup."""

    def __init__(
        self,
        persistence: ClosingFormPersistencePort,
        embedding: ClosingFormEmbeddingPort,
    ):
        self._persistence = persistence
        self._embedding = embedding

    async def execute(
        self, form_id: int, current_user: CurrentUserPort
    ) -> ClosingFormApproveResult:
        row = await self._persistence.get_pending_form(form_id)
        if row is None:
            raise NotFoundError("待审批表单不存在")
        if row["status"] != "pending":
            raise ValidationError(f"表单状态不是待审批（当前状态：{row['status']}）")

        self._embedding.upsert_approved_form(
            text=row["text"],
            uploader=row["uploader"],
            upload_time=str(row["upload_time"]),
            image_url_1=row.get("image_url_1"),
            image_url_2=row.get("image_url_2"),
        )
        await self._persistence.delete_pending_form(form_id)

        logger.info("表单审批通过: form_id=%s, approved_by=%s", form_id, current_user.username)
        return ClosingFormApproveResult(success=True, message="审批通过")


class RejectClosingFormUseCase:
    """Owns the rejection business flow: validate → delete images → mark rejected."""

    def __init__(
        self,
        persistence: ClosingFormPersistencePort,
        image_storage: ClosingFormImageStoragePort,
    ):
        self._persistence = persistence
        self._image_storage = image_storage

    async def execute(
        self, form_id: int, current_user: CurrentUserPort
    ) -> ClosingFormRejectResult:
        row = await self._persistence.get_pending_form(form_id)
        if row is None:
            raise NotFoundError("待审批表单不存在")
        if row["status"] != "pending":
            raise ValidationError(f"表单状态不是待审批（当前状态：{row['status']}）")

        self._image_storage.delete_form_images(row.get("image_url_1"), row.get("image_url_2"))
        await self._persistence.reject_pending_form(form_id)

        logger.info("表单审批不通过: form_id=%s, rejected_by=%s", form_id, current_user.username)
        return ClosingFormRejectResult(success=True, message="审批不通过，已退回待修改")


class ReviseClosingFormUseCase:
    """Owns the revision business flow: validate ownership & status → update form."""

    def __init__(
        self,
        persistence: ClosingFormPersistencePort,
        image_storage: ClosingFormImageStoragePort,
    ):
        self._persistence = persistence
        self._image_storage = image_storage

    async def execute(
        self, form_id: int, current_user: CurrentUserPort, cmd: ClosingFormCommand
    ) -> ClosingFormReviseResult:
        row = await self._persistence.get_pending_form(form_id)
        if row is None:
            raise NotFoundError("表单不存在")
        if row["status"] != "pending_revision":
            raise ValidationError(f"表单状态不是待修改（当前状态：{row['status']}）")
        if row["uploader"] != current_user.username:
            raise ValidationError("只能修改自己提交的表单")

        # Capture old image URLs before overwrite so we can delete the ones that
        # are being replaced (otherwise revise orphans the old MinIO images).
        old_urls = {
            row.get("image_url_1"),
            row.get("image_url_2"),
        }
        new_urls = {cmd.image_url_1, cmd.image_url_2}

        formatted_text = cmd.to_formatted_text()
        await self._persistence.update_pending_form(
            form_id=form_id,
            form_text=formatted_text,
            image_url_1=cmd.image_url_1,
            image_url_2=cmd.image_url_2,
        )

        # Delete old images no longer referenced by the new submission.
        replaced = [u for u in old_urls if u and u not in new_urls]
        if replaced:
            self._image_storage.delete_form_images(*replaced)

        logger.info("表单已修改重提: form_id=%s, user=%s", form_id, current_user.username)
        return ClosingFormReviseResult(success=True, message="修改已提交，等待审批")


class ListCollection2UseCase:
    def __init__(self, persistence: ClosingFormPersistencePort):
        self._persistence = persistence

    async def execute(self) -> Collection2ListResult:
        rows = await self._persistence.list_collection2_records()
        return Collection2ListResult(success=True, total=len(rows), records=list(rows))


class DeleteCollection2RecordUseCase:
    def __init__(self, persistence: ClosingFormPersistencePort):
        self._persistence = persistence

    async def execute(
        self, record_id: int, current_user: CurrentUserPort
    ) -> ClosingFormDeleteResult:
        if not await self._persistence.check_collection2_exists(record_id):
            raise NotFoundError("记录不存在")
        rowcount = await self._persistence.delete_collection2_record(record_id)
        if rowcount <= 0:
            raise APIException("删除失败，请稍后重试", status_code=500)
        logger.info("删除 collection2 记录: record_id=%s, deleted_by=%s", record_id, current_user.username)
        return ClosingFormDeleteResult(success=True, message="删除成功", deleted_id=str(record_id))


class DeleteApprovedClosingFormUseCase:
    def __init__(self, persistence: ClosingFormPersistencePort, image_storage: ClosingFormImageStoragePort):
        self._persistence = persistence
        self._image_storage = image_storage

    async def execute(
        self, record_id: int, current_user: CurrentUserPort
    ) -> ClosingFormDeleteResult:
        row = await self._persistence.get_approved_form(record_id)
        if row is None:
            raise NotFoundError("记录不存在")
        self._image_storage.delete_form_images(row.get("image_url_1"), row.get("image_url_2"))
        rowcount = await self._persistence.delete_approved_form(record_id)
        if rowcount <= 0:
            raise APIException("删除失败，请稍后重试", status_code=500)
        logger.info("删除已通过表单: record_id=%s, deleted_by=%s", record_id, current_user.username)
        return ClosingFormDeleteResult(success=True, message="删除成功", deleted_id=str(record_id))


class DeleteRejectedClosingFormUseCase:
    def __init__(
        self,
        persistence: ClosingFormPersistencePort,
        image_storage: ClosingFormImageStoragePort,
    ):
        self._persistence = persistence
        self._image_storage = image_storage

    async def execute(
        self, form_id: int, current_user: CurrentUserPort
    ) -> ClosingFormDeleteResult:
        status = await self._persistence.get_rejected_form_status(form_id)
        if status is None:
            raise NotFoundError("记录不存在")
        if status not in ("rejected", "pending_revision"):
            raise ValidationError("仅允许删除不通过或待修改状态的表单")
        # Delete any images still attached to the row before removing it. A
        # rejected row's images were already nulled at reject time, but a
        # pending_revision row may carry new images uploaded during revise —
        # those would orphan if we only delete the DB row.
        row = await self._persistence.get_pending_form(form_id)
        if row is not None:
            self._image_storage.delete_form_images(row.get("image_url_1"), row.get("image_url_2"))
        await self._persistence.delete_pending_form(form_id)
        logger.info("删除不通过表单: form_id=%s, deleted_by=%s", form_id, current_user.username)
        return ClosingFormDeleteResult(success=True, message="删除成功", deleted_id=str(form_id))


class UploadClosingFormImageUseCase:
    def __init__(self, image_storage: ClosingFormImageStoragePort):
        self._image_storage = image_storage

    async def execute(self, file_stream, original_filename, content_type, uploader):
        return self._image_storage.upload_image(file_stream, original_filename, content_type, uploader)
