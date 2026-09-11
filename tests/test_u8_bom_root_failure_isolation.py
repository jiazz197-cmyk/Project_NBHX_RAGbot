"""U8 BOM 故障隔离 + 熔断 + 失败根上报单元测试。

覆盖 commit c6c6596f 的行为：
- 可恢复 DB 故障（超时 20003 / 锁 20047 / 死锁 1205）→ 跳过该根、部分完成、failed_root_codes 可见。
- 连续 _MAX_CONSECUTIVE_ROOT_FAILURES 个根失败 → 抛 U8RootFailureBreakerError（携带 failed_root_codes）。
- QueryCancelledError → 原样上抛（不被故障隔离吞掉）。
- 非可恢复异常（代码缺陷如 KeyError）→ 原样上抛（不被静默跳过）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
from contextlib import contextmanager

import pytest

from app.core.config import settings
from app.adapters.sqlserver import u8_bom
from app.adapters.sqlserver.exceptions import (
    QueryCancelledError,
    U8RootFailureBreakerError,
)


class _FakeTimeout(Exception):
    """模拟 pymssql OperationalError(20003) 超时。"""

    def __init__(self, code: int = 20003) -> None:
        self.args = (code, b"Adaptive Server connection timed out")


class _FakeLockError(Exception):
    """模拟 pymssql OperationalError(20047) 锁。"""

    def __init__(self) -> None:
        self.args = (20047, b"Lock request time out period exceeded")


class _FakeDeadlock(Exception):
    """模拟 pymssql 死锁 1205。"""

    def __init__(self) -> None:
        self.args = (1205, b"Transaction was deadlocked on lock resources")


class _FakePool:
    """桩掉连接池，提供日志所需的 created_count 属性。"""

    created_count = 0


@contextmanager
def _fake_pooled_client(pool, *, cancel_checker=None):
    """桩掉 pooled_client context manager，yield 一个哨兵 client。"""
    yield object()


def _patch_infra(monkeypatch, walk_fn: Callable[..., Any]) -> None:
    """桩掉所有 DB / 并发槽 / 连接池依赖，仅保留 _walk_one_root 行为。

    强制串行路径（parallel_workers=1）避免 ThreadPoolExecutor 时序不确定性。
    """
    monkeypatch.setattr(settings, "U8_BOM_PARALLEL_WORKERS", 1)
    monkeypatch.setattr(u8_bom, "_acquire_per_user_slot", lambda *a, **k: True)
    monkeypatch.setattr(u8_bom, "_release_per_user_slot", lambda *a, **k: None)
    monkeypatch.setattr(u8_bom, "_acquire_bom_slot", lambda *a, **k: None)
    monkeypatch.setattr(u8_bom, "_release_bom_slot", lambda *a, **k: None)
    monkeypatch.setattr(u8_bom, "_get_shared_pool", lambda: _FakePool())
    monkeypatch.setattr(u8_bom, "pooled_client", _fake_pooled_client)
    monkeypatch.setattr(
        u8_bom, "_fetch_root_inv_names", lambda client, codes, cc: {c: c for c in codes}
    )
    monkeypatch.setattr(u8_bom, "_walk_one_root", walk_fn)
    monkeypatch.setattr(u8_bom, "_fill_inventory_only_rows", lambda *a, **k: None)
    monkeypatch.setattr(u8_bom, "_supplement_missing_prices", lambda *a, **k: None)


def _walk_ok(root_code: str) -> Tuple[List[Dict[str, Any]], bool]:
    return ([{"root": root_code, "part": f"{root_code}-1"}], False)


def test_skip_recoverable_timeout_root_partial_completion(monkeypatch):
    """单根超时(20003)被跳过，其余根正常完成，failed_root_codes 可见、partial=True。"""
    def walk(root_code, root_name, max_depth, root_codes_set, cc, pool, cache, diag):
        if root_code == "R2":
            raise _FakeTimeout()
        return _walk_ok(root_code)

    _patch_infra(monkeypatch, walk)

    result = u8_bom._query_u8_bom_inventory(["R1", "R2", "R3"], max_depth=3)

    assert result.failed_root_codes == ["R2"]
    assert result.partial is True
    roots_in_rows = {r["root"] for r in result.rows}
    assert roots_in_rows == {"R1", "R3"}


def test_skip_recoverable_lock_and_deadlock_roots(monkeypatch):
    """锁(20047)与死锁(1205)同为可恢复，被跳过且不影响其余根。"""
    def walk(root_code, root_name, max_depth, root_codes_set, cc, pool, cache, diag):
        if root_code == "L":
            raise _FakeLockError()
        if root_code == "D":
            raise _FakeDeadlock()
        return _walk_ok(root_code)

    _patch_infra(monkeypatch, walk)

    result = u8_bom._query_u8_bom_inventory(["L", "D", "OK"], max_depth=3)

    assert set(result.failed_root_codes) == {"L", "D"}
    assert {r["root"] for r in result.rows} == {"OK"}


def test_circuit_breaker_aborts_after_consecutive_failures(monkeypatch):
    """连续 _MAX_CONSECUTIVE_ROOT_FAILURES 个根失败 → U8RootFailureBreakerError，携带 failed_root_codes。"""
    fail_codes = [f"F{i}" for i in range(u8_bom._MAX_CONSECUTIVE_ROOT_FAILURES)]
    codes = fail_codes + ["OK"]

    def walk(root_code, root_name, max_depth, root_codes_set, cc, pool, cache, diag):
        if root_code.startswith("F"):
            raise _FakeTimeout()
        return _walk_ok(root_code)

    _patch_infra(monkeypatch, walk)

    with pytest.raises(U8RootFailureBreakerError) as ei:
        u8_bom._query_u8_bom_inventory(codes, max_depth=3)

    assert ei.value.failed_root_codes == fail_codes


def test_circuit_breaker_resets_on_interleaved_success(monkeypatch):
    """成功与失败交错会重置连续计数，故仅在持续大面积失败时触发熔断。"""
    # 交替：失败、成功、失败、成功、失败（3 次失败但都不连续 → 不熔断）
    codes = ["F0", "S0", "F1", "S1", "F2", "S2"]

    def walk(root_code, root_name, max_depth, root_codes_set, cc, pool, cache, diag):
        if root_code.startswith("F"):
            raise _FakeTimeout()
        return _walk_ok(root_code)

    _patch_infra(monkeypatch, walk)

    result = u8_bom._query_u8_bom_inventory(codes, max_depth=3)

    assert set(result.failed_root_codes) == {"F0", "F1", "F2"}
    assert {r["root"] for r in result.rows} == {"S0", "S1", "S2"}
    assert result.partial is True


def test_query_cancelled_propagates_not_swallowed(monkeypatch):
    """QueryCancelledError 必须原样上抛，不能被故障隔离吞掉。"""
    def walk(root_code, root_name, max_depth, root_codes_set, cc, pool, cache, diag):
        if root_code == "C":
            raise QueryCancelledError("cancelled")
        return _walk_ok(root_code)

    _patch_infra(monkeypatch, walk)

    with pytest.raises(QueryCancelledError):
        u8_bom._query_u8_bom_inventory(["OK", "C"], max_depth=3)


def test_code_bug_not_swallowed_as_recoverable(monkeypatch):
    """代码缺陷（KeyError 等）不属可恢复 DB 故障，必须原样上抛暴露。"""
    def walk(root_code, root_name, max_depth, root_codes_set, cc, pool, cache, diag):
        if root_code == "BUG":
            raise KeyError("missing field in BOM row")
        return _walk_ok(root_code)

    _patch_infra(monkeypatch, walk)

    with pytest.raises(KeyError):
        u8_bom._query_u8_bom_inventory(["OK", "BUG"], max_depth=3)


def test_is_recoverable_root_failure_classification():
    """_is_recoverable_root_failure 仅认 20003/20047/1205 及死锁文本。"""
    assert u8_bom._is_recoverable_root_failure(_FakeTimeout()) is True
    assert u8_bom._is_recoverable_root_failure(_FakeLockError()) is True
    assert u8_bom._is_recoverable_root_failure(_FakeDeadlock()) is True
    # 死锁文本形式
    assert u8_bom._is_recoverable_root_failure(Exception("deadlock 1205")) is True
    # 代码缺陷不可恢复
    assert u8_bom._is_recoverable_root_failure(KeyError("x")) is False
    assert u8_bom._is_recoverable_root_failure(AttributeError("no attr")) is False
    assert u8_bom._is_recoverable_root_failure(ValueError("bad value")) is False


def test_u8_bom_query_result_partial_property():
    """U8BomQueryResult.partial 仅在有 failed_root_codes 时为 True。"""
    assert u8_bom.U8BomQueryResult(rows=[], failed_root_codes=[]).partial is False
    assert u8_bom.U8BomQueryResult(rows=[], failed_root_codes=["R1"]).partial is True
