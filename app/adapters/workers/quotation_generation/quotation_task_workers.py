"""Quotation background workers: Phase1/2 and queue dispatch."""

from __future__ import annotations

import asyncio
import re
import threading
from datetime import datetime
from io import BytesIO
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.core.task_manager import TaskManager, task_manager
from app.core.executor import CancellationToken, executor_manager
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.adapters.quotation.dispatcher import quotation_dispatcher
from app.adapters.quotation.task_persistence import QuotationTaskPersistenceAdapter
from app.core.storage import save_file_from_minio, upload_stream_to_minio
from app.core.quotation_task_cleanup import cleanup_task_files_by_id_sync
from app.core.task_owner_registry import task_owner_registry
from app.adapters.quotation.deps import (
    build_quotation_workbook_use_case,
    build_execute_quotation_phase1_use_case,
    build_execute_quotation_phase2_use_case,
)
from app.domain.quotation.entities import QuotationTaskStatus
from app.domain.quotation.exceptions import QuotationPipelineCancelledError, QuotationPipelineError
from app.domain.quotation.partid_mapping import convert_partids_to_u8_codes
from app.adapters.sqlserver.exceptions import U8RootFailureBreakerError
from app.usecases.quotation.build_workbook import BuildQuotationWorkbookCommand
from app.usecases.quotation.execute_phase1 import ExecuteQuotationPhase1Command
from app.usecases.quotation.execute_phase2 import ExecuteQuotationPhase2Command

_persistence = QuotationTaskPersistenceAdapter()
_quotation_dispatch_loop: asyncio.AbstractEventLoop | None = None

