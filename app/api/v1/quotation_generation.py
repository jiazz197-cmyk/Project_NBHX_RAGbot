"""Quotation generation API: queueing, async execution, persistence."""

from __future__ import annotations

from datetime import datetime
import ipaddress
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.adapters.quotation import (
    MinioFileStorageAdapter,
    ResultPayloadQuotationApprovalSelectionAdapter,
    SqlAlchemyQuotationTaskRepoAdapter,
)
from app.adapters.workers.dispatch import QuotationDispatchAdapter
from app.adapters.quotation.purge import QuotationTaskPurgeAdapter
from app.adapters.quotation.retention import QuotationTaskRetentionAdapter
from app.adapters.tasking import TaskManagerStateAdapter, ThreadPoolTaskExecutionAdapter
from app.core.config import settings
from app.core.dependencies import get_async_db
from app.core.http_headers import build_content_disposition
from app.core.logging import get_logger
from app.core.security import get_current_user, require_permission
from app.core.async_storage import (
    STREAM_CHUNK_SIZE,
    async_download_object_stream,
    async_stat_object,
)
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER, ROLE_ADMIN
from app.models.orm.quotation_task import QuotationTask
from app.usecases.quotation.approve_task import (
    ApproveQuotationTaskCommand,
    ApproveQuotationTaskUseCase,
)
from app.usecases.quotation.cancel_task import CancelQuotationTaskCommand, CancelQuotationTaskUseCase
from app.usecases.quotation.create_task import CreateQuotationTaskCommand, CreateQuotationTaskUseCase
from app.usecases.quotation.create_direct_u8_task import (
    CreateDirectU8TaskCommand,
    CreateDirectU8TaskUseCase,
)
from app.usecases.quotation.delete_task import DeleteQuotationTaskCommand, DeleteQuotationTaskUseCase

router = APIRouter()
logger = get_logger("quotation_generation")
diag_logger = get_logger("diag.quotation")

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class QuotationApprovalData(BaseModel):
    pdm_result: Optional[Dict[str, Any]] = None
    keywords_payload: Optional[Dict[str, Any]] = None
    pdm_partids: Optional[List[str]] = None
    temp_image_url: Optional[str] = None


class QuotationTaskItemResponse(BaseModel):
    task_id: str
    status: str
    progress: int
    message: str
    owner_id: str
    owner_username: str
    uploaded_file_name: str
    display_name: str
    uploaded_file_content_type: str
    uploaded_file_size: int
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    approval_data: Optional[QuotationApprovalData] = None
    error: Optional[str] = None


class QuotationTaskListResponse(BaseModel):
    total: int
    items: List[QuotationTaskItemResponse]


class QuotationTaskSubmitResponse(BaseModel):
    task_id: str
    status: str
    message: str
    queue_position: int = Field(0, description="0 means not queued")


class CancelTaskResponse(BaseModel):
    success: bool
    message: str
    task_id: str


class DeleteTaskResponse(BaseModel):
    success: bool
    message: str
    task_id: str
    cleanup: Dict[str, Any]
    task_record_removed: bool


class ExtraPartidEntry(BaseModel):
    partid: str = Field(..., min_length=1, description="手动补充的 PARTID")
    type: str = Field("", description="手动指定的产品类型（用于分组展示）")


class DirectU8Request(BaseModel):
    partids: List[str] = Field(
        ...,
        min_length=1,
        description="用户直接输入的 U8 父级编码列表，将跳过 Phase1 直接进入 Phase2",
    )
    quantities: Optional[List[float]] = Field(
        None,
        description="与 partids 平行的数量数组（同长度，正数，最多四位小数）。省略时默认 1。",
    )
    task_name: Optional[str] = Field(None, description="任务展示名称（可选）")
    code_type: Optional[str] = Field(
        None,
        description="编码类型标记：'project' 表示项目编码，省略表示 U8 编码",
    )


class ApproveTaskRequest(BaseModel):
    approved_partids: List[str] = Field(
        ...,
        min_length=1,
        description="用户勾选的 PARTID 列表，仅这些将被送入 U8 BOM Inventory",
    )
    extra_partids: List[str] = Field(
        default_factory=list,
        description="用户手动补充的 PARTID，不受限于 Phase1 结果，与表格勾选合并后一同送入 Phase2",
    )
    extra_partid_entries: List[ExtraPartidEntry] = Field(
        default_factory=list,
        description="手动补充的 PARTID + 类型（替换 extra_partids 的增强字段），每条包含 partid 和 type",
    )


class ApproveTaskResponse(BaseModel):
    success: bool
    message: str
    task_id: str
    status: str
    approved_count: int


