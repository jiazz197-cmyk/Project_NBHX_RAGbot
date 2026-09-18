"""FastAPI 入口（.dsh/ragchain-interfaces.md §14）。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import settings
from .deps import AppDeps, build_deps, close_deps
from .errors import AuthError, ChainError
from .schemas import ChatMessageRequest, VALID_SEARCH_MODES
from .security import verify_bearer
from .sse import sse_frame



@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.deps = build_deps(settings)
    try:
        yield
    finally:
        await close_deps(app.state.deps)


# CLI 启动 uvicorn 时应用 logger 默认继承 root=WARNING，INFO 会被吞掉；
# 这里按 RAGCHAIN_LOG_LEVEL 显式配置 root，保证核心链可观测。
logging.basicConfig(
    level=getattr(logging, str(settings.RAGCHAIN_LOG_LEVEL).upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="NBHX RAGChain", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------- helpers
def _load_run_chat():
    """惰性导入 T-C 的 orchestrator，避免其文件未就绪时 import server 失败。"""
    from .orchestrator import run_chat

    return run_chat


def _get_deps() -> AppDeps:
    deps = getattr(app.state, "deps", None)
    if deps is None:  # 未走 lifespan（如 ASGI 直连单测）时的兜底装配
        deps = build_deps(settings)
        app.state.deps = deps
    return deps


async def _with_heartbeat(source, heartbeat_sec: float):
    """包装核心链 SSE 流，空闲超过 ``heartbeat_sec`` 时发送 ping。

    使用后台 anext task + ``asyncio.wait``，超时只发心跳，不 cancel 核心链的
    当前 await；客户端断连时 finally 取消 source 并触发 orchestrator 清理。
    """
    iterator = source.__aiter__()
    pending: asyncio.Future | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_sec)
            if pending not in done:
                yield sse_frame("ping", {"event": "ping"})
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                pending = None
                break
            else:
                pending = None
                yield frame
    except asyncio.CancelledError:
        # 客户端断连属于正常路径，不打印错误日志。
        raise
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(BaseException):
                await pending
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(BaseException):
                await aclose()


# --------------------------------------------------------------------- routes
@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.exception_handler(ChainError)
async def chain_error_handler(_request: Request, exc: ChainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=exc.to_error_body())


@app.post("/api/v1/chat-messages")
async def chat_messages(
    request: ChatMessageRequest,
    authorization: str | None = Header(default=None),
):
    try:
        auth = verify_bearer(authorization)
    except AuthError as exc:
        return JSONResponse(status_code=exc.status, content=exc.to_error_body())

    if request.search_mode not in VALID_SEARCH_MODES:
        return JSONResponse(
            status_code=422,
            content={"message": "search_mode 无效", "error_code": "VALIDATION_ERROR"},
        )

    deps = _get_deps()
    run_chat = _load_run_chat()
    source = run_chat(request, auth, deps)
    return StreamingResponse(
        _with_heartbeat(source, settings.SSE_HEARTBEAT_SEC),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v1/chat-messages/{task_id}/stop")
async def stop_chat_message(
    task_id: str,
    authorization: str | None = Header(default=None),
):
    try:
        auth = verify_bearer(authorization)
    except AuthError as exc:
        return JSONResponse(status_code=exc.status, content=exc.to_error_body())

    deps = _get_deps()
    entry = deps.registry.request_stop(task_id, auth.user_id)
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={
                "message": "任务不存在、已结束或不属于当前用户",
                "error_code": "NOT_FOUND",
            },
        )
    return {"result": "success"}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "app.server:app",
        host=settings.RAGCHAIN_HOST,
        port=settings.RAGCHAIN_PORT,
        log_level=settings.RAGCHAIN_LOG_LEVEL.lower(),
    )