logger = get_logger("quotation_generation")
diag_logger = get_logger("diag.phase2")

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _extract_partid_quantities(payload: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Read ``manual_partid_quantities`` from a task payload and validate types.

    Returns a sanitized ``{partid: float}`` mapping (positive floats only;
    invalid entries skipped) or ``None`` when the field is missing/empty/malformed.
    """
    raw = payload.get("manual_partid_quantities")
    if not isinstance(raw, dict) or not raw:
        return None
    cleaned: Dict[str, float] = {}
    for key, value in raw.items():
        partid = str(key).strip()
        if not partid:
            continue
        try:
            qty = float(value)
        except (TypeError, ValueError):
            continue
        if not isfinite(qty) or qty <= 0:
            continue
        cleaned[partid] = qty
    return cleaned or None


def _upload_u8_result_by_type_xlsx_to_minio(
    *,
    task_id: str,
    uploaded_file_name: str,
    task_name: str = "",
    u8_result_by_type: Dict[str, Any],
    summary_selection_items: Any = None,
    raw_extracted_info: Any = None,
    keywords_payload: Any = None,
    partid_quantities: Optional[Dict[str, float]] = None,
    cancel_checker: Optional[Callable[[], bool]] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Build multi-sheet xlsx from ``u8_result_by_type`` and upload via sync MinIO.

    Returns ``(minio_object_path, download_filename)`` or ``(None, None)`` if skipped or upload fails.
    """
    try:
        workbook_uc = build_quotation_workbook_use_case()
        xlsx_export = workbook_uc.execute(
            BuildQuotationWorkbookCommand(
                u8_result_by_type=u8_result_by_type,
                summary_selection_items=summary_selection_items,
                raw_extracted_info=raw_extracted_info,
                keywords_payload=keywords_payload,
                # Intentionally Asia/Shanghai — report generation timestamps must reflect local business timezone
                generated_at=datetime.now(ZoneInfo("Asia/Shanghai")),
                partid_quantities=partid_quantities,
                cancel_checker=cancel_checker,
            )
        )
    except ImportError as exc:
        logger.warning("Phase2 skip u8_by_type xlsx: openpyxl missing (%s)", exc)
        return None, None

    stem = Path(uploaded_file_name or "").stem or "quotation"
    name_source = (task_name or "").strip() or stem
    safe_name = re.sub(
        r"[^\w\u4e00-\u9fff\-.]+",
        "_",
        name_source,
    ).strip("_") or "quotation"
    filename = f"{safe_name}.xlsx"
    object_path = f"quotation-results/{task_id}/{filename}"
    try:
        upload_stream_to_minio(
            file_stream=BytesIO(xlsx_export.content),
            file_name=object_path,
            file_size=len(xlsx_export.content),
            content_type=_XLSX_CONTENT_TYPE,
        )
    except Exception as exc:
        logger.warning(
            "Phase2 u8_by_type xlsx upload error task_id=%s: %s",
            task_id,
            exc,
            exc_info=True,
        )
        return None, None

    return object_path, filename


def set_quotation_dispatch_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Register the main application event loop used for async DB dispatch.

    Worker-thread callbacks must not create fresh event loops for asyncpg-backed
    SQLAlchemy sessions because asyncpg connections are bound to their original
    loop.  They should submit dispatch coroutines back to this loop instead.
    """
    global _quotation_dispatch_loop
    _quotation_dispatch_loop = loop


def _schedule_dispatch_coro(coro, *, log_label: str, owner_id: str) -> None:
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    dispatch_loop = _quotation_dispatch_loop
    if dispatch_loop is not None and not dispatch_loop.is_closed():
        if running_loop is dispatch_loop:
            dispatch_loop.create_task(coro)
        else:
            future = asyncio.run_coroutine_threadsafe(coro, dispatch_loop)

            def _log_dispatch_failure(done, owner_id: str = owner_id, log_label: str = log_label) -> None:
                try:
                    exc = done.exception()
                except Exception as callback_exc:
                    logger.error(
                        "%s 回投主循环状态检查失败 owner_id=%s: %s",
                        log_label,
                        owner_id,
                        callback_exc,
                        exc_info=True,
                    )
                    return
                if exc is not None:
                    logger.error(
                        "%s 回投主循环失败 owner_id=%s: %s",
                        log_label,
                        owner_id,
                        exc,
                        exc_info=(type(exc), exc, exc.__traceback__),
                    )

            future.add_done_callback(_log_dispatch_failure)
        return

    if running_loop is not None:
        running_loop.create_task(coro)
        return

    coro.close()
    logger.error(
        "%s 失败 owner_id=%s: 主事件循环未注册，拒绝在临时事件循环中使用 async DB",
        log_label,
        owner_id,
    )


async def _dispatch_quotation_queue_for_owner_async(owner_id: str) -> None:
    """Async implementation: dequeue queued tasks and submit them to the executor."""
    try:
        dispatch_items = await quotation_dispatcher.dequeue_for_owner(owner_id)
    except Exception as exc:
        logger.error(f"调度报价任务失败 owner_id={owner_id}: {exc}", exc_info=True)
        return
    for item in dispatch_items:
        try:
            future = executor_manager.submit_task(
                item.task_id,
                process_quotation_task_background,
                item.task_id,
            )
            task_owner_registry.cache(item.task_id, item.owner_id)
            future.add_done_callback(lambda _: dispatch_quotation_queue_global())
        except Exception as exc:
            logger.error(f"提交报价任务到执行器失败 {item.task_id}: {exc}", exc_info=True)
            # Directly await – we are already in an async context.
            try:
                await _persistence.mark_task_failed(item.task_id, f"执行器提交失败: {exc}")
            except Exception as db_exc:
                logger.error(f"标记任务失败状态异常 {item.task_id}: {db_exc}")
            try:
                await task_manager.fail_task(item.task_id, str(exc), "任务提交执行器失败")
            except Exception as tm_exc:
                logger.error(f"TaskManager 标记失败异常 {item.task_id}: {tm_exc}")


def dispatch_quotation_queue_for_owner(owner_id: str) -> None:
    """Schedule queued tasks for the given owner on the main event loop.

    Async SQLAlchemy/asyncpg connections are loop-affine.  When this function is
    called from executor worker callbacks, submit the coroutine back to the main
    loop instead of creating a fresh loop.
    """
    _schedule_dispatch_coro(
        _dispatch_quotation_queue_for_owner_async(owner_id),
        log_label="报价队列调度",
        owner_id=owner_id,
    )


async def _dispatch_quotation_queue_global_async() -> None:
    """Global re-dispatch sweep across ALL owners with queued work.

    Per-IP quota is a cross-owner SHARED resource.  The per-owner done-callback
    only re-checks the completing task's own owner, so queued tasks that belong
    to a DIFFERENT owner but share the same ``owner_ip`` can starve indefinitely
    once an IP slot frees up (e.g. several users behind the same nginx/NAT IP).
    This sweep fixes that by reconsidering every owner that currently has queued
    tasks — each ``dequeue_for_owner`` re-validates both owner and IP quotas, so
    it is safe and idempotent.
    """
    try:
        owners = await quotation_dispatcher.list_queued_owners()
    except Exception as exc:
        logger.error(f"全局报价队列调度失败（列举排队 owner）: {exc}", exc_info=True)
        return
    if not owners:
        return
    logger.debug("全局报价队列重调度: owners=%s", owners)
    for owner_id in owners:
        await _dispatch_quotation_queue_for_owner_async(owner_id)


# ── Global dispatch debounce ─────────────────────────────────────────────
# done-callbacks fire from executor worker threads.  Without coalescing, a
# burst of ~K near-simultaneous completions (EXECUTOR_MAX_WORKERS up to ~30)
# each schedules a redundant full owner-scan on the main loop — O(K*M) DB
# roundtrips for M queued owners.  The debounce collapses any burst within
# `_GLOBAL_DISPATCH_DEBOUNCE_SEC` into a single sweep: the first call arms a
# short timer on the main loop; subsequent calls during the window are dropped
# (the armed sweep already covers all currently-queued owners at sweep time).
# A completion arriving *during* the sweep re-arms a follow-up sweep because
# `_debounce_pending` is cleared right before the sweep runs.
_GLOBAL_DISPATCH_DEBOUNCE_SEC = 0.3
_debounce_lock = threading.Lock()
_debounce_pending: bool = False


async def _debounced_global_dispatch_async() -> None:
    """Wait the debounce window, then run one global sweep.

    The pending flag is cleared *before* the sweep so a completion arriving
    during the sweep arms a follow-up sweep (no lost re-dispatch).  Each sweep
    re-scans all queued owners, so dropping calls during the window is safe.
    """
    global _debounce_pending
    await asyncio.sleep(_GLOBAL_DISPATCH_DEBOUNCE_SEC)
    with _debounce_lock:
        _debounce_pending = False
    await _dispatch_quotation_queue_global_async()


def dispatch_quotation_queue_global() -> None:
    """Schedule a (debounced) global re-dispatch sweep on the main event loop.

    Called from executor done-callbacks (worker threads).  Burst completions
    coalesce into a single sweep ``_GLOBAL_DISPATCH_DEBOUNCE_SEC`` after the
    first call.  See ``_dispatch_quotation_queue_global_async``.
    """
    global _debounce_pending
    dispatch_loop = _quotation_dispatch_loop
    if dispatch_loop is None or dispatch_loop.is_closed():
        # Loop unavailable (e.g. shutdown): bypass the debounce and let
        # _schedule_dispatch_coro handle/log it, so we never leave
        # _debounce_pending stuck True (which would block all future dispatch).
        _schedule_dispatch_coro(
            _dispatch_quotation_queue_global_async(),
            log_label="报价队列全局调度",
            owner_id="*",
        )
        return
    with _debounce_lock:
        if _debounce_pending:
            # A sweep is already armed; it will cover all queued owners at its
            # run time, so this call can be safely dropped.
            return
        _debounce_pending = True
    _schedule_dispatch_coro(
        _debounced_global_dispatch_async(),
        log_label="报价队列全局调度",
        owner_id="*",
    )


# Keep progress messages compact; detailed query parameters belong in logs, not task cards.
_MESSAGE_COLUMN_LIMIT = 220


def _clamp_message(message: str, limit: int = _MESSAGE_COLUMN_LIMIT) -> str:
    """Ensure progress messages fit inside quotation_tasks.message (VARCHAR 512)."""
    if message is None:
        return ""
    if len(message) <= limit:
        return message
    suffix = "…(truncated)"
    head = max(0, limit - len(suffix))
    return message[:head] + suffix


def _u8_by_type_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {"total": 0, "items": []}
    groups = value.get("items")
    compact_groups = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            compact_groups.append(
                {
                    "type": group.get("type"),
                    "u8_parent_inv_codes": group.get("u8_parent_inv_codes"),
                    "total": group.get("total"),
                    "items_count": len(group.get("items")) if isinstance(group.get("items"), list) else 0,
                }
            )
    return {
        "total": value.get("total"),
        "items_count": len(groups) if isinstance(groups, list) else 0,
        "items": compact_groups,
    }


def _close_thread_tm(loop: asyncio.AbstractEventLoop, thread_tm: TaskManager) -> None:
    try:
        if hasattr(thread_tm, "storage") and hasattr(thread_tm.storage, "redis_client"):
            if thread_tm.storage.redis_client is not None:
                async def close_redis() -> None:
                    if hasattr(thread_tm.storage.redis_client, "connection_pool"):
                        await thread_tm.storage.redis_client.connection_pool.disconnect()
                    await thread_tm.storage.redis_client.aclose()

                loop.run_until_complete(close_redis())
    except Exception:
        pass
    finally:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass


def process_quotation_task_background(token: CancellationToken, task_id: str) -> Dict[str, Any]:
    """Phase 1 worker: OCR -> keywords_payload -> PDM BOM, then pause for approval."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    thread_tm = TaskManager.create_thread_safe_instance()
    existing_payload: Dict[str, Any] = {}
    uploaded_file_minio_path = ""
    uploaded_file_name = ""
    # Track the temp image path from Phase1 so cancel/exception branches can pass
    # it to cleanup even when it was never persisted to DB (prevents orphan).
    phase1_temp_image_path: Optional[str] = None

    try:
        task_data = _persistence.get_task_payload_sync(task_id)
        if not task_data:
            return {"status": "error", "message": f"任务不存在: {task_id}"}
        existing_payload = dict(task_data.get("result_payload", {}))
        uploaded_file_minio_path = task_data.get("uploaded_file_minio_path", "")
        uploaded_file_name = task_data.get("uploaded_file_name", "")

        loop.run_until_complete(thread_tm.start_task(task_id))

        def update_progress(progress: int, message: str) -> None:
            safe_message = _clamp_message(message)
            safe_progress = max(0, min(100, progress))
            _persistence.patch_task_fields_sync(
                task_id,
                {
                    "progress": safe_progress,
                    "message": safe_message,
                    "status": QuotationTaskStatus.running.value,
                },
            )
            loop.run_until_complete(
                thread_tm.update_task_progress(task_id, safe_progress, safe_message)
            )

        if token.is_cancelled():
            raise QuotationPipelineCancelledError("任务在启动前被取消")

        update_progress(5, "正在下载上传PDF")
        temp_pdf = save_file_from_minio(uploaded_file_minio_path)
        try:
            pdf_bytes = temp_pdf.read_bytes()
        finally:
            temp_pdf.unlink(missing_ok=True)

        phase1_uc = build_execute_quotation_phase1_use_case()
        phase1_result = phase1_uc.execute(
            ExecuteQuotationPhase1Command(
                pdf_bytes=pdf_bytes,
                original_filename=uploaded_file_name,
                progress_callback=update_progress,
                cancel_checker=token.is_cancelled,
            )
        )
        phase1_temp_image_path = phase1_result.temp_image_minio_path

        result_payload = dict(existing_payload)
        result_payload.update(phase1_result.to_dict())

        if not phase1_result.pdm_partids:
            error_message = "PDM 结果为空，无法继续 U8 查询"
            _persistence.patch_task_fields_sync(
                task_id,
                {
                    "status": QuotationTaskStatus.failed.value,
                    "progress": 99,
                    "message": "PDM 查询未返回任何 PARTID",
                    "error": error_message,
                    "completed_at": utcnow_naive(),
                    "temp_image_minio_path": phase1_result.temp_image_minio_path,
                    "result_payload": result_payload,
                },
            )
            loop.run_until_complete(thread_tm.fail_task(task_id, error_message, "PDM 查询未返回任何 PARTID"))
            # PDM-empty is a terminal failure: clean up uploaded PDF + temp image
            # immediately rather than leaving them for retention.
            cleanup_result = cleanup_task_files_by_id_sync(
                task_id, extra_minio_paths=[phase1_temp_image_path] if phase1_temp_image_path else None
            )
            logger.info("Phase1 PDM-empty cleanup: task_id=%s result=%s", task_id, cleanup_result)
            return {"status": "error", "task_id": task_id, "error": error_message}

        converted_u8_codes, pdm_to_u8_mappings = convert_partids_to_u8_codes(
            phase1_result.pdm_partids
        )
        logger.info(
            "Phase1 PDM->U8 编码转换完成: task_id=%s, pdm_count=%s, u8_count=%s, sample=%s",
            task_id,
            len(phase1_result.pdm_partids),
            len(converted_u8_codes),
            pdm_to_u8_mappings[:8],
        )

        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.awaiting_approval.value,
                "progress": 50,
                "message": "等待用户审核 PDM 结果",
                "temp_image_minio_path": phase1_result.temp_image_minio_path,
                "result_payload": result_payload,
                "error": None,
                "awaiting_approval_at": utcnow_naive(),
            },
        )

        loop.run_until_complete(
            thread_tm.update_status(task_id, "awaiting_approval", "等待用户审核 PDM 结果")
        )
        loop.run_until_complete(
            thread_tm.update_task_progress(task_id, 50, "等待用户审核 PDM 结果")
        )
        return {"status": "awaiting_approval", "task_id": task_id}

    except QuotationPipelineCancelledError as exc:
        cleanup_result = cleanup_task_files_by_id_sync(
            task_id,
            extra_minio_paths=[phase1_temp_image_path] if phase1_temp_image_path else None,
        )
        existing_payload["cleanup"] = cleanup_result
        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.cancelled.value,
                "message": "任务已取消",
                "error": str(exc),
                "completed_at": utcnow_naive(),
                "result_payload": existing_payload,
            },
        )
        loop.run_until_complete(thread_tm.update_status(task_id, "cancelled", "任务已取消"))
        return {"status": "cancelled", "task_id": task_id}

    except Exception as exc:
        logger.error(f"报价任务 Phase1 执行失败 {task_id}: {exc}", exc_info=True)
        cleanup_result = cleanup_task_files_by_id_sync(
            task_id,
            extra_minio_paths=[phase1_temp_image_path] if phase1_temp_image_path else None,
        )
        existing_payload["cleanup"] = cleanup_result
        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.failed.value,
                "message": "Phase1 执行失败",
                "error": str(exc),
                "completed_at": utcnow_naive(),
                "result_payload": existing_payload,
            },
        )
        loop.run_until_complete(thread_tm.fail_task(task_id, str(exc), "Phase1 执行失败"))
        return {"status": "error", "task_id": task_id, "error": str(exc)}

    finally:
        _close_thread_tm(loop, thread_tm)


