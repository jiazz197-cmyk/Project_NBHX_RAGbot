"""Regression tests for issue #14: /retriever/excel returns MinIO NoSuchKey.

根因：chunk metadata['source'] 存的是裸文件名（如「华翔定价表报价模型基础
参数.xlsx」），而真实 MinIO 对象在 documents/<timestamp>_<uuid>.xlsx；
get_charts 旧实现直接拿裸文件名当 object key → NoSuchKey、sources 为空。

修复分两端，本文件覆盖：
  1. 写入端：pipeline._documents_to_nodes 把 minio_object_path / file_id
     随 chunk metadata 落库；document_task_runner 逐文件传入定位信息。
  2. 检索端：get_charts 解析真实对象路径（新数据用 metadata 里的
     minio_object_path，旧数据按 file_name 反查 file_resource 表，
     uploader 一致优先、created_at 不晚于 chunk upload_time 的最新一条优先），
     按检索顺序逐个候选尝试，且清理下载临时文件。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("llama_index")

from app.adapters.ragsystem import retriever_for_nbhx  # noqa: E402
from app.adapters.ragsystem.retriever_for_nbhx import (  # noqa: E402
    OptimizedRetriever,
    _lookup_minio_object_path,
    _parse_naive_utc,
    _resolve_minio_object_name,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _FakeNode:
    """鸭子类型：检索返回的 NodeWithScore 只需要 text / metadata / score。"""

    def __init__(self, text, metadata, score=1.0):
        self.text = text
        self.metadata = metadata
        self.score = score


class _FakeQueryEngine:
    def __init__(self, nodes):
        self._nodes = nodes

    def query(self, question):
        return SimpleNamespace(source_nodes=list(self._nodes))


class _FakeSession:
    """替代 SessionLocal()：execute(...).all() 返回预设行。"""

    def __init__(self, rows=None, error=None):
        self._rows = rows or []
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, stmt):
        if self._error is not None:
            raise self._error
        return SimpleNamespace(all=lambda: list(self._rows))


def _make_retriever(nodes):
    """object.__new__ 绕过 __init__（避免 rag_system / 模型加载）。"""
    r = object.__new__(OptimizedRetriever)
    r.collection_name = "excel_db_chunks"
    r.query_engines = _FakeQueryEngine(nodes)
    r.default_top_n = 3
    r.model_manager = SimpleNamespace(clear_cache=lambda: None)
    return r


def _patch_downloads(monkeypatch, tmp_path, download_error=None):
    """替换 save_file_from_minio / excel_to_json，记录调用并返回真实临时文件。"""
    calls = {"objects": [], "parsed": [], "deleted": []}

    def fake_save(object_name, temp_prefix="minio_download_"):
        calls["objects"].append(object_name)
        if download_error is not None and len(calls["objects"]) == 1:
            raise download_error
        temp_file = tmp_path / f"download_{len(calls['objects'])}.xlsx"
        temp_file.write_bytes(b"fake-xlsx")
        return temp_file

    def fake_excel_to_json(path):
        calls["parsed"].append(Path(path))
        return '{"sheet_name": "S", "headers": [], "rows": []}'

    # 监测临时文件是否被清理
    original_unlink = Path.unlink

    def spy_unlink(self, *args, **kwargs):
        original_unlink(self, *args, **kwargs)
        calls["deleted"].append(self)

    monkeypatch.setattr(retriever_for_nbhx, "save_file_from_minio", fake_save)
    monkeypatch.setattr(retriever_for_nbhx, "excel_to_json", fake_excel_to_json)
    monkeypatch.setattr(Path, "unlink", spy_unlink)
    return calls


# ---------------------------------------------------------------------------
# 1. 写入端：pipeline 落 minio_object_path / file_id
# ---------------------------------------------------------------------------


def test_pipeline_nodes_carry_minio_object_path_and_file_id():
    from langchain_core.documents import Document
    from app.adapters.doc_processing.pipeline import DocumentProcessingPipeline

    pipeline = object.__new__(DocumentProcessingPipeline)
    docs = [
        Document(
            page_content="chunk-1",
            metadata={"file_name": "华翔.xlsx", "source": "华翔.xlsx"},
        ),
    ]
    nodes = pipeline._documents_to_nodes(
        docs,
        "excel_db_chunks",
        uploader="alice",
        upload_time="2026-09-20T10:00:00+00:00",
        minio_object_path="documents/20260920_ab12华翔.xlsx",
        file_id=42,
    )
    assert len(nodes) == 1
    # 裸文件名保留（前端展示 / 同名预检依赖），定位信息另落独立键
    assert nodes[0].metadata["source"] == "华翔.xlsx"
    assert nodes[0].metadata["minio_object_path"] == "documents/20260920_ab12华翔.xlsx"
    assert nodes[0].metadata["file_id"] == 42


def test_pipeline_nodes_without_minio_info_keep_legacy_shape():
    """不传定位信息时 metadata 不多出空键（路径 / 文档集合等既有调用不受影响）。"""
    from langchain_core.documents import Document
    from app.adapters.doc_processing.pipeline import DocumentProcessingPipeline

    pipeline = object.__new__(DocumentProcessingPipeline)
    nodes = pipeline._documents_to_nodes(
        [Document(page_content="c", metadata={"source": "a.pdf"})],
        "knowledge_chunks",
    )
    assert "minio_object_path" not in nodes[0].metadata
    assert "file_id" not in nodes[0].metadata


def test_task_runner_passes_minio_location_to_pipeline():
    """document_task_runner 逐文件传 minio_object_path / file_id（结构断言）。"""
    source = (
        REPO_ROOT / "app/adapters/doc_processing/document_task_runner.py"
    ).read_text(encoding="utf-8")
    assert "minio_object_path=file_record.minio_object_path" in source
    assert "file_id=file_record.id" in source


# ---------------------------------------------------------------------------
# 2. 检索端：_resolve_minio_object_name 优先级
# ---------------------------------------------------------------------------


def test_resolve_prefers_chunk_metadata_minio_object_path(monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("metadata 已带 minio_object_path 时不应反查数据库")

    monkeypatch.setattr(retriever_for_nbhx, "_lookup_minio_object_path", _fail)
    metadata = {"minio_object_path": "documents/20260920_ab12华翔.xlsx"}
    assert (
        _resolve_minio_object_name("华翔.xlsx", metadata)
        == "documents/20260920_ab12华翔.xlsx"
    )


def test_resolve_falls_back_to_file_resource_lookup_for_legacy_chunks(monkeypatch):
    captured = {}

    def fake_lookup(file_name, uploader=None, upload_time=None):
        captured.update(
            file_name=file_name, uploader=uploader, upload_time=upload_time
        )
        return "documents/legacy.xlsx"

    monkeypatch.setattr(retriever_for_nbhx, "_lookup_minio_object_path", fake_lookup)
    metadata = {"uploader": "alice", "upload_time": "2026-09-20T10:00:00+00:00"}
    assert _resolve_minio_object_name("华翔.xlsx", metadata) == "documents/legacy.xlsx"
    assert captured == {
        "file_name": "华翔.xlsx",
        "uploader": "alice",
        "upload_time": "2026-09-20T10:00:00+00:00",
    }


@pytest.mark.parametrize(
    "bad_source",
    ["", "   ", None, 123],
)
def test_resolve_rejects_invalid_source(bad_source):
    assert _resolve_minio_object_name(bad_source, {"minio_object_path": "x"}) is None


# ---------------------------------------------------------------------------
# 3. 检索端：file_resource 反查的选择策略
# ---------------------------------------------------------------------------


def _row(path, uploader, created_at):
    return SimpleNamespace(
        minio_object_path=path, uploader=uploader, created_at=created_at
    )


def test_lookup_prefers_uploader_then_latest_before_chunk_time(monkeypatch):
    rows = [
        # 最新一条是别人传的同名文件
        _row("documents/new_bob.xlsx", "bob", datetime(2026, 9, 20, 12, 0, 0)),
        # alice 的两条：chunk(10:00) 之前最新的是 09:50 这条
        _row("documents/alice_new.xlsx", "alice", datetime(2026, 9, 20, 9, 50, 0)),
        _row("documents/alice_old.xlsx", "alice", datetime(2026, 9, 19, 8, 0, 0)),
    ]
    monkeypatch.setattr(
        "app.core.database.SessionLocal",
        lambda: _FakeSession(rows),
    )
    assert (
        _lookup_minio_object_path(
            "华翔.xlsx",
            uploader="alice",
            upload_time="2026-09-20T10:00:00+00:00",
        )
        == "documents/alice_new.xlsx"
    )


def test_lookup_returns_earliest_when_all_rows_after_chunk_time(monkeypatch):
    rows = [
        _row("documents/late2.xlsx", "alice", datetime(2026, 9, 21, 12, 0, 0)),
        _row("documents/late1.xlsx", "alice", datetime(2026, 9, 20, 23, 0, 0)),
    ]
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _FakeSession(rows))
    assert (
        _lookup_minio_object_path(
            "华翔.xlsx",
            uploader="alice",
            upload_time="2026-09-20T10:00:00+00:00",
        )
        == "documents/late1.xlsx"
    )


def test_lookup_without_chunk_time_takes_latest(monkeypatch):
    rows = [
        _row("documents/a.xlsx", "alice", datetime(2026, 9, 20, 9, 50, 0)),
        _row("documents/b.xlsx", "alice", datetime(2026, 9, 19, 8, 0, 0)),
    ]
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _FakeSession(rows))
    assert _lookup_minio_object_path("华翔.xlsx") == "documents/a.xlsx"


def test_lookup_returns_none_when_no_rows_or_db_error(monkeypatch):
    monkeypatch.setattr("app.core.database.SessionLocal", lambda: _FakeSession([]))
    assert _lookup_minio_object_path("不存在.xlsx") is None

    monkeypatch.setattr(
        "app.core.database.SessionLocal",
        lambda: _FakeSession(error=RuntimeError("db down")),
    )
    assert _lookup_minio_object_path("华翔.xlsx") is None


def test_parse_naive_utc_strips_timezone_and_rejects_garbage():
    assert _parse_naive_utc("2026-09-20T10:00:00+00:00") == datetime(
        2026, 9, 20, 10, 0, 0
    )
    assert _parse_naive_utc("2026-09-20T10:00:00") == datetime(2026, 9, 20, 10, 0, 0)
    assert _parse_naive_utc("not-a-date") is None
    assert _parse_naive_utc(None) is None
    assert _parse_naive_utc("") is None


# ---------------------------------------------------------------------------
# 4. 检索端：get_charts 行为
# ---------------------------------------------------------------------------


def test_get_charts_downloads_resolved_object_not_bare_filename(
    monkeypatch, tmp_path
):
    """核心回归：下载必须用解析后的 object key，而不是裸文件名。"""
    nodes = [
        _FakeNode(
            text="价格参数 …",
            metadata={
                "source": "华翔定价表报价模型基础参数.xlsx",
                "minio_object_path": "documents/20260920_ab12华翔定价表报价模型基础参数.xlsx",
            },
        ),
    ]
    calls = _patch_downloads(monkeypatch, tmp_path)
    result = _make_retriever(nodes).get_charts("价格参数有哪些？")

    assert '"sheet_name"' in result
    assert calls["objects"] == ["documents/20260920_ab12华翔定价表报价模型基础参数.xlsx"]
    # 下载临时文件用完即删
    assert calls["deleted"], "excel_to_json 成功后应清理临时文件"


def test_get_charts_legacy_chunk_resolves_via_file_resource(monkeypatch, tmp_path):
    """旧数据（metadata 无 minio_object_path）走 file_resource 反查。"""
    nodes = [
        _FakeNode(
            text="价格参数 …",
            metadata={
                "source": "华翔.xlsx",
                "uploader": "alice",
                "upload_time": "2026-09-20T10:00:00+00:00",
            },
        ),
    ]
    monkeypatch.setattr(
        retriever_for_nbhx,
        "_lookup_minio_object_path",
        lambda file_name, uploader=None, upload_time=None: "documents/legacy.xlsx",
    )
    calls = _patch_downloads(monkeypatch, tmp_path)
    result = _make_retriever(nodes).get_charts("价格参数有哪些？")

    assert '"sheet_name"' in result
    assert calls["objects"] == ["documents/legacy.xlsx"]


def test_get_charts_tries_next_distinct_source_on_download_failure(
    monkeypatch, tmp_path
):
    """首个候选下载失败时按检索顺序尝试下一个（去重），而不是直接失败。"""
    nodes = [
        _FakeNode(
            text="a",
            metadata={"source": "甲.xlsx", "minio_object_path": "documents/a.xlsx"},
        ),
        # 同文件的第二个 chunk：去重后不重复尝试
        _FakeNode(
            text="a2",
            metadata={"source": "甲.xlsx", "minio_object_path": "documents/a.xlsx"},
        ),
        _FakeNode(
            text="b",
            metadata={"source": "乙.xlsx", "minio_object_path": "documents/b.xlsx"},
        ),
    ]
    calls = _patch_downloads(
        monkeypatch, tmp_path, download_error=RuntimeError("S3 NoSuchKey")
    )
    result = _make_retriever(nodes).get_charts("价格参数有哪些？")

    assert '"sheet_name"' in result
    assert calls["objects"] == ["documents/a.xlsx", "documents/b.xlsx"]


def test_get_charts_cleans_temp_file_when_parse_fails(monkeypatch, tmp_path):
    nodes = [
        _FakeNode(
            text="a",
            metadata={"source": "甲.xlsx", "minio_object_path": "documents/a.xlsx"},
        ),
    ]
    calls = _patch_downloads(monkeypatch, tmp_path)

    def bad_excel_to_json(path):
        calls["parsed"].append(Path(path))
        raise ValueError("bad workbook")

    monkeypatch.setattr(retriever_for_nbhx, "excel_to_json", bad_excel_to_json)
    result = _make_retriever(nodes).get_charts("价格参数有哪些？")

    assert result == {"error": "bad workbook"}
    assert calls["deleted"], "解析失败也应清理下载临时文件"


def test_get_charts_reports_error_when_object_not_resolvable(monkeypatch, tmp_path):
    nodes = [
        _FakeNode(text="a", metadata={"source": "已删除.xlsx"}),
    ]
    monkeypatch.setattr(
        retriever_for_nbhx, "_lookup_minio_object_path", lambda *a, **k: None
    )
    calls = _patch_downloads(monkeypatch, tmp_path)
    result = _make_retriever(nodes).get_charts("价格参数有哪些？")

    assert "已删除.xlsx" in result["error"]
    assert calls["objects"] == []


def test_get_charts_returns_error_when_retrieval_finds_nothing(tmp_path):
    result = _make_retriever([]).get_charts("价格参数有哪些？")
    assert result == {"error": "未找到相关文件"}


def test_get_response_returns_parallel_metadata_list():
    """get_response 附带与 source 平行的 metadata 列表（get_charts 依赖）。"""
    nodes = [
        _FakeNode(text="a", metadata={"source": "甲.xlsx", "minio_object_path": "documents/a.xlsx"}),
        _FakeNode(text="b", metadata={"source": "乙.xlsx"}),
    ]
    response = _make_retriever(nodes).get_response("任意问题")
    assert response["source"] == ["甲.xlsx", "乙.xlsx"]
    assert response["metadata"][0]["minio_object_path"] == "documents/a.xlsx"
    assert response["metadata"][1] == {"source": "乙.xlsx"}
