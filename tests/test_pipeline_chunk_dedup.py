"""issue #17：写入端内容级去重（pipeline）单测。

按既有风格用 ``object.__new__`` 手工装配，绕开分割器 / 嵌入模型 / 向量库，
只验证「哪些块被写、跳过多少、返回什么」。
"""

from __future__ import annotations

import io
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

pytest.importorskip("llama_index")

from app.adapters.doc_processing.pipeline import DocumentProcessingPipeline  # noqa: E402
from app.domain.knowledge.chunk_identity import (  # noqa: E402
    FINGERPRINT_PREFIX,
    content_fingerprint,
)


class _FakeGuard:
    """记录临界区边界，用于断言「预检 + 写入」确实都在守卫内。"""

    def __init__(self, events):
        self._events = events

    def __enter__(self):
        self._events.append("guard-enter")
        return True

    def __exit__(self, *exc_info):
        self._events.append("guard-exit")
        return False


class _FakeVectorStore:
    """替身：记录预检与写入，可配置「库里已有什么」或直接抛错。"""

    def __init__(self, existing=(), error=None):
        self.existing = set(existing)
        self.error = error
        self.lookups = []  # [(collection, fingerprints)]
        self.writes = []  # [(nodes, collection)]
        self.guards = []  # [collection]
        self.events = []  # 临界区/预检/写入的顺序

    def fingerprint_write_guard(self, collection):
        self.guards.append(collection)
        return _FakeGuard(self.events)

    def existing_fingerprints(self, collection, fingerprints):
        fingerprints = set(fingerprints)
        self.lookups.append((collection, fingerprints))
        self.events.append("lookup")
        if self.error is not None:
            raise self.error
        return self.existing & fingerprints

    def upsert_chunks(self, nodes, collection, embedding_model=None):
        self.writes.append((list(nodes), collection))
        self.events.append("write")
        return len(nodes)


class _FakeProcessor:
    def __init__(self, chunks):
        self.chunks = chunks

    def process_document(self, *args, **kwargs):
        return list(self.chunks)


def _pipeline(processor, store):
    pipe = object.__new__(DocumentProcessingPipeline)
    pipe.text_splitter = None
    pipe.tag_generator = None
    pipe.num_tags = 5
    pipe.excel_splitter = None
    pipe.embedding_model = None
    pipe.document_processor = processor
    pipe.vector_store_manager = store
    return pipe


def _stream(name="a.pdf", payload=b"fake-bytes"):
    stream = io.BytesIO(payload)
    stream.name = name
    return stream


def _chunk(text, **metadata):
    return SimpleNamespace(page_content=text, metadata=dict(metadata))


def _texts(nodes):
    return [node.text for node in nodes]


# ---------------------------------------------------------------------------
# 1. _documents_to_nodes：指纹 + 空块
# ---------------------------------------------------------------------------


def test_nodes_carry_content_fingerprint_of_cleaned_text():
    pipe = _pipeline(_FakeProcessor([]), _FakeVectorStore())
    nodes = pipe._documents_to_nodes(
        [_chunk("hello\x00world", source="a.pdf")], "knowledge_chunks"
    )

    assert len(nodes) == 1
    assert nodes[0].text == "helloworld"  # NUL 已清理
    # 指纹算在**清洗后**的字符串上，否则与 PG md5(text) 对不上账
    assert nodes[0].metadata["content_hash"] == content_fingerprint("helloworld")
    assert nodes[0].metadata["content_hash"].startswith(FINGERPRINT_PREFIX)


def test_documents_to_nodes_overwrites_stale_content_hash():
    """metadata 里带了过期/伪造的 content_hash → 必须按本次文本重算。"""
    pipe = _pipeline(_FakeProcessor([]), _FakeVectorStore())
    nodes = pipe._documents_to_nodes(
        [_chunk("真实内容", content_hash="md5:v1:" + "0" * 32)], "knowledge_chunks"
    )

    assert nodes[0].metadata["content_hash"] == content_fingerprint("真实内容")


