"""issue #21 写入端失效钩子：知识库删除路径必须 bump 集合版本号。

对应验收②「知识库写入后，该 collection 的缓存按选定策略失效」。三条写入路径：

1. ``VectorMetadataAdapter.delete_chunks_by_file_name``（同文件替换 / 删除文件）；
2. ``KnowledgePersistence.delete_knowledge_record``（管理页删单条 chunk）；
3. ``DocumentProcessingPipeline.process``（上传入库，在
   ``tests/test_pipeline_chunk_dedup.py`` 里覆盖——那条路径需要 llama-index）。

本文件不依赖 Redis / DB / llama-index：DB 用假 session，缓存用「记录调用」的假件。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest

from app.adapters import retrieval_cache as cache_mod
from app.adapters.knowledge import metadata as metadata_mod
from app.adapters.knowledge import persistence as persistence_mod
from app.adapters.knowledge.constants import KNOWLEDGE_COLLECTION_NAME
from app.adapters.knowledge.metadata import VectorMetadataAdapter
from app.adapters.knowledge.persistence import KnowledgePersistence


class _RecordingCache:
    """假缓存：只记录失效调用（不碰 Redis）。"""

    enabled = True
    embedding_enabled = False

    def __init__(self) -> None:
        self.bumps: List[str] = []
        self.sync_bumps: List[str] = []

    async def invalidate_collection(self, collection: str):
        self.bumps.append(collection)
        return len(self.bumps)

    def invalidate_collection_sync(self, collection: str):
        self.sync_bumps.append(collection)
        return len(self.sync_bumps)


@pytest.fixture
def recording_cache():
    """把单例换成记录假件（conftest 的 autouse 夹具负责最终还原）。"""
    fake = _RecordingCache()
    cache_mod.set_retrieval_cache_for_tests(fake)
    return fake


class _FakeSession:
    """假 AsyncSession：``execute`` 返回带 rowcount 的结果或抛错，``commit`` 记账。"""

    def __init__(self, rowcount: int = 0, error: Exception | None = None) -> None:
        self._rowcount = rowcount
        self._error = error
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return SimpleNamespace(rowcount=self._rowcount)

    async def commit(self) -> None:
        self.committed = True


def _install_session(monkeypatch, module, session: _FakeSession) -> _FakeSession:
    monkeypatch.setattr(module, "AsyncSessionLocal", lambda: session)
    return session


# ---------------------------------------------------------------------------
# 1. delete_chunks_by_file_name（替换同名文件 / 删除文件）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_chunks_bumps_collection_when_rows_were_deleted(
    monkeypatch, recording_cache
):
    session = _install_session(monkeypatch, metadata_mod, _FakeSession(rowcount=7))

    deleted = await VectorMetadataAdapter().delete_chunks_by_file_name(
        "knowledge_chunks", "a.pdf"
    )

    assert deleted == 7
    assert session.committed is True
    assert recording_cache.bumps == ["knowledge_chunks"]


@pytest.mark.asyncio
async def test_delete_chunks_without_rows_does_not_bump(monkeypatch, recording_cache):
    _install_session(monkeypatch, metadata_mod, _FakeSession(rowcount=0))

    deleted = await VectorMetadataAdapter().delete_chunks_by_file_name(
        "knowledge_chunks", "a.pdf"
    )

    assert deleted == 0
    assert recording_cache.bumps == []


@pytest.mark.asyncio
async def test_delete_chunks_on_lazy_missing_table_does_not_bump(monkeypatch, recording_cache):
    """懒建表（表还不存在）是正常中间态：不报错、不 bump。"""
    from sqlalchemy.exc import ProgrammingError

    error = ProgrammingError(
        "DELETE FROM data_knowledge_chunks", {}, Exception('relation "data_knowledge_chunks" does not exist')
    )
    _install_session(monkeypatch, metadata_mod, _FakeSession(error=error))

    deleted = await VectorMetadataAdapter().delete_chunks_by_file_name(
        "knowledge_chunks", "a.pdf"
    )

    assert deleted == 0
    assert recording_cache.bumps == []


# ---------------------------------------------------------------------------
# 2. delete_knowledge_record（管理页删除单条 chunk）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_knowledge_record_bumps_document_collection(monkeypatch, recording_cache):
    _install_session(monkeypatch, persistence_mod, _FakeSession(rowcount=1))

    deleted = await KnowledgePersistence().delete_knowledge_record(42)

    assert deleted == 1
    # 删的是 data_knowledge_chunks → 逻辑集合名 knowledge_chunks
    assert recording_cache.bumps == [KNOWLEDGE_COLLECTION_NAME]


@pytest.mark.asyncio
async def test_delete_knowledge_record_without_rows_does_not_bump(
    monkeypatch, recording_cache
):
    _install_session(monkeypatch, persistence_mod, _FakeSession(rowcount=0))

    deleted = await KnowledgePersistence().delete_knowledge_record(42)

    assert deleted == 0
    assert recording_cache.bumps == []


# ---------------------------------------------------------------------------
# 3. 缓存不可用时删除路径必须照样成功（fail-open）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_still_succeeds_when_cache_is_unavailable(monkeypatch):
    """Redis 挂掉时删除必须成功：失效失败由 TTL 兜底，不能影响删除结果。"""
    from app.adapters.retrieval_cache import RetrievalCache

    class _BoomStore:
        async def get(self, key):  # pragma: no cover - 不会被调用
            raise RuntimeError("redis down")

        async def set(self, key, value, ttl=None):  # pragma: no cover
            raise RuntimeError("redis down")

        async def incr(self, key, amount=1):
            raise RuntimeError("redis down")

        async def delete(self, key):  # pragma: no cover
            return False

    cache_mod.set_retrieval_cache_for_tests(RetrievalCache(store=_BoomStore()))
    _install_session(monkeypatch, metadata_mod, _FakeSession(rowcount=3))

    deleted = await VectorMetadataAdapter().delete_chunks_by_file_name(
        "knowledge_chunks", "a.pdf"
    )

    assert deleted == 3  # 删除照常返回，exception 没漏出去
