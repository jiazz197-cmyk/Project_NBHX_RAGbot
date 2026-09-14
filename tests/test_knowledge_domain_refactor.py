"""Knowledge domain refactor regression tests (issue #2).

覆盖「collection2 迁出 closing_form 并全面语义化重命名」的关键行为：
  1. knowledge 域 UseCase 业务逻辑（fake port，无外部服务）
  2. 结构安全检查：knowledge 域不依赖 closing_form、表常量、路由挂载
  3. 检索语义化：RetrievalQuery 无 instance_id、集合名校验、白名单访问控制
  4. 文档处理链路 collection 贯穿（UseCase 层）
  5. 向量写入表名语义化（重依赖测试，torch/llama_index 缺失时自动跳过；
     CI 最小依赖集下其余测试仍全部可跑）
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import APIException, NotFoundError
from app.ports.contracts.identity import (
    ROLE_SUPERUSER,
    ROLE_USER,
    CurrentUserDTO,
)
from app.usecases.knowledge.operations import (
    DeleteKnowledgeRecordUseCase,
    ListKnowledgeRecordsUseCase,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class FakeKnowledgePersistence:
    """内存版 KnowledgePersistencePort 实现。"""

    def __init__(self, rows=None, *, exists=True, rowcount=1):
        self.rows = rows or []
        self._exists = exists
        self._rowcount = rowcount
        self.checked_ids: list[int] = []
        self.deleted_ids: list[int] = []

    async def list_knowledge_records(self):
        return list(self.rows)

    async def check_knowledge_record_exists(self, record_id: int) -> bool:
        self.checked_ids.append(record_id)
        return self._exists

    async def delete_knowledge_record(self, record_id: int) -> int:
        self.deleted_ids.append(record_id)
        return self._rowcount


def _admin() -> CurrentUserDTO:
    return CurrentUserDTO(id="u1", username="admin", name="管理员", role="admin")


# ---------------------------------------------------------------------------
# 1. Knowledge UseCase 业务逻辑
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_knowledge_records_returns_rows():
    rows = [
        {"id": "2", "text": "chunk-b", "file_name": "b.pdf", "upload_time": "2026-01-02 10:00:00", "uploader": "u2", "status": "approved"},
        {"id": "1", "text": "chunk-a", "file_name": "a.pdf", "upload_time": "2026-01-01 10:00:00", "uploader": "u1", "status": "approved"},
    ]
    persistence = FakeKnowledgePersistence(rows)
    result = await ListKnowledgeRecordsUseCase(persistence).execute()
    assert result.success is True
    assert result.total == 2
    assert result.records == rows


@pytest.mark.asyncio
async def test_list_knowledge_records_empty():
    result = await ListKnowledgeRecordsUseCase(FakeKnowledgePersistence()).execute()
    assert result.success is True
    assert result.total == 0
    assert result.records == []


@pytest.mark.asyncio
async def test_delete_knowledge_record_success():
    persistence = FakeKnowledgePersistence(exists=True, rowcount=1)
    result = await DeleteKnowledgeRecordUseCase(persistence).execute(7, _admin())
    assert result.success is True
    assert result.message == "删除成功"
    assert result.deleted_id == "7"
    assert persistence.deleted_ids == [7]


@pytest.mark.asyncio
async def test_delete_knowledge_record_missing_raises_404():
    persistence = FakeKnowledgePersistence(exists=False)
    with pytest.raises(NotFoundError):
        await DeleteKnowledgeRecordUseCase(persistence).execute(404, _admin())
    assert persistence.deleted_ids == []


@pytest.mark.asyncio
async def test_delete_knowledge_record_zero_rowcount_raises_500():
    persistence = FakeKnowledgePersistence(exists=True, rowcount=0)
    with pytest.raises(APIException) as exc_info:
        await DeleteKnowledgeRecordUseCase(persistence).execute(9, _admin())
    assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# 2. 结构安全检查：knowledge 域独立于 closing_form
# ---------------------------------------------------------------------------

KNOWLEDGE_DOMAIN_FILES = [
    "app/api/v1/knowledge.py",
    "app/adapters/knowledge/__init__.py",
    "app/adapters/knowledge/adapter.py",
    "app/adapters/knowledge/constants.py",
    "app/adapters/knowledge/persistence.py",
    "app/adapters/web/endpoints/knowledge.py",
    "app/ports/outbound/knowledge.py",
    "app/usecases/knowledge/__init__.py",
    "app/usecases/knowledge/operations.py",
    "app/usecases/knowledge/results.py",
]


@pytest.mark.parametrize("rel_path", KNOWLEDGE_DOMAIN_FILES, ids=str)
def test_knowledge_domain_does_not_reference_closing_form(rel_path):
    """验收标准：新 knowledge 域不 import app.adapters.closing_form / app.usecases.closing_form。"""
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert "closing_form" not in source, f"{rel_path} 不应引用 closing_form"


def test_knowledge_chunks_table_constants():
    from app.adapters.knowledge.constants import (
        KNOWLEDGE_CHUNKS_TABLE,
        KNOWLEDGE_COLLECTION_NAME,
    )

    assert KNOWLEDGE_CHUNKS_TABLE == "data_knowledge_chunks"
    assert KNOWLEDGE_COLLECTION_NAME == "knowledge_chunks"


def test_knowledge_router_routes_and_prefix():
    from app.api.v1 import knowledge, prefixes

    assert prefixes.KNOWLEDGE == "/knowledge"
    route_paths = {(route.path, tuple(sorted(route.methods))) for route in knowledge.router.routes}
    assert ("/records", ("GET",)) in route_paths
    assert ("/records/{record_id}", ("DELETE",)) in route_paths


def test_knowledge_router_mounted_in_registry_source():
    """registry 模块 import 会拉全部路由（含重依赖），这里做源码级断言。"""
    source = (REPO_ROOT / "app/api/v1/registry.py").read_text(encoding="utf-8")
    assert "knowledge.router" in source
    assert "p.KNOWLEDGE" in source


def test_knowledge_router_registered_in_openapi_tags():
    from app.api.v1 import tags

    assert tags.KNOWLEDGE == "Knowledge"
    assert any(m["name"] == "Knowledge" for m in tags.OPENAPI_TAG_METADATA)


# ---------------------------------------------------------------------------
# 3. 检索语义化：DTO / 集合名校验 / 白名单
# ---------------------------------------------------------------------------

def test_retrieval_query_has_no_instance_id_field():
    from app.ports.outbound.retriever import RetrievalQuery

    field_names = {f.name for f in dataclasses.fields(RetrievalQuery)}
    assert "instance_id" not in field_names
    assert "collection_name" in field_names


def test_collection_name_pattern():
    from app.api.v1.retriever import _COLLECTION_NAME_PATTERN

    pattern = re.compile(_COLLECTION_NAME_PATTERN)
    for valid in ("knowledge_chunks", "doc_collection_1", "a", "kb_2026_v2"):
        assert pattern.match(valid), f"{valid} 应为合法集合名"
    for invalid in ("Knowledge_Chunks", "1abc", "-abc", "ab-cd", "ab.cd", "data_knowledge_chunks;", ""):
        assert not pattern.match(invalid), f"{invalid} 应为非法集合名"


def _retriever_user(role: str) -> CurrentUserDTO:
    return CurrentUserDTO(id="u1", username="tester", name="t", role=role)


def test_collection_access_superuser_bypasses_whitelist(monkeypatch):
    from app.api.v1 import retriever
    from app.core.config import settings

    monkeypatch.setattr(settings, "RETRIEVER_ALLOWED_COLLECTIONS", ["knowledge_chunks"])
    superuser = _retriever_user(ROLE_SUPERUSER)
    # superuser 访问白名单外的集合也放行
    assert (
        retriever._ensure_collection_access("doc_collection_1", superuser)
        == "doc_collection_1"
    )


def test_collection_access_regular_user_whitelist_hit(monkeypatch):
    from app.api.v1 import retriever
    from app.core.config import settings
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "RETRIEVER_ALLOWED_COLLECTIONS", ["knowledge_chunks"])
    user = _retriever_user(ROLE_USER)
    assert (
        retriever._ensure_collection_access("knowledge_chunks", user)
        == "knowledge_chunks"
    )
    with pytest.raises(HTTPException) as exc_info:
        retriever._ensure_collection_access("doc_collection_1", user)
    assert exc_info.value.status_code == 403


def test_collection_accepts_data_prefixed_whitelist_entries(monkeypatch):
    """白名单写物理表名（data_ 前缀）时同样兼容语义集合名。"""
    from app.api.v1 import retriever
    from app.core.config import settings

    monkeypatch.setattr(settings, "RETRIEVER_ALLOWED_COLLECTIONS", ["data_knowledge_chunks"])
    user = _retriever_user(ROLE_USER)
    assert retriever._ensure_collection_access("knowledge_chunks", user) == "knowledge_chunks"


def test_env_example_whitelist_placeholder_semantic():
    content = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"RETRIEVER_ALLOWED_COLLECTIONS=(\S+)", content)
    assert match is not None
    assert "knowledge_chunks" in match.group(1)


# ---------------------------------------------------------------------------
# 4. 文档处理链路：collection 贯穿（UseCase 层）
# ---------------------------------------------------------------------------

class FakeDocumentRegistration:
    def __init__(self):
        self.calls = []

    async def register_uploaded_files(self, files, normalized_uploader):
        self.calls.append((list(files), normalized_uploader))
        return [1, 2]


class FakeTaskState:
    def __init__(self):
        self.metadata = None

    async def create_task(self, task_type, metadata):
        self.metadata = metadata
        return "task-1"


class FakeTaskExecution:
    def __init__(self):
        self.owners = []

    def set_task_owner(self, task_id, owner_id):
        self.owners.append((task_id, owner_id))


class FakeWorker:
    def __init__(self):
        self.calls = []

    def submit_process_documents(self, task_id, file_ids, collection, chunk_size, chunk_overlap):
        self.calls.append((task_id, list(file_ids), collection, chunk_size, chunk_overlap))


@pytest.mark.asyncio
async def test_submit_document_processing_passes_collection_through():
    from app.ports.contracts.identity import CurrentUserPort  # noqa: F401  (类型引用)
    from app.usecases.document_processing.submit import (
        SubmitDocumentProcessingCommand,
        SubmitDocumentProcessingUseCase,
    )

    registration = FakeDocumentRegistration()
    task_state = FakeTaskState()
    task_execution = FakeTaskExecution()
    worker = FakeWorker()
    user = _admin()

    cmd = SubmitDocumentProcessingCommand(
        files=["f1", "f2"],
        collection="knowledge_chunks",
        chunk_size=500,
        chunk_overlap=50,
        normalized_uploader="admin",
        current_user=user,
    )
    result = await SubmitDocumentProcessingUseCase(
        registration=registration,
        task_state=task_state,
        task_execution=task_execution,
        worker=worker,
    ).execute(cmd)

    assert result.task_id == "task-1"
    assert result.files_count == 2
    # 任务元数据记录语义集合名
    assert task_state.metadata["collection"] == "knowledge_chunks"
    assert "instance_id" not in task_state.metadata
    # worker 收到语义集合名（写入 data_<collection> 向量表）
    assert worker.calls == [("task-1", [1, 2], "knowledge_chunks", 500, 50)]


@pytest.mark.asyncio
async def test_submit_document_processing_requires_files():
    from app.core.exceptions import ValidationError as AppValidationError
    from app.usecases.document_processing.submit import (
        SubmitDocumentProcessingCommand,
        SubmitDocumentProcessingUseCase,
    )

    cmd = SubmitDocumentProcessingCommand(
        files=[],
        collection="knowledge_chunks",
        chunk_size=500,
        chunk_overlap=50,
        normalized_uploader="admin",
        current_user=_admin(),
    )
    with pytest.raises(AppValidationError):
        await SubmitDocumentProcessingUseCase(
            registration=FakeDocumentRegistration(),
            task_state=FakeTaskState(),
            task_execution=FakeTaskExecution(),
            worker=FakeWorker(),
        ).execute(cmd)


# ---------------------------------------------------------------------------
# 5. 向量写入表名语义化（需要 torch / llama_index，缺失时跳过）
# ---------------------------------------------------------------------------

def test_vector_store_manager_uses_semantic_collection_as_table_name(monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("llama_index")
    from app.adapters.doc_processing import embedding_store

    captured: dict = {}

    def fake_from_params(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(embedding_store.PGVectorStore, "from_params", staticmethod(fake_from_params))

    manager = embedding_store.VectorStoreManager(
        db_config={"database": "db", "host": "h", "password": "p", "port": 5432, "user": "u"}
    )
    manager._build_vector_store("knowledge_chunks")
    # 传逻辑表名 knowledge_chunks（PGVector 自动映射物理表 data_knowledge_chunks）
    assert captured["table_name"] == "knowledge_chunks"

    manager._build_vector_store("doc_collection_1")
    assert captured["table_name"] == "doc_collection_1"


def test_pipeline_nodes_carry_collection_metadata():
    pytest.importorskip("llama_index")

    from app.adapters.doc_processing.pipeline import DocumentProcessingPipeline

    class _Chunk:
        """鸭子类型：doc_reader 产出的 chunk 带 page_content / metadata。"""

        def __init__(self, page_content, metadata):
            self.page_content = page_content
            self.metadata = metadata

    # object.__new__ 绕过 __init__（避免加载分割器 / 嵌入模型），_documents_to_nodes 不依赖实例状态
    pipeline = object.__new__(DocumentProcessingPipeline)
    nodes = pipeline._documents_to_nodes(
        [_Chunk("hello\x00world", {"source": "a.pdf"})],
        "knowledge_chunks",
    )
    assert len(nodes) == 1
    assert nodes[0].metadata["collection"] == "knowledge_chunks"
    assert "instance_id" not in nodes[0].metadata
    assert "\x00" not in nodes[0].text  # NUL 清理仍生效
    assert nodes[0].metadata["source"] == "a.pdf"


def test_closing_form_embedding_still_writes_doc_collection_1(monkeypatch):
    """验收标准：data_doc_collection_1 维持现状（closing_form 删除前仍由其写入）。"""
    pytest.importorskip("torch")
    pytest.importorskip("llama_index")
    from app.adapters.closing_form import embedding as closing_form_embedding

    captured: dict = {}

    class FakeVectorStoreManager:
        def __init__(self, db_config=None, **kwargs):
            captured["init_kwargs"] = kwargs

        def upsert_chunks(self, chunks, collection_name, embedding_model):
            captured["collection_name"] = collection_name
            captured["chunks"] = chunks

    # closing_form.embedding 通过 from-import 持有名字绑定，必须 patch 其自身命名空间
    monkeypatch.setattr(closing_form_embedding, "VectorStoreManager", FakeVectorStoreManager)
    monkeypatch.setattr(closing_form_embedding, "BGEM3EmbeddingWrapper", lambda: object())

    closing_form_embedding.ClosingFormEmbeddingAdapter().upsert_approved_form(
        text="表单文本",
        uploader="alice",
        upload_time="2026-01-01 00:00:00",
    )
    assert captured["collection_name"] == "doc_collection_1"
