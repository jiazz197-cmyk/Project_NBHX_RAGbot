"""Regression tests for PymssqlConnectionPool keepalive strategy.

Covers recycle-by-age, optional pre-ping, build-failure counter/slot rollback,
and close() -- the behavior added to align the hand-written SQL Server pool
with PG's pool_pre_ping + pool_recycle.

No real ERP / pymssql needed: ``_build_pymssql_conn`` is monkeypatched to a fake
factory, and the pool's monotonic clock is replaced so age-recycle is tested
without waiting ``SQLSERVER_POOL_RECYCLE_SEC`` seconds. ``client.time`` is
swapped (not the global ``time.monotonic``) so ``threading``'s semaphore timeout
keeps using the real clock.
"""
from __future__ import annotations

import types
from typing import Any

import pytest

from app.adapters.sqlserver import client as sqlclient


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def execute(self, sql: str, params: Any = None) -> None:
        if (sql or "").strip().upper().startswith("SELECT 1") and self._conn.broken:
            raise RuntimeError("simulated stale connection")

    def fetchall(self) -> list:
        return []


class _FakeConn:
    """pymssql-shaped fake: cursor() ctx mgr + close()."""

    _next = 0

    def __init__(self) -> None:
        _FakeConn._next += 1
        self.nid = _FakeConn._next
        self.closed = False
        self.broken = False  # SELECT 1 (pre-ping) fails when True

    def cursor(self, as_dict: bool = True) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def _install_fake_factory(monkeypatch, fail_on_call: int = 0) -> dict:
    """Monkeypatch ``_build_pymssql_conn`` + controllable clock; return state.

    ``fail_on_call`` is the 1-based index of the build call that should raise.
    0 (default) = never fail. This lets a test succeed on the first checkout
    and fail on a later rebuild (e.g. recycle / pre-ping), matching the
    ``_materialize_new_conn`` failure path under test.
    """
    state = {"fail_on_call": fail_on_call, "calls": 0}

    def fake_build(conf: dict) -> _FakeConn:
        state["calls"] += 1
        if state["fail_on_call"] and state["calls"] == state["fail_on_call"]:
            raise RuntimeError("simulated build failure")
        return _FakeConn()

    monkeypatch.setattr(sqlclient, "_build_pymssql_conn", fake_build)

    clock = [0.0]
    # Replace the `time` name in client's namespace only (threading keeps real clock).
    monkeypatch.setattr(sqlclient, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    state["clock"] = clock
    return state


def _make_pool(recycle: int = 1800, pre_ping: bool = False, max_size: int = 4):
    pool = sqlclient.PymssqlConnectionPool(
        {"server": "s", "database": "d", "username": "u", "password": "p"},
        max_size=max_size,
    )
    pool._recycle_sec = recycle
    pool._pre_ping = pre_ping
    return pool


def test_recycle_by_age_replaces_stale_connection(monkeypatch):
    st = _install_fake_factory(monkeypatch)
    pool = _make_pool(recycle=10)

    c1 = pool.acquire(timeout=0.5)
    pool.release(c1)
    assert pool.created_count == 1

    st["clock"][0] += 20  # age past recycle threshold
    c2 = pool.acquire(timeout=0.5)

    assert c2 is not c1
    assert c1.closed, "old connection must be closed on recycle"
    assert pool.created_count == 1, "recycle is net-zero on created_count"
    assert id(c1) not in pool._born and id(c2) in pool._born
    pool.release(c2)


def test_pre_ping_failure_rebuilds_transparently(monkeypatch):
    _install_fake_factory(monkeypatch)
    pool = _make_pool(pre_ping=True)

    c1 = pool.acquire(timeout=0.5)
    pool.release(c1)
    c1.broken = True  # next checkout's SELECT 1 will fail

    c2 = pool.acquire(timeout=0.5)

    assert c2 is not c1
    assert c1.closed, "stale connection must be closed after pre-ping failure"
    assert pool.created_count == 1, "pre-ping rebuild is net-zero on created_count"
    pool.release(c2)


def test_pre_ping_success_reuses_connection(monkeypatch):
    _install_fake_factory(monkeypatch)
    pool = _make_pool(pre_ping=True)

    c1 = pool.acquire(timeout=0.5)
    pool.release(c1)
    c2 = pool.acquire(timeout=0.5)  # healthy -> SELECT 1 ok -> reuse

    assert c2 is c1
    assert pool.created_count == 1
    pool.release(c2)


def test_build_failure_on_recycle_rolls_back_counter_and_slot(monkeypatch):
    st = _install_fake_factory(monkeypatch, fail_on_call=2)
    pool = _make_pool(recycle=10)

    c1 = pool.acquire(timeout=0.5)
    pool.release(c1)
    st["clock"][0] += 20  # age out

    with pytest.raises(RuntimeError):
        pool.acquire(timeout=0.5)

    assert c1.closed, "old connection closed even though rebuild failed"
    assert pool.created_count == 0, "recycle build-failure is net -1"
    # slot recovered -> next acquire works
    c2 = pool.acquire(timeout=0.5)
    assert pool.created_count == 1
    pool.release(c2)


def test_discard_drops_connection_and_frees_slot(monkeypatch):
    _install_fake_factory(monkeypatch)
    pool = _make_pool(max_size=2)

    c1 = pool.acquire(timeout=0.5)
    assert pool.checked_out == 1
    pool.discard(c1)

    assert c1.closed
    assert pool.created_count == 0
    assert id(c1) not in pool._born
    assert pool.checked_out == 0
    # slot freed -> can acquire up to max_size again
    a = pool.acquire(timeout=0.5)
    b = pool.acquire(timeout=0.5)
    assert pool.checked_out == 2
    pool.release(a)
    pool.release(b)


def test_close_closes_all_idle_and_is_idempotent(monkeypatch):
    _install_fake_factory(monkeypatch)
    pool = _make_pool(max_size=4)

    a = pool.acquire(timeout=0.5)
    b = pool.acquire(timeout=0.5)
    pool.release(a)
    pool.release(b)
    assert pool.idle_count == 2

    pool.close()  # must not over-release the BoundedSemaphore
    assert a.closed and b.closed, "all idle connections closed"
    assert pool.created_count == 0
    assert len(pool._born) == 0

    with pytest.raises(RuntimeError):
        pool.acquire(timeout=0.5)  # pool closed

    pool.close()  # idempotent, no error


def test_max_size_enforced_via_pool_timeout(monkeypatch):
    _install_fake_factory(monkeypatch)
    pool = _make_pool(max_size=2)

    pool.acquire(timeout=0.5)
    pool.acquire(timeout=0.5)
    with pytest.raises(sqlclient.PoolTimeout):
        pool.acquire(timeout=0.2)