def _is_admin_like(user: CurrentUserPort) -> bool:
    return user.is_admin_like()


def _build_trusted_proxy_networks() -> list[Any]:
    networks: list[Any] = []
    for raw in settings.TRUSTED_PROXIES:
        value = str(raw).strip()
        if not value:
            continue
        try:
            if "/" in value:
                networks.append(ipaddress.ip_network(value, strict=False))
            else:
                ip = ipaddress.ip_address(value)
                prefix = 32 if ip.version == 4 else 128
                networks.append(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
        except ValueError:
            logger.warning("Ignored invalid trusted proxy config: %s", value)
    return networks


_TRUSTED_PROXY_NETWORKS = _build_trusted_proxy_networks()


def _is_trusted_proxy(client_ip: str) -> bool:
    if not _TRUSTED_PROXY_NETWORKS:
        return False
    try:
        ip_obj = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(ip_obj in network for network in _TRUSTED_PROXY_NETWORKS)


def _extract_request_client_ip(request: Request) -> str:
    direct_ip = (request.client.host if request.client else "") or "unknown"
    if not settings.TRUST_PROXY_HEADERS:
        return direct_ip
    if not _is_trusted_proxy(direct_ip):
        return direct_ip

    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    if not x_forwarded_for:
        return direct_ip

    first_hop = x_forwarded_for.split(",", 1)[0].strip()
    try:
        ipaddress.ip_address(first_hop)
    except ValueError:
        logger.warning("Invalid X-Forwarded-For value: %s", x_forwarded_for)
        return direct_ip
    return first_hop





def _safe_json_size(value: Any) -> Optional[int]:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:
        return None


def _count_statuses(tasks: List[QuotationTask]) -> Dict[str, int]:
    counts = {"awaiting_approval": 0, "active": 0, "completed": 0}
    for task in tasks:
        if task.status == "awaiting_approval":
            counts["awaiting_approval"] += 1
        if task.status in {"queued", "running", "awaiting_approval"}:
            counts["active"] += 1
        if task.status == "completed":
            counts["completed"] += 1
    return counts


def _log_list_tasks_diag(**details: Any) -> None:
    diag_logger.debug("[quotation_tasks_diag] list_tasks | %s", details)


def _serialize_task(task: QuotationTask) -> QuotationTaskItemResponse:
    approval_data: Optional[QuotationApprovalData] = None
    if task.status == "awaiting_approval":
        payload = task.result_payload
        if isinstance(payload, dict):
            approval_data = QuotationApprovalData(
                pdm_result=payload.get("pdm_result"),
                keywords_payload=payload.get("keywords_payload"),
                pdm_partids=payload.get("pdm_partids"),
                temp_image_url=payload.get("temp_image_url"),
            )

    return QuotationTaskItemResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        message=task.message,
        owner_id=task.owner_id,
        owner_username=task.owner_username,
        uploaded_file_name=task.uploaded_file_name,
        display_name=task.display_name,
        uploaded_file_content_type=task.uploaded_file_content_type,
        uploaded_file_size=task.uploaded_file_size,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        approval_data=approval_data,
        error=task.error,
    )


async def _get_task_or_404(db: AsyncSession, task_id: str, *, defer_result: bool = False) -> QuotationTask:
    stmt = select(QuotationTask)
    if defer_result:
        stmt = stmt.options(defer(QuotationTask.result_payload))
    result = await db.execute(stmt.where(QuotationTask.task_id == task_id))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return task


