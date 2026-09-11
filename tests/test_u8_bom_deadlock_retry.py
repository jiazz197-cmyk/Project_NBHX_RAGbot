from __future__ import annotations

from typing import Any

from app.adapters.sqlserver import u8_bom


class _DeadlockError(Exception):
    pass


class _FlakyClient:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    def query(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        self.calls += 1
        if self.calls <= self.failures:
            raise _DeadlockError(1205, b"Transaction was deadlocked on lock resources")
        return [{"ok": True}]


def test_query_with_deadlock_retry_sleeps_and_retries(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(u8_bom.time, "sleep", lambda seconds: sleeps.append(seconds))
    client = _FlakyClient(failures=2)

    rows = u8_bom._query_with_deadlock_retry(client, "SELECT 1", log_label="test")

    assert rows == [{"ok": True}]
    assert client.calls == 3
    assert sleeps == [0.3, 0.8]


def test_query_with_deadlock_retry_raises_after_retry_budget(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(u8_bom.time, "sleep", lambda seconds: sleeps.append(seconds))
    client = _FlakyClient(failures=4)

    try:
        u8_bom._query_with_deadlock_retry(client, "SELECT 1", log_label="test")
    except _DeadlockError:
        pass
    else:  # pragma: no cover
        raise AssertionError("deadlock error should be raised after retry budget")

    assert client.calls == 4
    assert sleeps == [0.3, 0.8, 1.5]
