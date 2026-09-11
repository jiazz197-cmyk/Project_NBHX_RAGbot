"""按用户 WS 连接上限单元测试（B3：NAT 安全的逐用户分档）。"""
from __future__ import annotations

import pytest
from fastapi import WebSocketException

from app.core.config import settings
from app.core.websocket_task_manager import WebSocketConnectionManager


class _StubState:
    pass


class _StubWS:
    """最小 WebSocket 桩：register/disconnect 只读写 .state。

    register 把 websocket 放进 set（active_connections[task_id].add），故桩必须可哈希。
    SimpleNamespace 因定义了 __eq__ 而不可哈希，这里用默认身份哈希的普通类。
    """

    def __init__(self) -> None:
        self.state = _StubState()


def _ws() -> _StubWS:
    return _StubWS()


def test_register_within_user_cap_succeeds():
    mgr = WebSocketConnectionManager()
    cap = settings.WS_MAX_CONNECTIONS_PER_USER
    for i in range(cap):
        mgr.register(_ws(), f"task-{i}", "1.2.3.4", user_id="user-A", is_admin=False)
    assert mgr.user_connections["user-A"] == cap


def test_register_exceeds_user_cap_raises():
    mgr = WebSocketConnectionManager()
    cap = settings.WS_MAX_CONNECTIONS_PER_USER
    for i in range(cap):
        mgr.register(_ws(), f"task-{i}", "1.2.3.4", user_id="user-A", is_admin=False)
    with pytest.raises(WebSocketException):
        mgr.register(_ws(), "task-overflow", "1.2.3.4", user_id="user-A", is_admin=False)


def test_admin_has_higher_cap():
    mgr = WebSocketConnectionManager()
    admin_cap = settings.WS_MAX_CONNECTIONS_PER_USER_ADMIN
    assert admin_cap > settings.WS_MAX_CONNECTIONS_PER_USER
    for i in range(admin_cap):
        mgr.register(_ws(), f"task-{i}", "1.2.3.4", user_id="admin-A", is_admin=True)
    with pytest.raises(WebSocketException):
        mgr.register(_ws(), "task-overflow", "1.2.3.4", user_id="admin-A", is_admin=True)


def test_per_user_cap_isolated_across_users_on_same_ip():
    """NAT 安全：同一 IP 下不同用户互不挤占连接配额。"""
    mgr = WebSocketConnectionManager()
    cap = settings.WS_MAX_CONNECTIONS_PER_USER
    for i in range(cap):
        mgr.register(_ws(), f"task-{i}", "1.2.3.4", user_id="user-A", is_admin=False)
    # 同 IP 的另一用户仍可正常注册（不受 user-A 挤占影响）
    mgr.register(_ws(), "task-b", "1.2.3.4", user_id="user-B", is_admin=False)
    assert mgr.user_connections["user-B"] == 1


def test_disconnect_decrements_user_count():
    mgr = WebSocketConnectionManager()
    ws = _ws()
    mgr.register(ws, "task-1", "1.2.3.4", user_id="user-A", is_admin=False)
    assert mgr.user_connections["user-A"] == 1
    mgr.disconnect(ws, "task-1")
    assert "user-A" not in mgr.user_connections


def test_register_without_user_id_skips_user_cap():
    """旧调用方（user_id 空）仅计 IP，不触发按用户上限。"""
    mgr = WebSocketConnectionManager()
    cap = settings.WS_MAX_CONNECTIONS_PER_USER
    # 远超 per-user cap，但 user_id 为空 → 不应抛
    for i in range(cap + 5):
        mgr.register(_ws(), f"task-{i}", "1.2.3.4", user_id="", is_admin=False)
    assert mgr.user_connections == {}
