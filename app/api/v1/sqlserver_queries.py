"""
SQL Server query routes.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from fastapi import APIRouter, Depends

from app.adapters.sqlserver_queries import PdmBomQueryAdapter, PdmMatchQueryAdapter, U8BomInventoryQueryAdapter
from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.security import get_current_user_detached
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.sqlserver_queries import U8RootFailureBreakerError
from app.ports.dto.sqlserver_queries import (
    PdmBomCommand,
    PdmMatchCommand,
    SqlserverQueryResultDTO,
    U8BomInventoryCommand,
)
from app.adapters.web.sqlserver import PdmBomRequest, QueryResponse, U8BomInventoryRequest
from app.usecases.sqlserver_queries.run_queries import RunPdmBomQueryUseCase, RunPdmMatchQueryUseCase, RunU8BomInventoryQueryUseCase

router = APIRouter()

_u8 = U8BomInventoryQueryAdapter()
_pdm = PdmBomQueryAdapter()
_pdm_match = PdmMatchQueryAdapter()
# 同步查询 API 的执行器：大小对齐 EXECUTOR_MAX_WORKERS（已由 config validator
# 保证 ≥ U8_BOM_MAX_CONCURRENT_TASKS），否则多人同时点"查 BOM"时任务会卡在这个
# 执行器队列里"看戏"，根本到不了 per-user / 全局 BOM 信号量。
# 注意：SQLSERVER_QUERY_MAX_WORKERS 不用在这里--它只管 PDM matcher 的查询内并行。
_sqlserver_query_executor = ThreadPoolExecutor(
    max_workers=settings.EXECUTOR_MAX_WORKERS,
    thread_name_prefix="sqlserver_query_",
)


async def _run_sqlserver_query(func, *args, **kwargs) -> SqlserverQueryResultDTO:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _sqlserver_query_executor, partial(func, *args, **kwargs)
    )


def _to_response(result: SqlserverQueryResultDTO) -> QueryResponse:
    """Map the port-level result DTO to the web-layer response model."""
    return QueryResponse(
        total=result.total,
        items=result.items,
        components=result.components,
        failed_root_codes=result.failed_root_codes,
        partial=result.partial,
    )


def shutdown_sqlserver_query_executor() -> None:
    """Best-effort shutdown of the module-level SQLServer query thread pool.

    Called from the application lifespan shutdown so pending query workers are
    released instead of lingering until interpreter exit.
    """
    try:
        _sqlserver_query_executor.shutdown(wait=False)
    except Exception:
        pass


@router.post("/u8/bom-inventory", response_model=QueryResponse, summary="U8 BOM + Inventory 递归查询")
async def query_u8_bom_inventory(
    payload: U8BomInventoryRequest,
    _current_user: CurrentUserPort = Depends(get_current_user_detached),
) -> QueryResponse:
    """按 parent_inv_codes 递归展开 U8 BOM，并关联 Inventory 成本信息。需登录。"""
    cmd = U8BomInventoryCommand(
        parent_inv_codes=payload.parent_inv_codes,
        max_depth=payload.max_depth,
    )
    # 传入调用者 id 作为 per-user 并发限流的 key（单人最多 2 个并发 BOM 查询）。
    try:
        return _to_response(
            await _run_sqlserver_query(
                RunU8BomInventoryQueryUseCase(_u8).execute, cmd, user_key=_current_user.id
            )
        )
    except U8RootFailureBreakerError as exc:
        failed_sample = ", ".join(exc.failed_root_codes[:10]) or "未知"
        raise ExternalServiceError(
            "U8 SQLServer",
            f"U8 数据库连续故障，查询中止。已跳过根: {failed_sample}",
        ) from exc


@router.post("/pdm/bom/old", response_model=QueryResponse, summary="PDM BOM_016 条件查询（旧版）")
async def query_pdm_bom_old(
    payload: PdmBomRequest,
    _current_user: CurrentUserPort = Depends(get_current_user_detached),
) -> QueryResponse:
    """查询 pdm78.BOM_016，支持单组关键词 AND 或多组关键词分批查询。需登录。"""
    cmd = PdmBomCommand(keywords=payload.keywords)
    return _to_response(await _run_sqlserver_query(RunPdmBomQueryUseCase(_pdm).execute, cmd))


@router.post("/pdm/bom", response_model=QueryResponse, summary="PDM 部件匹配（四路召回+打分）")
async def query_pdm_bom(
    payload: PdmBomRequest,
    _current_user: CurrentUserPort = Depends(get_current_user_detached),
) -> QueryResponse:
    """PDM 部件匹配 - 四路召回 + 多维打分 + 分层输出。需登录。"""
    cmd = PdmMatchCommand(keywords=payload.keywords)
    return _to_response(await _run_sqlserver_query(RunPdmMatchQueryUseCase(_pdm_match).execute, cmd))