@pytest.mark.parametrize("text", ["", "   ", "\n\t ", "\x00", " \x00 "])
def test_empty_chunks_are_not_written(text):
    """空/纯空白块不落库：检索端早已丢弃，且空串 md5 恒等会互相误判为重复。"""
    pipe = _pipeline(_FakeProcessor([]), _FakeVectorStore())

    assert pipe._documents_to_nodes([_chunk(text)], "knowledge_chunks") == []


# ---------------------------------------------------------------------------
# 2. _filter_duplicate_nodes：批内 + 库内
# ---------------------------------------------------------------------------


def test_batch_duplicates_are_dropped_and_counted():
    """同一批内部就有重复（PDF 页眉页脚 / Excel 重复表头）→ 只留首次出现。"""
    store = _FakeVectorStore()
    pipe = _pipeline(_FakeProcessor([]), store)
    nodes = [
        SimpleNamespace(text="甲", metadata={"content_hash": content_fingerprint("甲")}),
        SimpleNamespace(text="乙", metadata={"content_hash": content_fingerprint("乙")}),
        SimpleNamespace(text="甲", metadata={"content_hash": content_fingerprint("甲")}),
    ]

    kept, skipped = pipe._filter_duplicate_nodes(nodes, "knowledge_chunks")

    assert _texts(kept) == ["甲", "乙"]
    assert skipped == 1
    # 库内预检不重复查同一指纹
    assert store.lookups[0][1] == {content_fingerprint("甲"), content_fingerprint("乙")}


def test_existing_fingerprints_are_dropped():
    store = _FakeVectorStore(existing=[content_fingerprint("旧内容")])
    pipe = _pipeline(_FakeProcessor([]), store)
    nodes = [
        SimpleNamespace(text="旧内容", metadata={"content_hash": content_fingerprint("旧内容")}),
        SimpleNamespace(text="新内容", metadata={"content_hash": content_fingerprint("新内容")}),
    ]

    kept, skipped = pipe._filter_duplicate_nodes(nodes, "knowledge_chunks")

    assert _texts(kept) == ["新内容"]
    assert skipped == 1


def test_nodes_without_fingerprint_get_one_computed():
    store = _FakeVectorStore()
    pipe = _pipeline(_FakeProcessor([]), store)
    nodes = [SimpleNamespace(text="无指纹", metadata={"chunk_id": "x"})]

    kept, skipped = pipe._filter_duplicate_nodes(nodes, "knowledge_chunks")

    assert skipped == 0
    assert kept[0].metadata["content_hash"] == content_fingerprint("无指纹")


def test_lookup_failure_degrades_to_batch_only_dedupe():
    """预检失败只降级：批内仍去重，其余照写（P0 修复不该给上传引入新失败面）。"""
    store = _FakeVectorStore(error=RuntimeError("db down"))
    pipe = _pipeline(_FakeProcessor([]), store)
    nodes = [
        SimpleNamespace(text="甲", metadata={"content_hash": content_fingerprint("甲")}),
        SimpleNamespace(text="甲", metadata={"content_hash": content_fingerprint("甲")}),
    ]

    kept, skipped = pipe._filter_duplicate_nodes(nodes, "knowledge_chunks")

    assert len(kept) == 1
    assert skipped == 1


def test_empty_node_list_skips_lookup():
    store = _FakeVectorStore()
    pipe = _pipeline(_FakeProcessor([]), store)

    assert pipe._filter_duplicate_nodes([], "knowledge_chunks") == ([], 0)
    assert store.lookups == []


# ---------------------------------------------------------------------------
# 3. process()：计数与「全部重复不写」
# ---------------------------------------------------------------------------


def test_process_reports_skipped_duplicate_chunks():
    store = _FakeVectorStore(existing=[content_fingerprint("重复段")])
    processor = _FakeProcessor(
        [
            _chunk("重复段", source="a.pdf"),
            _chunk("新段一", source="a.pdf"),
            _chunk("新段一", source="a.pdf"),  # 批内重复
        ]
    )
    pipe = _pipeline(processor, store)

    result = pipe.process([_stream("a.pdf")], collection="knowledge_chunks")

    assert result["status"] == "success"
    assert result["processed_files"] == 1
    assert result["skipped_duplicate_chunks"] == 2  # 1 个库内 + 1 个批内
    assert result["failed_files"] == []
    assert len(store.writes) == 1
    assert _texts(store.writes[0][0]) == ["新段一"]
    assert store.writes[0][1] == "knowledge_chunks"


