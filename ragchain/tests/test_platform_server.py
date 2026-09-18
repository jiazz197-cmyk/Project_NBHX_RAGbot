"""FastAPI 路由测试：/healthz、SSE 校验、心跳、stop 注册表语义。"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app import server
from app.sse import sse_frame

pytestmark = pytest.mark.asyncio


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://testserver",
    )


async def test_healthz():
    async with _client() as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_chat_requires_valid_bearer(make_token):
    async with _client() as client:
        no_auth = await client.post("/api/v1/chat-messages", json={"query": "你好"})
        bad_auth = await client.post(
            "/api/v1/chat-messages",
            json={"query": "你好"},
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    for resp in (no_auth, bad_auth):
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"
        assert "message" in resp.json()


async def test_chat_rejects_invalid_search_mode(make_token):
    token = make_token()
    async with _client() as client:
        resp = await client.post(
            "/api/v1/chat-messages",
            json={"query": "你好", "search_mode": "随便搜"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert resp.json() == {"message": "search_mode 无效", "error_code": "VALIDATION_ERROR"}


async def test_chat_streams_frames_and_headers(make_token, monkeypatch):
    calls = {}

    async def fake_run_chat(request, auth, deps):
        calls["query"] = request.query
        calls["user_id"] = auth.user_id
        yield sse_frame(
            "message",
            {"event": "message", "task_id": "t-1", "conversation_id": "c-1", "content": "你好"},
        )
        yield sse_frame(
            "message_end",
            {"event": "message_end", "task_id": "t-1", "conversation_id": "c-1", "content": ""},
        )

    monkeypatch.setattr(server, "_load_run_chat", lambda: fake_run_chat)
    token = make_token(sub="user-stream")
    async with _client() as client:
        resp = await client.post(
            "/api/v1/chat-messages",
            json={"query": "去年售后费用", "search_mode": "本地检索"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"
    text = resp.text
    assert "event: message\n" in text
    assert '"content": "你好"' in text
    assert text.index("event: message\n") < text.index("event: message_end\n")
    assert calls == {"query": "去年售后费用", "user_id": "user-stream"}


async def test_chat_heartbeat_does_not_cancel_core_stream(make_token, monkeypatch):
    monkeypatch.setattr(server.settings, "SSE_HEARTBEAT_SEC", 0.03)

    async def slow_run_chat(request, auth, deps):
        yield sse_frame(
            "message",
            {"event": "message", "task_id": "t-hb", "conversation_id": "c-hb", "content": "第一段"},
        )
        await asyncio.sleep(0.25)
        yield sse_frame(
            "message_end",
            {"event": "message_end", "task_id": "t-hb", "conversation_id": "c-hb", "content": ""},
        )

    monkeypatch.setattr(server, "_load_run_chat", lambda: slow_run_chat)
    token = make_token()
    async with _client() as client:
        resp = await client.post(
            "/api/v1/chat-messages",
            json={"query": "q"},
            headers={"Authorization": f"Bearer {token}"},
        )

    text = resp.text
    assert "event: ping\n" in text
    assert text.index("event: message\n") < text.index("event: ping\n")
    assert text.index("event: ping\n") < text.index("event: message_end\n")


async def test_stop_missing_task_returns_404(make_token):
    token = make_token()
    async with _client() as client:
        resp = await client.post(
            f"/api/v1/chat-messages/{uuid.uuid4()}/stop",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
    assert resp.json() == {
        "message": "任务不存在、已结束或不属于当前用户",
        "error_code": "NOT_FOUND",
    }


async def test_stop_success_idempotent_and_wrong_user(make_token):
    deps = server._get_deps()
    registry = deps.registry
    entry = registry.register("user-stop", "c-stop")
    token = make_token(sub="user-stop")
    other_token = make_token(sub="user-other")

    async with _client() as client:
        wrong = await client.post(
            f"/api/v1/chat-messages/{entry.task_id}/stop",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        first = await client.post(
            f"/api/v1/chat-messages/{entry.task_id}/stop",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert entry.cancelled is True
        registry.finish(entry.task_id, "success")
        second = await client.post(
            f"/api/v1/chat-messages/{entry.task_id}/stop",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert wrong.status_code == 404
    assert first.status_code == 200
    assert first.json() == {"result": "success"}
    assert second.status_code == 200
    assert second.json() == {"result": "success"}


async def test_stop_requires_auth():
    async with _client() as client:
        resp = await client.post(f"/api/v1/chat-messages/{uuid.uuid4()}/stop")
    assert resp.status_code == 401
    assert resp.json()["error_code"] == "AUTHENTICATION_ERROR"
