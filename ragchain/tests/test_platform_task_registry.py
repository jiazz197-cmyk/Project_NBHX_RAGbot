"""TaskRegistry 取消 / 幂等 / 越权 / TTL 过期测试。"""

from __future__ import annotations

import asyncio
import time

from app.task_registry import TaskEntry, TaskRegistry


def test_register_get_and_update():
    registry = TaskRegistry(ttl_sec=60)
    entry = registry.register("u-1", "c-1")
    assert isinstance(entry, TaskEntry)
    assert entry.user_id == "u-1"
    assert entry.conversation_id == "c-1"
    assert entry.result == "running"
    assert entry.finished is False
    assert entry.cancelled is False
    assert registry.get(entry.task_id) is entry

    registry.update_conversation(entry.task_id, "c-2")
    assert entry.conversation_id == "c-2"
    registry.update_conversation("missing", "c-x")  # 不抛异常


def test_register_default_conversation_and_unique_ids():
    registry = TaskRegistry(ttl_sec=60)
    a = registry.register("u-1")
    b = registry.register("u-1")
    assert a.conversation_id == ""
    assert a.task_id != b.task_id


def test_request_stop_sets_cancel_event_and_is_idempotent():
    registry = TaskRegistry(ttl_sec=60)
    entry = registry.register("u-1", "c-1")
    first = registry.request_stop(entry.task_id, "u-1")
    assert first is entry
    assert entry.cancel_event.is_set()
    assert entry.cancelled is True
    second = registry.request_stop(entry.task_id, "u-1")
    assert second is entry
    assert entry.cancel_event.is_set()


def test_request_stop_wrong_user_returns_none():
    registry = TaskRegistry(ttl_sec=60)
    entry = registry.register("u-1", "c-1")
    assert registry.request_stop(entry.task_id, "u-2") is None
    assert entry.cancel_event.is_set() is False


def test_request_stop_missing_task_returns_none():
    registry = TaskRegistry(ttl_sec=60)
    assert registry.request_stop("nope", "u-1") is None


def test_request_stop_finished_task_still_success():
    registry = TaskRegistry(ttl_sec=60)
    entry = registry.register("u-1", "c-1")
    registry.finish(entry.task_id, "success")
    assert entry.finished is True
    assert entry.result == "success"
    assert registry.request_stop(entry.task_id, "u-1") is entry


def test_finish_missing_task_is_noop():
    registry = TaskRegistry(ttl_sec=60)
    registry.finish("missing", "failed")  # 不抛异常


def test_ttl_expiry_lazy_cleanup():
    registry = TaskRegistry(ttl_sec=0.05)
    entry = registry.register("u-1", "c-1")
    assert registry.get(entry.task_id) is entry
    time.sleep(0.1)
    assert registry.get(entry.task_id) is None
    assert registry.request_stop(entry.task_id, "u-1") is None


def test_ttl_zero_immediate_expiry():
    registry = TaskRegistry(ttl_sec=0)
    entry = registry.register("u-1", "c-1")
    assert registry.get(entry.task_id) is None


async def test_cancel_event_can_be_awaited():
    registry = TaskRegistry(ttl_sec=60)
    entry = registry.register("u-1", "c-1")

    async def waiter() -> str:
        await entry.cancel_event.wait()
        return "cancelled"

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    registry.request_stop(entry.task_id, "u-1")
    assert await asyncio.wait_for(task, timeout=1) == "cancelled"
