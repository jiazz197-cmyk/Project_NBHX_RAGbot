"""任务进度 WebSocket：按 task 订阅推送；连接管理在 app.core.websocket_task_manager。"""
import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, status, Depends

from app.core.logging import get_logger
from app.core.security import require_roles
from app.core.task_owner_registry import resolve_task_owner_id
from app.core.websocket_task_manager import (
    WebSocketTaskObserver,
    decode_ws_bearer_user_id,
    load_user_for_websocket,
    ws_manager,
)
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER, ROLE_ADMIN

logger = get_logger("websocket_notifier")
ws_diag_logger = get_logger("diag.ws")

router = APIRouter()

# Re-export for main.py / tests that imported from this module
__all__ = ["router", "WebSocketTaskObserver", "ws_manager"]

_WS_AUTH_TIMEOUT = 5.0


def _extract_ws_user_agent(websocket: WebSocket) -> str:
    return str(websocket.headers.get("user-agent", "")).strip()


def _parse_auth_message(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="无效鉴权消息")
    token = str(payload.get("token", "")).strip()
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="缺少认证令牌")
    return token


@router.websocket("/ws/{task_id}")
async def websocket_task_endpoint(websocket: WebSocket, task_id: str) -> None:
    started_at = time.perf_counter()
    client_ip = (websocket.client.host if websocket.client else "") or "unknown"
    user_agent = _extract_ws_user_agent(websocket)
    ws_diag_logger.info(
        "[ws_diag] ws_connect_requested: task_id=%s client_ip=%s user_agent=%s",
        task_id,
        client_ip,
        user_agent,
    )

    await websocket.accept()

    try:
        raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=_WS_AUTH_TIMEOUT)
    except asyncio.TimeoutError:
        ws_diag_logger.warning(
            "[ws_diag] ws_connect_rejected: task_id=%s client_ip=%s reason=auth_timeout elapsed_ms=%.2f",
            task_id,
            client_ip,
            (time.perf_counter() - started_at) * 1000,
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="鉴权超时")
        return

    try:
        token = _parse_auth_message(raw_auth)
    except WebSocketException as exc:
        ws_diag_logger.warning(
            "[ws_diag] ws_connect_rejected: task_id=%s client_ip=%s reason=invalid_auth close_code=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            exc.code,
            (time.perf_counter() - started_at) * 1000,
        )
        await websocket.close(code=exc.code, reason=exc.reason or "认证失败")
        return

    try:
        user_id = decode_ws_bearer_user_id(token)
    except WebSocketException as exc:
        ws_diag_logger.warning(
            "[ws_diag] ws_connect_rejected: task_id=%s client_ip=%s reason=invalid_token close_code=%s close_reason=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            exc.code,
            exc.reason,
            (time.perf_counter() - started_at) * 1000,
        )
        await websocket.close(code=exc.code, reason=exc.reason or "认证失败")
        return

    owner_id = await resolve_task_owner_id(task_id)
    if not owner_id:
        ws_diag_logger.warning(
            "[ws_diag] ws_connect_rejected: task_id=%s client_ip=%s user_id=%s reason=missing_owner elapsed_ms=%.2f",
            task_id,
            client_ip,
            user_id,
            (time.perf_counter() - started_at) * 1000,
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="任务缺少归属信息，禁止订阅")
        return

    ws_user = await load_user_for_websocket(user_id)
    is_admin_like = bool(ws_user and ws_user.role in (ROLE_ADMIN, ROLE_SUPERUSER))
    if owner_id != user_id and not is_admin_like:
        ws_diag_logger.warning(
            "[ws_diag] ws_connect_rejected: task_id=%s client_ip=%s user_id=%s owner_id=%s reason=forbidden elapsed_ms=%.2f",
            task_id,
            client_ip,
            user_id,
            owner_id,
            (time.perf_counter() - started_at) * 1000,
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="无权订阅该任务")
        return

    try:
        ws_manager.register(websocket, task_id, client_ip, user_id=user_id, is_admin=is_admin_like)
        ws_diag_logger.info(
            "[ws_diag] ws_connect_accepted: task_id=%s client_ip=%s user_id=%s owner_id=%s task_connections=%s total_connections=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            user_id,
            owner_id,
            ws_manager.get_connection_count(task_id),
            ws_manager.get_connection_count(),
            (time.perf_counter() - started_at) * 1000,
        )
    except WebSocketException as exc:
        ws_diag_logger.warning(
            "[ws_diag] ws_connect_rejected: task_id=%s client_ip=%s user_id=%s reason=manager_rejected close_code=%s close_reason=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            user_id,
            exc.code,
            exc.reason,
            (time.perf_counter() - started_at) * 1000,
        )
        await websocket.close(code=exc.code, reason=exc.reason or "连接被拒绝")
        return

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "connection_established",
                    "task_id": task_id,
                    "message": f"已订阅任务 {task_id} 的实时更新",
                },
                ensure_ascii=False,
            )
        )
        ws_diag_logger.info(
            "[ws_diag] ws_connection_established_sent: task_id=%s client_ip=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            (time.perf_counter() - started_at) * 1000,
        )
        while True:
            if not ws_manager.allow_message(client_ip):
                ws_diag_logger.warning(
                    "[ws_diag] ws_disconnect_observed: task_id=%s client_ip=%s reason=message_rate_limited elapsed_ms=%.2f",
                    task_id,
                    client_ip,
                    (time.perf_counter() - started_at) * 1000,
                )
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="消息频率超过限制",
                )
                break
            data = await websocket.receive_text()
            logger.debug("Received client message: %s", data)
    except WebSocketDisconnect as exc:
        ws_manager.disconnect(websocket, task_id)
        ws_diag_logger.info(
            "[ws_diag] ws_disconnect_observed: task_id=%s client_ip=%s code=%s task_connections=%s total_connections=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            exc.code,
            ws_manager.get_connection_count(task_id),
            ws_manager.get_connection_count(),
            (time.perf_counter() - started_at) * 1000,
        )
        logger.info("[event] Client disconnected: task_id=%s", task_id)
    except Exception as e:
        ws_diag_logger.error(
            "[ws_diag] ws_exception_observed: task_id=%s client_ip=%s error=%s elapsed_ms=%.2f",
            task_id,
            client_ip,
            e,
            (time.perf_counter() - started_at) * 1000,
            exc_info=True,
        )
        logger.error("WebSocket error: %s", e, exc_info=True)
        ws_manager.disconnect(websocket, task_id)


@router.get("/ws/stats")
async def get_websocket_stats(
    _: CurrentUserPort = Depends(require_roles(ROLE_SUPERUSER)),
):
    return {
        "total_connections": ws_manager.get_connection_count(),
        "tasks_subscribed": len(ws_manager.active_connections),
        "connections_by_task": {
            tid: ws_manager.get_connection_count(tid) for tid in ws_manager.active_connections.keys()
        },
    }