def _check_task_permission(task: QuotationTask, current_user: CurrentUserPort) -> None:
    if _is_admin_like(current_user):
        return
    if task.owner_id != str(current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该任务")


@router.post("/tasks", response_model=QuotationTaskSubmitResponse, summary="创建报价生成任务")
async def create_quotation_task(
    request: Request,
    file: UploadFile = File(..., description="仅支持 PDF 文件"),
    task_name: Optional[str] = Form(None, description="任务展示名称（可选）"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> QuotationTaskSubmitResponse:
    file_data = await file.read()
    usecase = CreateQuotationTaskUseCase(
        task_state=TaskManagerStateAdapter(),
        task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
        file_storage=MinioFileStorageAdapter(),
        task_execution=ThreadPoolTaskExecutionAdapter(),
        task_dispatch=QuotationDispatchAdapter(),
        retention=QuotationTaskRetentionAdapter(QuotationTaskPurgeAdapter()),
    )
    result = await usecase.execute(
        CreateQuotationTaskCommand(
            file_name=file.filename,
            task_name=task_name,
            content_type=file.content_type,
            file_bytes=file_data,
            max_file_size=settings.MAX_FILE_SIZE,
            owner_id=str(current_user.id),
            owner_username=current_user.username,
            owner_ip=_extract_request_client_ip(request),
            role_snapshot=current_user.role,
        )
    )
    return QuotationTaskSubmitResponse(
        task_id=result.task_id,
        status=result.status,
        message=result.message,
        queue_position=result.queue_position,
    )


@router.post("/tasks/direct-u8", response_model=QuotationTaskSubmitResponse, summary="直接进行 U8 查询")
async def create_direct_u8_task(
    request: Request,
    body: DirectU8Request,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> QuotationTaskSubmitResponse:
    usecase = CreateDirectU8TaskUseCase(
        task_state=TaskManagerStateAdapter(),
        task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
        file_storage=MinioFileStorageAdapter(),
        task_execution=ThreadPoolTaskExecutionAdapter(),
        task_dispatch=QuotationDispatchAdapter(),
    )
    result = await usecase.execute(
        CreateDirectU8TaskCommand(
            partids=body.partids,
            quantities=body.quantities,
            task_name=body.task_name,
            code_type=body.code_type,
            owner_id=str(current_user.id),
            owner_username=current_user.username,
            owner_ip=_extract_request_client_ip(request),
            role_snapshot=current_user.role,
        )
    )
    return QuotationTaskSubmitResponse(
        task_id=result.task_id,
        status=result.status,
        message=result.message,
        queue_position=result.queue_position,
    )


@router.get("/tasks", response_model=QuotationTaskListResponse, summary="查询报价任务列表")
async def list_quotation_tasks(
    status_filter: Optional[str] = Query(None, alias="status", description="按状态过滤"),
    owner_username: Optional[str] = Query(None, description="管理员可按用户名过滤"),
    limit: int = Query(100, ge=1, le=500),
    active_only: bool = Query(False, description="仅返回活动任务(queued/running/awaiting_approval)"),
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> QuotationTaskListResponse:
    started_at = time.perf_counter()
    stmt = select(QuotationTask).options(defer(QuotationTask.result_payload))
    if not _is_admin_like(current_user):
        stmt = stmt.where(QuotationTask.owner_id == str(current_user.id))
    elif owner_username:
        stmt = stmt.where(QuotationTask.owner_username == owner_username)
    if active_only:
        stmt = stmt.where(
            QuotationTask.status.in_(["queued", "running", "awaiting_approval"])
        )
    elif status_filter:
        stmt = stmt.where(QuotationTask.status == status_filter)
    stmt = stmt.order_by(QuotationTask.created_at.desc()).limit(limit)
    query_started_at = time.perf_counter()
    result = await db.execute(stmt)
    tasks = list(result.scalars().all())
    query_done_at = time.perf_counter()
    for task in tasks:
        if task.status == "awaiting_approval":
            await db.refresh(task, ["result_payload"])
    status_counts = _count_statuses(tasks)
    serialized_items = [_serialize_task(task) for task in tasks]
    serialize_done_at = time.perf_counter()
    response = QuotationTaskListResponse(
        total=len(tasks),
        items=serialized_items,
    )
    response_ready_at = time.perf_counter()
    approx_payload_chars = _safe_json_size(response.model_dump(mode="json"))
    _log_list_tasks_diag(
        limit=limit,
        active_only=active_only,
        status_filter=status_filter,
        owner_username=owner_username,
        current_user=current_user.username,
        current_role=current_user.role,
        tasks_count=len(tasks),
        awaiting_approval_count=status_counts["awaiting_approval"],
        active_count=status_counts["active"],
        completed_count=status_counts["completed"],
        query_ms=round((query_done_at - query_started_at) * 1000, 2),
        serialize_ms=round((serialize_done_at - query_done_at) * 1000, 2),
        response_build_ms=round((response_ready_at - serialize_done_at) * 1000, 2),
        total_ms=round((response_ready_at - started_at) * 1000, 2),
        approx_payload_chars=approx_payload_chars,
    )
    return response


@router.get("/tasks/{task_id}", response_model=QuotationTaskItemResponse, summary="查询报价任务详情")
async def get_quotation_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> QuotationTaskItemResponse:
    task = await _get_task_or_404(db, task_id, defer_result=True)
    _check_task_permission(task, current_user)
    if task.status == "awaiting_approval":
        await db.refresh(task, ["result_payload"])
    return _serialize_task(task)


@router.post("/tasks/{task_id}/cancel", response_model=CancelTaskResponse, summary="取消报价任务")
async def cancel_quotation_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> CancelTaskResponse:
    task = await _get_task_or_404(db, task_id)
    _check_task_permission(task, current_user)
    usecase = CancelQuotationTaskUseCase(
        task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
        task_state=TaskManagerStateAdapter(),
        task_execution=ThreadPoolTaskExecutionAdapter(),
        task_dispatch=QuotationDispatchAdapter(),
    )
    result = await usecase.execute(CancelQuotationTaskCommand(task_id=task_id))
    return CancelTaskResponse(
        success=result.success,
        message=result.message,
        task_id=result.task_id,
    )


@router.delete("/tasks/{task_id}", response_model=DeleteTaskResponse, summary="删除已结束报价任务")
async def delete_quotation_task(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> DeleteTaskResponse:
    task = await _get_task_or_404(db, task_id)
    _check_task_permission(task, current_user)
    usecase = DeleteQuotationTaskUseCase(
        task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
        purge_port=QuotationTaskPurgeAdapter(),
    )
    result = await usecase.execute(DeleteQuotationTaskCommand(task_id=task_id))
    return DeleteTaskResponse(
        success=result.success,
        message=result.message,
        task_id=result.task_id,
        cleanup=result.cleanup,
        task_record_removed=result.task_record_removed,
    )


@router.post(
    "/tasks/{task_id}/approve",
    response_model=ApproveTaskResponse,
    summary="同意 PDM 审核并触发 Phase2 (U8 BOM Inventory)",
)
async def approve_quotation_task(
    task_id: str,
    request: ApproveTaskRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
) -> ApproveTaskResponse:
    diag_logger.debug(
        "[diag_approve] 收到审批请求 task_id=%s approved_count=%s extra_count=%s extra_entries=%s",
        task_id,
        len(request.approved_partids),
        len(request.extra_partids),
        [(e.partid, e.type) for e in request.extra_partid_entries],
    )
    task = await _get_task_or_404(db, task_id)
    _check_task_permission(task, current_user)
    usecase = ApproveQuotationTaskUseCase(
        task_repo=SqlAlchemyQuotationTaskRepoAdapter(db),
        approval_selection=ResultPayloadQuotationApprovalSelectionAdapter(db),
        task_state=TaskManagerStateAdapter(),
        task_dispatch=QuotationDispatchAdapter(),
    )
    result = await usecase.execute(
        ApproveQuotationTaskCommand(
            task_id=task_id,
            approved_partids=request.approved_partids,
            extra_partids=request.extra_partids,
            extra_partid_entries=[
                {"partid": e.partid, "type": e.type} for e in request.extra_partid_entries
            ],
        )
    )
    return ApproveTaskResponse(
        success=result.success,
        message=result.message,
        task_id=result.task_id,
        status=result.status,
        approved_count=result.approved_count,
    )


@router.get("/tasks/{task_id}/file", summary="查看或下载任务上传文件")
async def get_quotation_task_file(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
):
    task = await _get_task_or_404(db, task_id)
    _check_task_permission(task, current_user)
    try:
        await async_stat_object(task.uploaded_file_minio_path)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="文件已清理，当前不可查看"
        ) from None

    async def stream_file():
        response = await async_download_object_stream(task.uploaded_file_minio_path)
        try:
            async for chunk in response.content.iter_chunked(STREAM_CHUNK_SIZE):
                yield chunk
        finally:
            try:
                response.close()
            except Exception:
                pass

    return StreamingResponse(
        stream_file(),
        media_type=task.uploaded_file_content_type,
        headers={"Content-Disposition": build_content_disposition(task.uploaded_file_name)},
    )


@router.get(
    "/tasks/{task_id}/u8-by-type-workbook",
    summary="下载 Phase2 U8 按 type 分组的 Excel",
)
async def get_quotation_task_u8_by_type_workbook(
    task_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: CurrentUserPort = Depends(require_permission("view_quotation")),
):
    task = await _get_task_or_404(db, task_id)
    _check_task_permission(task, current_user)
    payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    minio_path = payload.get("u8_result_by_type_xlsx_minio_path")
    filename = payload.get("u8_result_by_type_xlsx_filename") or "u8_by_type.xlsx"
    if not minio_path or not isinstance(minio_path, str):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="U8 分组 Excel 未生成或不可用",
        )
    try:
        await async_stat_object(minio_path)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Excel 已不可用",
        ) from None

    async def stream_xlsx():
        response = await async_download_object_stream(minio_path)
        try:
            async for chunk in response.content.iter_chunked(STREAM_CHUNK_SIZE):
                yield chunk
        finally:
            try:
                response.close()
            except Exception:
                pass

    safe_name = str(filename).replace('"', "")
    return StreamingResponse(
        stream_xlsx(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": build_content_disposition(safe_name)},
    )
