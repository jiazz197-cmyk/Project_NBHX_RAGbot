"""issue #17：内容指纹 SQL 侧（预检 / 对账 / 清理 / 索引）单测。

沿用 ``tests/test_lexical_search_adapter.py`` 的假引擎模式：真实 SQL 文本 +
注入的 engine，既不需要数据库，也能断言「发了哪条 SQL、带了什么参数」。
"""

from __future__ import annotations

import pytest
from sqlalchemy import TextClause
from sqlalchemy.exc import ProgrammingError, OperationalError

from app.adapters.knowledge import chunk_fingerprint as cf
from app.core.exceptions import ValidationError
from app.domain.knowledge.chunk_identity import content_fingerprint


class _FakeResult:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def __init__(self, responder, recorder):
        self._responder = responder
        self._recorder = recorder
        self.closed = False

    def execution_options(self, **kwargs):
        self._recorder.append({"execution_options": kwargs})
        return self

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self._recorder.append({"sql": sql, "params": params, "raw": stmt})
        return self._responder(sql, params)

    def close(self):
        self.closed = True
        self._recorder.append({"close": True})

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeEngine:
    """connect() / begin() 都返回同一个假连接（够用且不依赖 SQLAlchemy 事务）。"""

    def __init__(self, responder, recorder):
        self._connection = _FakeConnection(responder, recorder)

    def connect(self):
        return self._connection

    def begin(self):
        return self._connection