def test_process_skips_write_when_everything_is_duplicate():
    store = _FakeVectorStore(existing=[content_fingerprint("重复段")])
    pipe = _pipeline(_FakeProcessor([_chunk("重复段")]), store)

    result = pipe.process([_stream("a.pdf")], collection="knowledge_chunks")

    assert result["processed_files"] == 1  # 解析成功仍算 processed（语义不变）
    assert result["skipped_duplicate_chunks"] == 1
    assert store.writes == []  # 没有新块 → 不触发 embedding / 写入


def test_process_returns_zero_when_no_duplicates():
    store = _FakeVectorStore()
    pipe = _pipeline(_FakeProcessor([_chunk("甲"), _chunk("乙")]), store)

    result = pipe.process([_stream("a.pdf")], collection="knowledge_chunks")

    assert result["skipped_duplicate_chunks"] == 0
    assert len(store.writes[0][0]) == 2


def test_precheck_and_write_happen_inside_write_guard():
    """承重：预检与 upsert 必须在同一个 advisory lock 临界区内，否则并发双写。"""
    store = _FakeVectorStore()
    pipe = _pipeline(_FakeProcessor([_chunk("甲")]), store)

    pipe.process([_stream("a.pdf")], collection="knowledge_chunks")

    assert store.events == ["guard-enter", "lookup", "write", "guard-exit"]
    assert store.guards == ["knowledge_chunks"]


def test_process_empty_chunks_do_not_touch_vector_store():
    store = _FakeVectorStore()
    pipe = _pipeline(_FakeProcessor([_chunk(""), _chunk("   ")]), store)

    result = pipe.process([_stream("a.pdf")], collection="knowledge_chunks")

    assert result["processed_files"] == 1
    assert result["skipped_duplicate_chunks"] == 0
    assert store.writes == []


def test_process_parse_failure_still_reports_skipped_key():
    """issue15 的失败分支不受影响，且返回值保持了 skipped 键（契约稳定）。"""
    from app.adapters.doc_processing.exceptions import DocumentParseError

    class _Boom:
        def process_document(self, *args, **kwargs):
            raise DocumentParseError("boom")

    pipe = _pipeline(_Boom(), _FakeVectorStore())
    result = pipe.process([_stream("bad.xlsx")], collection="knowledge_chunks")

    assert result["processed_files"] == 0
    assert result["failed_files"] == [{"file_name": "bad.xlsx", "error": "boom"}]
    assert result["skipped_duplicate_chunks"] == 0


# ---------------------------------------------------------------------------
# 4. 任务完成消息（前端可见的验收口径）
# ---------------------------------------------------------------------------


def test_complete_message_plain_success():
    from app.adapters.doc_processing.document_task_runner import _compose_complete_message

    assert (
        _compose_complete_message(1, 1, [], skipped_duplicate_chunks=0)
        == "文档处理完成"
    )


def test_complete_message_reports_skipped_duplicates():
    from app.adapters.doc_processing.document_task_runner import _compose_complete_message

    message = _compose_complete_message(1, 1, [], skipped_duplicate_chunks=3)

    assert message == "文档处理完成，跳过 3 个已存在的重复块"


def test_complete_message_keeps_failure_detail_and_skipped_count():
    from app.adapters.doc_processing.document_task_runner import _compose_complete_message

    message = _compose_complete_message(
        1,
        3,
        [{"file_name": "a.xlsx", "error": "boom"}],
        skipped_duplicate_chunks=2,
    )

    assert message == (
        "文档处理完成：1/3 个文件成功，失败：a.xlsx（详见任务结果）"
        "，跳过 2 个已存在的重复块"
    )