def process_quotation_task_phase2_background(
    token: CancellationToken, task_id: str
) -> Dict[str, Any]:
    """Phase 2 worker: call U8 BOM Inventory using previously-collected PARTIDs."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    thread_tm = TaskManager.create_thread_safe_instance()
    existing_payload: Dict[str, Any] = {}
    uploaded_file_name = ""
    # Track the xlsx path so the exception handler can reclaim it even when the
    # patch that would persist it to DB never ran (prevents orphan).
    phase2_xlsx_path: Optional[str] = None

    try:
        task_data = _persistence.get_task_payload_sync(task_id)
        if not task_data:
            return {"status": "error", "message": f"任务不存在: {task_id}"}
        existing_payload = dict(task_data.get("result_payload", {}))
        uploaded_file_name = task_data.get("uploaded_file_name", "")
        display_name = task_data.get("display_name", "")
        loop.run_until_complete(thread_tm.start_task(task_id))

        def update_progress(progress: int, message: str) -> None:
            safe_message = _clamp_message(message)
            safe_progress = max(0, min(100, progress))
            _persistence.patch_task_fields_sync(
                task_id,
                {
                    "progress": safe_progress,
                    "message": safe_message,
                    "status": QuotationTaskStatus.running.value,
                },
            )
            loop.run_until_complete(
                thread_tm.update_task_progress(task_id, safe_progress, safe_message)
            )

        if token.is_cancelled():
            raise QuotationPipelineCancelledError("任务在启动前被取消")

        approved_partids = existing_payload.get("approved_partids")
        if isinstance(approved_partids, list) and approved_partids:
            selected_partids = [str(partid) for partid in approved_partids if str(partid).strip()]
            source = "approved_partids"
        else:
            fallback = existing_payload.get("pdm_partids") or []
            selected_partids = [str(partid) for partid in fallback if str(partid).strip()]
            source = "pdm_partids(fallback)"

        logger.info(
            "Phase2 准备执行 U8 查询: task_id=%s, source=%s, selected_count=%s, sample=%s",
            task_id,
            source,
            len(selected_partids),
            selected_partids[:3],
        )

        if not selected_partids:
            logger.warning(
                "Phase2 缺少可用 PARTID: task_id=%s, payload_keys=%s",
                task_id,
                list(existing_payload.keys()),
            )
            raise QuotationPipelineError("任务缺少已批准的 PARTID 列表，无法继续 U8 查询")

        update_progress(60, f"开始 U8 BOM Inventory 查询（{len(selected_partids)} 项）")

        phase2_uc = build_execute_quotation_phase2_use_case()
        manual_types = existing_payload.get("manual_partid_types")
        code_type = existing_payload.get("code_type")
        # 取任务归属用户作为 per-user BOM 并发限流 key（dispatch 时已缓存）。
        owner_id = task_owner_registry.peek_cache(task_id) or ""
        phase2_result = phase2_uc.execute(
            ExecuteQuotationPhase2Command(
                pdm_partids=selected_partids,
                keywords_payload=existing_payload.get("keywords_payload"),
                pdm_result=existing_payload.get("pdm_result"),
                approved_partids=selected_partids,
                manual_partid_types=manual_types if isinstance(manual_types, dict) else None,
                code_type=code_type,
                progress_callback=update_progress,
                cancel_checker=token.is_cancelled,
                owner_id=owner_id or None,
            )
        )

        cleanup_result = cleanup_task_files_by_id_sync(task_id)

        u8_total = None
        u8_items = None
        if isinstance(phase2_result.u8_result, dict):
            u8_total = phase2_result.u8_result.get("total")
            u8_items = phase2_result.u8_result.get("items")
        logger.debug(
            "Phase2 U8 查询结果: task_id=%s, total=%s, items_len=%s",
            task_id,
            u8_total,
            len(u8_items) if isinstance(u8_items, list) else None,
        )

        full_u8_by_type = (
            phase2_result.u8_result_by_type
            if isinstance(phase2_result.u8_result_by_type, dict)
            else {}
        )
        failed_root_codes = phase2_result.failed_root_codes or []
        final_payload: Dict[str, Any] = {
            "keywords_payload": existing_payload.get("keywords_payload"),
            "approved_partids": selected_partids,
            "summary_selection_items": existing_payload.get("summary_selection_items"),
            "u8_result_by_type": _u8_by_type_summary(full_u8_by_type),
            "cleanup": cleanup_result,
        }
        if failed_root_codes:
            final_payload["failed_root_codes"] = failed_root_codes

        xlsx_path, xlsx_name = _upload_u8_result_by_type_xlsx_to_minio(
            task_id=task_id,
            uploaded_file_name=uploaded_file_name,
            task_name=display_name,
            u8_result_by_type=full_u8_by_type,
            summary_selection_items=existing_payload.get("summary_selection_items"),
            raw_extracted_info=existing_payload.get("raw_extracted_info"),
            keywords_payload=existing_payload.get("keywords_payload"),
            partid_quantities=_extract_partid_quantities(existing_payload),
            cancel_checker=token.is_cancelled,
        )
        if xlsx_path and xlsx_name:
            final_payload["u8_result_by_type_xlsx_minio_path"] = xlsx_path
            final_payload["u8_result_by_type_xlsx_filename"] = xlsx_name
            phase2_xlsx_path = xlsx_path

        if failed_root_codes:
            sample = ", ".join(failed_root_codes[:10])
            completed_message = (
                f"任务完成（U8 BOM Inventory 已返回，但 {len(failed_root_codes)} 个根节点"
                f"因数据库超时/锁被跳过，结果可能不完整。被跳过根: {sample}）"
            )
        else:
            completed_message = "任务完成（U8 BOM Inventory 已返回）"
        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.completed.value,
                "progress": 100,
                "message": completed_message,
                "result_payload": final_payload,
                "error": None,
                "completed_at": utcnow_naive(),
            },
        )

        loop.run_until_complete(thread_tm.complete_task(task_id, final_payload, completed_message))
        return {"status": "success", "task_id": task_id}

    except QuotationPipelineCancelledError as exc:
        cleanup_result = cleanup_task_files_by_id_sync(
            task_id,
            extra_minio_paths=[phase2_xlsx_path] if phase2_xlsx_path else None,
        )
        existing_payload["cleanup"] = cleanup_result
        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.cancelled.value,
                "message": "任务已取消",
                "error": str(exc),
                "completed_at": utcnow_naive(),
                "result_payload": existing_payload,
            },
        )
        loop.run_until_complete(thread_tm.update_status(task_id, "cancelled", "任务已取消"))
        return {"status": "cancelled", "task_id": task_id}

    except U8RootFailureBreakerError as exc:
        # 系统性故障（连续根节点失败）：把被跳过的根上报给用户，任务标记失败。
        logger.error(f"报价任务 Phase2 系统性故障 {task_id}: {exc}", exc_info=True)
        failed_sample = ", ".join(exc.failed_root_codes[:10]) or "未知"
        error_message = f"U8 数据库连续故障，任务中止。已跳过根: {failed_sample}"
        cleanup_result = cleanup_task_files_by_id_sync(task_id)
        existing_payload["cleanup"] = cleanup_result
        existing_payload["failed_root_codes"] = exc.failed_root_codes
        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.failed.value,
                "message": "U8 数据库连续故障，任务中止",
                "error": error_message,
                "completed_at": utcnow_naive(),
                "result_payload": existing_payload,
            },
        )
        loop.run_until_complete(thread_tm.fail_task(task_id, error_message, "U8 数据库连续故障"))
        return {"status": "error", "task_id": task_id, "error": error_message}

    except Exception as exc:
        logger.error(f"报价任务 Phase2 执行失败 {task_id}: {exc}", exc_info=True)
        # Reclaim MinIO artifacts (uploaded PDF, temp image, and any xlsx already
        # uploaded before the failure). Previously this branch skipped cleanup,
        # orphaning the xlsx when patch_task_fields_sync failed after upload.
        cleanup_result = cleanup_task_files_by_id_sync(
            task_id,
            extra_minio_paths=[phase2_xlsx_path] if phase2_xlsx_path else None,
        )
        existing_payload["cleanup"] = cleanup_result
        _persistence.patch_task_fields_sync(
            task_id,
            {
                "status": QuotationTaskStatus.failed.value,
                "message": "Phase2 执行失败",
                "error": str(exc),
                "completed_at": utcnow_naive(),
                "result_payload": existing_payload,
            },
        )
        loop.run_until_complete(thread_tm.fail_task(task_id, str(exc), "Phase2 执行失败"))
        return {"status": "error", "task_id": task_id, "error": str(exc)}

    finally:
        _close_thread_tm(loop, thread_tm)


async def _dispatch_quotation_phase2_async(task_id: str, owner_id: str) -> None:
    """Async implementation: submit Phase 2 worker to executor."""
    try:
        executor_manager.forget_task(task_id)
        future = executor_manager.submit_task(
            task_id,
            process_quotation_task_phase2_background,
            task_id,
        )
        task_owner_registry.cache(task_id, owner_id)
        future.add_done_callback(lambda _: dispatch_quotation_queue_global())
    except Exception as exc:
        logger.error(f"Phase2 提交执行器失败 {task_id}: {exc}", exc_info=True)
        # Directly await – we are in an async context.  The sync bridge
        # helpers would deadlock if called from the event loop thread.
        try:
            await _persistence.mark_task_failed(task_id, f"执行器提交失败: {exc}")
        except Exception as db_exc:
            logger.error(f"Phase2 标记任务失败状态异常 {task_id}: {db_exc}")
        try:
            await task_manager.fail_task(task_id, str(exc), "Phase2 提交执行器失败")
        except Exception as tm_exc:
            logger.error(f"Phase2 TaskManager 标记失败异常 {task_id}: {tm_exc}")


def dispatch_quotation_phase2(task_id: str, owner_id: str) -> None:
    """Submit the Phase 2 worker. Must be called after the DB status flip.

    The executor retains Phase 1's Future under the same task_id for history,
    so we evict it first to allow resubmitting with the same business id.
    Dispatch is always scheduled on the main event loop to avoid asyncpg
    connection-pool cross-loop reuse.
    """
    _schedule_dispatch_coro(
        _dispatch_quotation_phase2_async(task_id, owner_id),
        log_label="报价 Phase2 调度",
        owner_id=owner_id,
    )