class _Row:
    """支持属性访问的结果行（SQLAlchemy Row 的鸭子类型替身）。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _engine(responder, recorder):
    return _FakeEngine(responder, recorder)


def _missing_table_error(table="data_knowledge_chunks"):
    return ProgrammingError(
        "select 1", {}, Exception(f'relation "{table}" does not exist')
    )


# ---------------------------------------------------------------------------
# 1. 写入前端预检 existing_fingerprints
# ---------------------------------------------------------------------------


def test_existing_fingerprints_returns_prefixed_hits():
    recorder = []
    seen_params = {}

    def responder(sql, params):
        seen_params.update(params or {})
        return _FakeResult([_Row(digest="a" * 32), _Row(digest=None)])

    result = cf.existing_fingerprints(
        "knowledge_chunks",
        [content_fingerprint("甲"), content_fingerprint("乙")],
        engine=_engine(responder, recorder),
    )

    assert result == {f"{cf.FINGERPRINT_PREFIX}{'a' * 32}"}
    sql = recorder[0]["sql"]
    assert "md5(text) = ANY(:digests)" in sql
    assert "data_knowledge_chunks" in sql
    # 传给 PG 的是裸 32 位 hex（与 PG 侧 md5(text) 同口径），不是带前缀的指纹
    assert seen_params["digests"] == [content_fingerprint("甲")[len(cf.FINGERPRINT_PREFIX) :],
                                      content_fingerprint("乙")[len(cf.FINGERPRINT_PREFIX) :]]
    assert all(len(d) == 32 for d in seen_params["digests"])


def test_existing_fingerprints_drops_malformed_and_dedupes():
    recorder = []

    def responder(sql, params):
        return _FakeResult([])

    cf.existing_fingerprints(
        "knowledge_chunks",
        ["", None, "sha1:abc", content_fingerprint("同一条"), content_fingerprint("同一条")],
        engine=_engine(responder, recorder),
    )

    assert len(recorder) == 1
    assert recorder[0]["params"]["digests"] == [
        content_fingerprint("同一条")[len(cf.FINGERPRINT_PREFIX) :]
    ]


def test_existing_fingerprints_without_valid_input_makes_no_query():
    recorder = []

    def responder(sql, params):  # pragma: no cover - 不应被调用
        raise AssertionError("没有合法指纹时不该查库")

    assert (
        cf.existing_fingerprints(
            "knowledge_chunks", [None, "bad"], engine=_engine(responder, recorder)
        )
        == set()
    )
    assert recorder == []


def test_existing_fingerprints_batches_large_input():
    recorder = []
    batches = []

    def responder(sql, params):
        batches.append(params["digests"])
        return _FakeResult([])

    fingerprints = [content_fingerprint(f"chunk-{i}") for i in range(2500)]
    cf.existing_fingerprints("knowledge_chunks", fingerprints, engine=_engine(responder, recorder))

    assert [len(b) for b in batches] == [1000, 1000, 500]


def test_existing_fingerprints_treats_missing_table_as_empty():
    recorder = []

    def responder(sql, params):
        raise _missing_table_error()

    assert (
        cf.existing_fingerprints(
            "knowledge_chunks", [content_fingerprint("x")], engine=_engine(responder, recorder)
        )
        == set()
    )


def test_existing_fingerprints_reraises_other_db_errors():
    """其他 DB 异常必须上抛，由写入端决定降级（不在本层吞掉）。"""

    def responder(sql, params):
        raise OperationalError("select 1", {}, Exception("connection refused"))

    with pytest.raises(OperationalError):
        cf.existing_fingerprints(
            "knowledge_chunks", [content_fingerprint("x")], engine=_engine(responder, [])
        )


# ---------------------------------------------------------------------------
# 2. 存量对账 plan / delete / empty
# ---------------------------------------------------------------------------


def test_plan_duplicate_groups_shapes_plan():
    recorder = []

    def responder(sql, params):
        if "count(*) FILTER" in sql:
            return _FakeResult([_Row(total_rows=153, group_count=2, redundant_rows=3)])
        return _FakeResult(
            [
                _Row(
                    digest="b" * 32,
                    ids=[7, 9, 11],
                    n=3,
                    sources=["《员工手册》-2026.pdf", ""],
                ),
                _Row(digest="c" * 32, ids=[1, 2], n=2, sources=None),
            ]
        )

    plan = cf.plan_duplicate_groups("knowledge_chunks", engine=_engine(responder, recorder), limit=5)

    assert (plan.collection, plan.table) == ("knowledge_chunks", "data_knowledge_chunks")
    assert (plan.total_rows, plan.group_count, plan.redundant_rows) == (153, 2, 3)
    assert plan.has_duplicates is True
    assert plan.groups[0].ids == [7, 9, 11]
    assert plan.groups[0].sources == ["《员工手册》-2026.pdf"]
    assert plan.groups[1].sources == []
    assert recorder[1]["params"] == {"limit": 5}
    # 只读：没有任何写语句
    assert all(not sql["sql"].lstrip().upper().startswith(("DELETE", "CREATE")) for sql in recorder)


def test_plan_duplicate_groups_missing_table_is_not_an_error():
    def responder(sql, params):
        raise _missing_table_error()

    plan = cf.plan_duplicate_groups("knowledge_chunks", engine=_engine(responder, []))

    assert plan.total_rows == 0 and plan.has_duplicates is False


def test_delete_duplicate_rows_keeps_min_id_per_group():
    recorder = []

    def responder(sql, params):
        if sql.lstrip().upper().startswith("SELECT"):
            assert "row_number() OVER (PARTITION BY md5(text) ORDER BY id)" in sql
            return _FakeResult([_Row(id=9), _Row(id=11)])
        return _FakeResult(rowcount=len(params["ids"]))

    deleted = cf.delete_duplicate_rows("knowledge_chunks", engine=_engine(responder, recorder))

    assert deleted == 2
    delete_sqls = [r for r in recorder if r["sql"].lstrip().upper().startswith("DELETE")]
    assert len(delete_sqls) == 1
    assert "DELETE FROM data_knowledge_chunks WHERE id = ANY(:ids)" == delete_sqls[0]["sql"]
    assert delete_sqls[0]["params"]["ids"] == [9, 11]


def test_delete_duplicate_rows_batches_and_counts_all():
    recorder = []

    def responder(sql, params):
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeResult([_Row(id=i) for i in range(2500)])
        return _FakeResult(rowcount=len(params["ids"]))

    deleted = cf.delete_duplicate_rows(
        "knowledge_chunks", engine=_engine(responder, recorder), batch_size=1000
    )

    assert deleted == 2500
    assert [len(r["params"]["ids"]) for r in recorder if r["sql"].startswith("DELETE")] == [
        1000,
        1000,
        500,
    ]


def test_empty_chunk_helpers_use_btrim():
    recorder = []

    def responder(sql, params):
        if sql.lstrip().upper().startswith("SELECT"):
            return _FakeResult([_Row(n=1)])
        return _FakeResult(rowcount=1)

    engine = _engine(responder, recorder)
    assert cf.count_empty_chunks("knowledge_chunks", engine=engine) == 1
    assert cf.purge_empty_chunks("knowledge_chunks", engine=engine) == 1

    assert "btrim(text) = ''" in recorder[0]["sql"]
    assert recorder[1]["sql"].startswith("DELETE FROM data_knowledge_chunks WHERE btrim(text) = ''")


# ---------------------------------------------------------------------------
# 3. 索引保障
# ---------------------------------------------------------------------------


def test_fingerprint_index_ddl_is_idempotent_and_not_unique():
    ddl = cf.plan_fingerprint_index("knowledge_chunks")

    assert ddl == (
        "CREATE INDEX IF NOT EXISTS data_knowledge_chunks_text_md5_idx"
        " ON data_knowledge_chunks (md5(text))"
    )
    # 承重：唯一索引会让 llama-index 的裸 insert（无 ON CONFLICT）整批回滚
    assert "UNIQUE" not in ddl
    assert cf.fingerprint_index_name("knowledge_chunks") == "data_knowledge_chunks_text_md5_idx"


def test_ensure_fingerprint_indexes_uses_autocommit_and_skips_missing_table():
    recorder = []

    def responder(sql, params):
        if "to_regclass" in sql:
            return _FakeResult([_Row(reg=None if params["table"] == "data_missing" else params["table"])])
        return _FakeResult([])

    ensured = cf.ensure_fingerprint_indexes(
        ["knowledge_chunks", "missing"], engine=_engine(responder, recorder)
    )

    assert ensured == ["data_knowledge_chunks_text_md5_idx"]
    # SQLAlchemy 2.0 的 Connection.close() 会回滚未提交事务 → DDL 必须走 AUTOCOMMIT
    assert {"execution_options": {"isolation_level": "AUTOCOMMIT"}} in recorder
    index_sqls = [r["sql"] for r in recorder if r.get("sql", "").startswith("CREATE INDEX")]
    assert index_sqls == [
        "CREATE INDEX IF NOT EXISTS data_knowledge_chunks_text_md5_idx"
        " ON data_knowledge_chunks (md5(text))"
    ]


def test_ensure_fingerprint_indexes_supports_concurrently():
    recorder = []

    def responder(sql, params):
        if "to_regclass" in sql:
            return _FakeResult([_Row(reg="data_excel_db_chunks")])
        return _FakeResult([])

    ensured = cf.ensure_fingerprint_indexes(
        ["excel_db_chunks"], engine=_engine(responder, recorder), concurrently=True
    )

    assert ensured == ["data_excel_db_chunks_text_md5_idx"]
    index_sqls = [r["sql"] for r in recorder if r.get("sql", "").startswith("CREATE INDEX")]
    assert index_sqls == [
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS data_excel_db_chunks_text_md5_idx"
        " ON data_excel_db_chunks (md5(text))"
    ]


def test_ensure_fingerprint_indexes_degrades_on_ddl_failure():
    recorder = []

    def responder(sql, params):
        if "to_regclass" in sql:
            return _FakeResult([_Row(reg="data_knowledge_chunks")])
        raise OperationalError("CREATE INDEX", {}, Exception("permission denied"))

    assert cf.ensure_fingerprint_indexes(["knowledge_chunks"], engine=_engine(responder, recorder)) == []


def test_collection_name_is_validated_before_sql_interpolation():
    """集合名会直接拼进 SQL → 非法名必须被物理表映射拦下（防注入）。"""
    with pytest.raises(ValidationError):
        cf.plan_fingerprint_index("knowledge_chunks; drop table users")
    with pytest.raises(ValidationError):
        cf.plan_duplicate_groups("Knowledge-Chunks")


# ---------------------------------------------------------------------------
# 4. 指纹写锁（并发竞态：预检 + 写入必须串行化）
# ---------------------------------------------------------------------------


def _lock_responder(sequence):
    """按调用顺序依次返回 try 锁结果，之后一直返回最后一个。"""
    calls = {"n": 0}

    def responder(sql, params):
        if "pg_try_advisory_lock" in sql:
            index = min(calls["n"], len(sequence) - 1)
            calls["n"] += 1
            return _FakeResult([_Row(locked=sequence[index])])
        return _FakeResult([_Row(locked=True)])

    return responder


def test_write_guard_yields_true_and_unlocks():
    recorder = []
    engine = _engine(_lock_responder([True]), recorder)

    with cf.fingerprint_write_guard("knowledge_chunks", engine=engine) as locked:
        assert locked is True

    sqls = [r.get("sql", "") for r in recorder]
    assert any("pg_try_advisory_lock(hashtext(:key)::bigint)" in s for s in sqls)
    assert any("pg_advisory_unlock(hashtext(:key)::bigint)" in s for s in sqls)
    # 承重：裸字符串会让 SQLAlchemy 抛 "Not an executable object" 并静默降级
    # （2026-09-21 真实踩过），所有语句必须是 text() 包出来的 TextClause
    assert all(isinstance(r["raw"], TextClause) for r in recorder if "raw" in r)
    assert {"close": True} in recorder  # 连接必须归还（否则锁会跟着连接泄漏）
    assert recorder[0]["params"] == {"key": "data_knowledge_chunks"}


def test_write_guard_retries_until_lock_available():
    recorder = []
    engine = _engine(_lock_responder([False, False, True]), recorder)

    with cf.fingerprint_write_guard(
        "knowledge_chunks", engine=engine, timeout_sec=5, poll_interval_sec=0
    ) as locked:
        assert locked is True

    try_calls = [s for s in (r.get("sql", "") for r in recorder) if "pg_try" in s]
    assert len(try_calls) == 3


def test_write_guard_degrades_on_timeout():
    """等锁超时不能阻断写入：yield False、不解锁、连接照常归还。"""
    recorder = []
    engine = _engine(_lock_responder([False]), recorder)

    with cf.fingerprint_write_guard(
        "knowledge_chunks", engine=engine, timeout_sec=0, poll_interval_sec=0
    ) as locked:
        assert locked is False

    sqls = [r.get("sql", "") for r in recorder]
    assert not any("pg_advisory_unlock" in s for s in sqls)
    assert {"close": True} in recorder


def test_write_guard_degrades_when_lock_unavailable():
    """取锁报错（DB 抖动）同样降级，不把异常抛给上传主链。"""
    recorder = []

    def responder(sql, params):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    with cf.fingerprint_write_guard("knowledge_chunks", engine=_engine(responder, recorder)) as locked:
        assert locked is False


def test_write_guard_releases_lock_even_if_body_raises():
    recorder = []
    engine = _engine(_lock_responder([True]), recorder)

    with pytest.raises(RuntimeError):
        with cf.fingerprint_write_guard("knowledge_chunks", engine=engine):
            raise RuntimeError("boom")

    sqls = [r.get("sql", "") for r in recorder]
    assert any("pg_advisory_unlock" in s for s in sqls)
    assert {"close": True} in recorder
