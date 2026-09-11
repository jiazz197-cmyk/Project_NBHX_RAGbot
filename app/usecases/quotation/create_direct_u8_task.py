"""Usecase: create a direct U8 BOM query task (skip Phase1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from typing import List, Optional
from uuid import uuid4

from app.core.exceptions import APIException
from app.core.logging import get_logger
from app.ports.contracts.tasking import TaskDispatchPort, TaskExecutionPort, TaskStatePort
from app.ports.outbound.quotation import FileStoragePort, QuotationTaskRepoPort

logger = get_logger("quotation.create_direct_u8_task")

_MAX_PARTIDS = 1500


@dataclass
class CreateDirectU8TaskCommand:
    partids: List[str]
    quantities: Optional[List[float]]
    task_name: Optional[str]
    code_type: Optional[str]
    owner_id: str
    owner_username: str
    owner_ip: Optional[str]
    role_snapshot: str


@dataclass
class CreateDirectU8TaskResult:
    task_id: str
    status: str
    message: str
    queue_position: int
    approved_partids: List[str] = field(default_factory=list)
    manual_partid_types: dict = field(default_factory=dict)
    manual_partid_quantities: dict = field(default_factory=dict)


class CreateDirectU8TaskUseCase:
    """Validate PARTIDs → upload placeholder PDF → create task → dispatch Phase2."""

    def __init__(
        self,
        task_state: TaskStatePort,
        task_repo: QuotationTaskRepoPort,
        file_storage: FileStoragePort,
        task_execution: TaskExecutionPort,
        task_dispatch: TaskDispatchPort,
    ):
        self._task_state = task_state
        self._task_repo = task_repo
        self._file_storage = file_storage
        self._task_execution = task_execution
        self._task_dispatch = task_dispatch

    async def execute(self, cmd: CreateDirectU8TaskCommand) -> CreateDirectU8TaskResult:
        partids, manual_partid_quantities = self._validate_and_dedupe(cmd)

        unique_name = f"direct_u8_{uuid4().hex}.pdf"
        minio_path = f"quotation/uploads/{unique_name}"
        await self._file_storage.upload_pdf(
            object_path=minio_path,
            file_bytes=b"\x00",
            content_type="application/pdf",
        )

        # After the placeholder PDF uploads, any failure before create_task
        # commits would orphan it. Wrap the persist section to reclaim.
        try:
            file_id = await self._task_repo.create_file_record(
                file_name=unique_name,
                unique_name=unique_name,
                minio_path=minio_path,
                content_type="application/pdf",
                file_size=1,
                uploader=cmd.owner_username,
            )

            task_name = (cmd.task_name or "").strip() or "直接U8查询"
            task_id = await self._task_state.create_task(
                task_type="quotation_generation",
                metadata={
                    "owner_username": cmd.owner_username,
                    "owner_ip": cmd.owner_ip,
                    "file_id": file_id,
                    "file_name": unique_name,
                    "task_name": task_name,
                },
            )

            await self._task_repo.create_task(
                task_id=task_id,
                owner_id=cmd.owner_id,
                owner_username=cmd.owner_username,
                owner_ip=cmd.owner_ip,
                role_snapshot=cmd.role_snapshot,
                uploaded_file_id=file_id,
                uploaded_file_name=unique_name,
                display_name=task_name,
                uploaded_file_minio_path=minio_path,
                uploaded_file_content_type="application/pdf",
                uploaded_file_size=1,
            )
        except Exception:
            try:
                from app.core.async_storage import async_delete_from_minio
                await async_delete_from_minio(minio_path)
                logger.warning("创建直传U8任务失败后回删占位 PDF: %s", minio_path)
            except Exception as cleanup_err:
                logger.error("回删占位 PDF 失败 path=%s err=%s", minio_path, cleanup_err)
            raise

        self._task_execution.set_task_owner(task_id, cmd.owner_id)

        manual_partid_types: dict = {p: p for p in partids}

        await self._task_repo.patch_task(
            task_id,
            {
                "status": "running",
                "progress": 55,
                "message": "开始 U8 BOM Inventory 查询",
                "result_payload": {
                    "approved_partids": partids,
                    "manual_partid_types": manual_partid_types,
                    "manual_partid_quantities": manual_partid_quantities,
                    "code_type": cmd.code_type,
                },
                "error": None,
            },
        )

        self._task_dispatch.dispatch_phase2(task_id, cmd.owner_id)

        logger.info("直接 U8 查询任务已创建: task_id=%s, owner=%s, partids=%d", task_id, cmd.owner_username, len(partids))

        return CreateDirectU8TaskResult(
            task_id=task_id,
            status="running",
            message="直接 U8 查询已创建",
            queue_position=0,
            approved_partids=partids,
            manual_partid_types=manual_partid_types,
            manual_partid_quantities=manual_partid_quantities,
        )

    @staticmethod
    def _validate_and_dedupe(cmd: CreateDirectU8TaskCommand):
        if cmd.quantities is not None and len(cmd.quantities) != len(cmd.partids):
            raise APIException(
                "quantities 长度必须与 partids 一致",
                status_code=400,
                error_code="INVALID_QUANTITIES",
            )
        if cmd.code_type is not None and cmd.code_type != "project":
            raise APIException(
                "code_type 仅支持 'project'，省略表示 U8 编码",
                status_code=400,
                error_code="INVALID_CODE_TYPE",
            )

        seen: set[str] = set()
        partids: List[str] = []
        manual_partid_quantities: dict[str, float] = {}
        for idx, raw in enumerate(cmd.partids):
            value = str(raw).strip()
            if not value or value in seen:
                continue
            if cmd.quantities is not None:
                qty = cmd.quantities[idx]
                if not isfinite(qty) or qty <= 0:
                    raise APIException(
                        f"quantities[{idx}] 必须为正数（最多四位小数）",
                        status_code=400,
                        error_code="INVALID_QUANTITY",
                    )
                if Decimal(str(qty)).as_tuple().exponent < -4:
                    raise APIException(
                        f"quantities[{idx}] 最多四位小数",
                        status_code=400,
                        error_code="INVALID_QUANTITY",
                    )
            else:
                qty = 1
            seen.add(value)
            partids.append(value)
            manual_partid_quantities[value] = qty

        if not partids:
            raise APIException("至少需要提供一个 PARTID", status_code=400, error_code="EMPTY_PARTIDS")
        if len(partids) > _MAX_PARTIDS:
            raise APIException(f"最多支持 {_MAX_PARTIDS} 个 PARTID", status_code=400, error_code="TOO_MANY_PARTIDS")

        return partids, manual_partid_quantities
