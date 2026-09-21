"""issue #16 字面检索适配器单测：SQL 形态、转义、集合名白名单与降级语义。

不连真实数据库：session / engine 全部用假件；SQL 文本按关键字断言。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app.adapters.knowledge.collection_tables import is_missing_table, physical_table
from app.adapters.knowledge.lexical_search import (
    PostgresLexicalSearcher,
    ensure_fulltext_indexes,
    index_ddl,
    like_pattern,
    plan_fulltext_indexes,
)
from app.core.exceptions import ValidationError

# ---------------------------------------------------------------------------
# 假件：async session / sync engine
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows or [])

    def fetchall(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows=None, error=None, recorder=None):
        self._rows = rows or []
        self._error = error
        self._recorder = recorder if recorder is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, stmt, params=None):
        self._recorder.append({"sql": str(stmt), "params": dict(params or {})})
        if self._error is not None:
            raise self._error
        return _FakeResult(self._rows)


def _session_factory(*, rows=None, error=None, recorder=None):
    def _factory():
        return _FakeSession(rows=rows, error=error, recorder=recorder)

    return _factory


class _FakeConnection:
    def __init__(self, tables=None, recorder=None, extension_error=None):
        self._tables = list(tables or [])
        self._recorder = recorder if recorder is not None else []
        self._extension_error = extension_error

    def execution_options(self, **kwargs):
        self._recorder.append({"execution_options": kwargs})
        return self

    def execute(self, stmt):
        sql = str(stmt)
        self._recorder.append({"sql": sql})
        if self._extension_error is not None and "CREATE EXTENSION" in sql.upper():
            raise self._extension_error
        if "information_schema" in sql:
            return _FakeResult([(t,) for t in self._tables])
        return _FakeResult([])


class _FakeEngine:
    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        connection = self._connection

        class _Ctx:
            def __enter__(self):
                return connection

            def __exit__(self, *exc_info):
                return False

        return _Ctx()


def _rows():
    return [
        SimpleNamespace(
            node_id="n1",
            content="  NBHX-GZZD-HR-006 绩效管理制度  ",
            source="《绩效管理制度》.docx",
            metadata_json='{"source": "《绩效管理制度》.docx", "page": 3}',
            hits=2,
        ),
        SimpleNamespace(
            node_id=None,
            content="无 node_id 的历史行",
            source=None,
            metadata_json="not-json",
            hits=1,
        ),
        SimpleNamespace(
            node_id="n-blank",
            content="   ",
            source="x.pdf",
            metadata_json=None,
            hits=1,
        ),
    ]


# ---------------------------------------------------------------------------
# 1. 纯函数：转义、表名、DDL
# ---------------------------------------------------------------------------


def test_like_pattern_escapes_wildcards_and_backslash():
    assert like_pattern("V254") == "%V254%"
    assert like_pattern("100%") == "%100\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("c:\\d") == "%c:\\\\d%"


def test_physical_table_whitelist_blocks_injection():
    assert physical_table("knowledge_chunks") == "data_knowledge_chunks"
    for bad in ("a;drop table x", "Knowledge", "", "1abc", "data_../x"):
        with pytest.raises(ValidationError):
            physical_table(bad)


def test_is_missing_table_detects_undefined_table_only():
    missing = ProgrammingError(
        "select 1", {}, Exception('relation "data_x" does not exist')
    )
    other = ProgrammingError("select 1", {}, Exception("permission denied for table x"))
    assert is_missing_table(missing) is True
    assert is_missing_table(other) is False


def test_index_ddl_is_idempotent_and_supports_concurrently():
    ddl = index_ddl("data_knowledge_chunks")
    assert ddl == (
        "CREATE INDEX IF NOT EXISTS idx_data_knowledge_chunks_text_trgm"
        " ON data_knowledge_chunks USING gin (text gin_trgm_ops)"
    )
    assert "CONCURRENTLY" in index_ddl("data_knowledge_chunks", concurrently=True)


# ---------------------------------------------------------------------------
# 2. 检索：SQL 形态与参数
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_builds_parameterized_ilike_sql():
    recorder = []
    searcher = PostgresLexicalSearcher(session_factory=_session_factory(recorder=recorder))

    hits = await searcher.search("knowledge_chunks", ["V254", "杨贵宁"], top_k=7)

    call = recorder[0]
    sql = call["sql"]
    assert "FROM data_knowledge_chunks" in sql
    assert "text ILIKE :kw_0 ESCAPE '\\'" in sql
    assert "text ILIKE :kw_1 ESCAPE '\\'" in sql
    assert "CASE WHEN text ILIKE :kw_0" in sql
    assert "ORDER BY hits DESC, length(text) ASC, node_id ASC" in sql
    assert "LIMIT :limit" in sql
    assert call["params"]["kw_0"] == "%V254%"
    assert call["params"]["kw_1"] == "%杨贵宁%"
    assert call["params"]["limit"] == 7
    # 关键词只作为参数出现，绝不拼进 SQL 文本
    assert "V254" not in sql and "杨贵宁" not in sql
    assert hits == []


@pytest.mark.asyncio
async def test_search_maps_rows_and_filters_blank_content():
    searcher = PostgresLexicalSearcher(
        session_factory=_session_factory(rows=_rows())
    )

    hits = await searcher.search("knowledge_chunks", ["NBHX"], top_k=5)

    assert [h.node_id for h in hits] == ["n1", ""]
    assert hits[0].content == "NBHX-GZZD-HR-006 绩效管理制度"  # strip
    assert hits[0].hits == 2
    assert hits[0].metadata == {"source": "《绩效管理制度》.docx", "page": 3}
    # 无 node_id / 非法 JSON 的历史行也能安全返回
    assert hits[1].source == "Unknown"
    assert hits[1].metadata == {}


@pytest.mark.asyncio
async def test_search_skips_db_when_no_usable_keyword():
    recorder = []
    searcher = PostgresLexicalSearcher(session_factory=_session_factory(recorder=recorder))

    assert await searcher.search("knowledge_chunks", [], top_k=5) == []
    assert await searcher.search("knowledge_chunks", ["   "], top_k=5) == []
    assert await searcher.search("knowledge_chunks", ["x" * 200], top_k=5) == []
    assert await searcher.search("knowledge_chunks", ["V254"], top_k=0) == []
    assert recorder == []


@pytest.mark.asyncio
async def test_search_rejects_illegal_collection_before_sql():
    recorder = []
    searcher = PostgresLexicalSearcher(session_factory=_session_factory(recorder=recorder))

    with pytest.raises(ValidationError):
        await searcher.search("x; drop table y", ["V254"], top_k=5)

    assert recorder == []


@pytest.mark.asyncio
async def test_search_returns_empty_when_table_missing():
    error = ProgrammingError(
        "select 1", {}, Exception('relation "data_new_collection" does not exist')
    )
    searcher = PostgresLexicalSearcher(
        session_factory=_session_factory(error=error)
    )

    assert await searcher.search("new_collection", ["V254"], top_k=5) == []


@pytest.mark.asyncio
async def test_search_degrades_on_other_db_errors():
    """稀疏路任何 DB 异常都不能把异常抛给检索主链。"""
    searcher = PostgresLexicalSearcher(
        session_factory=_session_factory(
            error=OperationalError("select 1", {}, Exception("connection refused"))
        )
    )

    assert await searcher.search("knowledge_chunks", ["V254"], top_k=5) == []


@pytest.mark.asyncio
async def test_search_honours_keyword_caps():
    recorder = []
    searcher = PostgresLexicalSearcher(
        session_factory=_session_factory(recorder=recorder),
        max_keywords=2,
        max_keyword_len=6,
    )

    await searcher.search("knowledge_chunks", ["V254", "杨贵宁", "第三个", "太长太长太长"], top_k=5)

    params = recorder[0]["params"]
    assert params["kw_0"] == "%V254%"
    assert params["kw_1"] == "%杨贵宁%"
    assert "kw_2" not in params


# ---------------------------------------------------------------------------
# 3. 索引保障（pg_trgm + GIN）
# ---------------------------------------------------------------------------


def test_ensure_fulltext_indexes_creates_extension_and_indexes():
    recorder = []
    connection = _FakeConnection(
        tables=["data_knowledge_chunks", "data_excel_db_chunks"], recorder=recorder
    )
    engine = _FakeEngine(connection)

    ensured = ensure_fulltext_indexes(engine=engine)

    assert ensured == [
        "idx_data_knowledge_chunks_text_trgm",
        "idx_data_excel_db_chunks_text_trgm",
    ]
    sqls = [r.get("sql", "") for r in recorder]
    assert any("CREATE EXTENSION IF NOT EXISTS pg_trgm" in s for s in sqls)
    index_sqls = [s for s in sqls if s.startswith("CREATE INDEX")]
    assert len(index_sqls) == 2
    assert all("USING gin (text gin_trgm_ops)" in s for s in index_sqls)
    assert all("CONCURRENTLY" not in s for s in index_sqls)
    assert {"execution_options": {"isolation_level": "AUTOCOMMIT"}} in recorder


def test_ensure_fulltext_indexes_supports_concurrently():
    recorder = []
    connection = _FakeConnection(tables=["data_knowledge_chunks"], recorder=recorder)

    ensure_fulltext_indexes(engine=_FakeEngine(connection), concurrently=True)

    index_sqls = [r["sql"] for r in recorder if r.get("sql", "").startswith("CREATE INDEX")]
    assert index_sqls == [
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_data_knowledge_chunks_text_trgm"
        " ON data_knowledge_chunks USING gin (text gin_trgm_ops)"
    ]


def test_ensure_fulltext_indexes_warns_without_superuser():
    """pg_trgm 装不上：不抛异常、不建索引（ILIKE 仍能顺序扫描）。"""
    recorder = []
    connection = _FakeConnection(
        tables=["data_knowledge_chunks"],
        recorder=recorder,
        extension_error=SQLAlchemyError("permission denied to create extension"),
    )

    assert ensure_fulltext_indexes(engine=_FakeEngine(connection)) == []
    assert not [
        r for r in recorder if r.get("sql", "").startswith("CREATE INDEX")
    ]


def test_plan_fulltext_indexes_is_read_only():
    recorder = []
    connection = _FakeConnection(tables=["data_knowledge_chunks"], recorder=recorder)

    plans = plan_fulltext_indexes(engine=_FakeEngine(connection))

    assert plans == [
        (
            "data_knowledge_chunks",
            "CREATE INDEX IF NOT EXISTS idx_data_knowledge_chunks_text_trgm"
            " ON data_knowledge_chunks USING gin (text gin_trgm_ops)",
        )
    ]
    assert not [r for r in recorder if r.get("sql", "").startswith("CREATE")]
