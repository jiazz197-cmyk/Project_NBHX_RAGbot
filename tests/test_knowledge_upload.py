"""Knowledge upload regression tests (issue #3).

覆盖文档 / Excel 上传链路的关键行为（fake port / monkeypatch，无外部服务）：
  1. 白名单与大小上限校验（422）
  2. 同名冲突预检（409 + 摘要）与 replace / append 语义
  3. collection 定向（knowledge_chunks / excel_db_chunks）与固定 chunk 参数
  4. ExcelParser 多 sheet 解析与 process_document 的 sheet_name metadata
     （pandas / langchain_core 缺失时自动跳过，其余测试不受影响）
  5. 配置化大小上限与 domain 常量一致性
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import (
    KnowledgeFileNameConflictError,
    ValidationError,
)
from app.domain.knowledge.collections import (
    EXCEL_DB_COLLECTION_NAME,
    KNOWLEDGE_COLLECTION_NAME,
)
from app.ports.dto.knowledge_upload import (
    ExcelDbUploadCommand,
    KnowledgeFileConflict,
    KnowledgeUploadCommand,
)
from app.usecases.knowledge.upload import (
    UploadExcelDbUseCase,
    UploadKnowledgeDocumentUseCase,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

MB = 1024 * 1024


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeFile:
    """支持 seek/tell 的伪文件对象（避免真实分配 50MB 缓冲）。"""

    def __init__(self, size: int):
        self._size = size
        self._pos = 0

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = self._size + offset
        return self._pos


class FakeUploadFile:
    def __init__(self, filename: str, size: int = 1024):
        self.filename = filename
        self.file = FakeFile(size)


class FakeUser:
    def __init__(self, username: str = "alice"):
        self.username = username


class FakeMetadataPort:
    """内存版 KnowledgeMetadataPort。"""

    def __init__(self, conflicts=None):
        self._conflicts = conflicts or {}
        self.deleted: list = []
        self.find_calls: list = []

    async def find_conflict(self, collection, file_name):
        self.find_calls.append((collection, file_name))
        return self._conflicts.get(file_name)

    async def delete_chunks_by_file_name(self, collection, file_name) -> int:
        self.deleted.append((collection, file_name))
        return 3


class FakeSubmitUseCase:
    """记录 SubmitDocumentProcessingCommand 并返回可控结果。"""

    def __init__(self, task_id: str = "task-1"):
        self.task_id = task_id
        self.commands = []

    async def execute(self, cmd):
        self.commands.append(cmd)
        return SimpleNamespace(
            task_id=self.task_id,
            status="pending",
            message="ok",
            files_count=len(cmd.files),
        )


_UNSET = object()


def _doc_cmd(files, on_conflict=None, user=_UNSET):
    return KnowledgeUploadCommand(
        files=files,
        on_conflict=on_conflict,
        current_user=FakeUser() if user is _UNSET else user,
    )


def _excel_cmd(files, on_conflict=None, user=_UNSET):
    return ExcelDbUploadCommand(
        files=files,
        on_conflict=on_conflict,
        current_user=FakeUser() if user is _UNSET else user,
    )


def _conflict(file_name: str) -> KnowledgeFileConflict:
    return KnowledgeFileConflict(
        file_name=file_name,
        uploader="bob",
        upload_time="2026-01-01 10:00:00",
        chunk_count=5,
    )


def _two_sheet_tables():
    return [
        {"headers": ["h1"], "rows": [["v1"]], "sheet_name": "s1"},
        {"headers": ["h2"], "rows": [["v2"]], "sheet_name": "s2"},
    ]


# ---------------------------------------------------------------------------
# 1. 白名单 / 大小上限校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_upload_rejects_excel_file_with_hint():
    use_case = UploadKnowledgeDocumentUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_doc_cmd([FakeUploadFile("sales.xlsx")]))
    assert exc_info.value.status_code == 422
    assert "Excel" in exc_info.value.message


@pytest.mark.asyncio
async def test_document_upload_rejects_unknown_extension():
    use_case = UploadKnowledgeDocumentUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_doc_cmd([FakeUploadFile("data.exe")]))
    assert exc_info.value.status_code == 422
    assert "不支持" in exc_info.value.message


@pytest.mark.asyncio
async def test_excel_db_upload_rejects_document_file():
    use_case = UploadExcelDbUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_excel_cmd([FakeUploadFile("doc.pdf")]))
    assert exc_info.value.status_code == 422
    assert "xlsx" in exc_info.value.message


@pytest.mark.asyncio
async def test_document_upload_oversize_rejected():
    use_case = UploadKnowledgeDocumentUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_doc_cmd([FakeUploadFile("big.pdf", 50 * MB + 1)]))
    assert exc_info.value.status_code == 422
    assert "50MB" in exc_info.value.message


@pytest.mark.asyncio
async def test_excel_db_upload_oversize_rejected():
    use_case = UploadExcelDbUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_excel_cmd([FakeUploadFile("db.xlsx", 20 * MB + 1)]))
    assert exc_info.value.status_code == 422
    assert "20MB" in exc_info.value.message


@pytest.mark.asyncio
async def test_empty_files_rejected():
    use_case = UploadKnowledgeDocumentUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_doc_cmd([]))
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_invalid_on_conflict_rejected():
    use_case = UploadKnowledgeDocumentUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_doc_cmd([FakeUploadFile("a.pdf")], on_conflict="overwrite"))
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_upload_requires_current_user():
    use_case = UploadKnowledgeDocumentUseCase(FakeSubmitUseCase(), FakeMetadataPort())
    with pytest.raises(ValidationError) as exc_info:
        await use_case.execute(_doc_cmd([FakeUploadFile("a.pdf")], user=None))
    assert exc_info.value.status_code == 422
    assert "用户" in exc_info.value.message


# ---------------------------------------------------------------------------
# 2. 同名冲突（409）与 replace / append 语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_name_conflict_raises_409_with_summary():
    metadata = FakeMetadataPort(conflicts={"a.pdf": _conflict("a.pdf")})
    submit = FakeSubmitUseCase()
    use_case = UploadKnowledgeDocumentUseCase(submit, metadata)

    with pytest.raises(KnowledgeFileNameConflictError) as exc_info:
        await use_case.execute(_doc_cmd([FakeUploadFile("a.pdf")]))

    err = exc_info.value
    assert err.status_code == 409
    assert err.error_code == "KNOWLEDGE_FILE_NAME_CONFLICT"
    assert err.details == {
        "file_name": "a.pdf",
        "uploader": "bob",
        "upload_time": "2026-01-01 10:00:00",
        "chunk_count": 5,
    }
    # 冲突时不得提交任务
    assert submit.commands == []


@pytest.mark.asyncio
async def test_replace_deletes_old_chunks_then_submits():
    metadata = FakeMetadataPort(conflicts={"a.pdf": _conflict("a.pdf")})
    submit = FakeSubmitUseCase(task_id="task-replace")
    use_case = UploadKnowledgeDocumentUseCase(submit, metadata)

    result = await use_case.execute(_doc_cmd([FakeUploadFile("a.pdf")], on_conflict="replace"))

    assert metadata.deleted == [(KNOWLEDGE_COLLECTION_NAME, "a.pdf")]
    assert len(submit.commands) == 1
    cmd = submit.commands[0]
    assert cmd.collection == KNOWLEDGE_COLLECTION_NAME
    assert cmd.chunk_size == 500
    assert cmd.chunk_overlap == 50
    assert cmd.normalized_uploader == "alice"
    assert result.collection == KNOWLEDGE_COLLECTION_NAME
    assert result.task_id == "task-replace"


@pytest.mark.asyncio
async def test_append_keeps_old_chunks_and_submits():
    metadata = FakeMetadataPort(conflicts={"a.pdf": _conflict("a.pdf")})
    submit = FakeSubmitUseCase()
    use_case = UploadKnowledgeDocumentUseCase(submit, metadata)

    await use_case.execute(_doc_cmd([FakeUploadFile("a.pdf")], on_conflict="append"))

    assert metadata.deleted == []
    assert len(submit.commands) == 1


@pytest.mark.asyncio
async def test_excel_db_upload_targets_excel_collection():
    metadata = FakeMetadataPort()
    submit = FakeSubmitUseCase(task_id="task-excel")
    use_case = UploadExcelDbUseCase(submit, metadata)

    result = await use_case.execute(_excel_cmd([FakeUploadFile("db.xlsx")]))

    assert submit.commands[0].collection == EXCEL_DB_COLLECTION_NAME
    assert result.collection == EXCEL_DB_COLLECTION_NAME
    assert result.task_id == "task-excel"
    assert metadata.find_calls == [(EXCEL_DB_COLLECTION_NAME, "db.xlsx")]


# ---------------------------------------------------------------------------
# 3. Excel 多 sheet 解析（pandas 缺失时跳过）
# ---------------------------------------------------------------------------


def test_excel_parser_reads_all_sheets(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    from app.adapters.doc_processing.doc_reader import ExcelParser

    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"产品": ["A", "B"], "数量": [1, 2]}).to_excel(
            writer, sheet_name="产品表", index=False
        )
        pd.DataFrame({"编码": ["X"]}).to_excel(writer, sheet_name="元数据", index=False)

    _, tables = ExcelParser()(str(path), sheet_idx=None)

    by_name = {table["sheet_name"]: table for table in tables}
    assert set(by_name) == {"产品表", "元数据"}
    assert by_name["产品表"]["headers"] == ["产品", "数量"]
    assert [list(row) for row in by_name["产品表"]["rows"]] == [["A", 1], ["B", 2]]


def test_excel_parser_default_reads_single_sheet(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    from app.adapters.doc_processing.doc_reader import ExcelParser

    path = tmp_path / "multi.xlsx"
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"b": [2]}).to_excel(writer, sheet_name="second", index=False)

    _, tables = ExcelParser()(str(path))

    # 默认向后兼容：只读第一个 sheet，且不带 sheet_name 键
    assert len(tables) == 1
    assert "sheet_name" not in tables[0]


# ---------------------------------------------------------------------------
# 4. process_document 多 sheet metadata（langchain_core 缺失时跳过）
# ---------------------------------------------------------------------------


def test_process_document_all_sheets_writes_sheet_name_metadata():
    pytest.importorskip("langchain_core")
    from app.adapters.doc_processing.doc_reader import DocumentProcessor

    class FakeParser:
        def __init__(self):
            self.sheet_calls = []

        def __call__(self, file_input, sheet_idx=0):
            self.sheet_calls.append(sheet_idx)
            return "raw", _two_sheet_tables()

    class FakeSplitter:
        def split_excel_data(self, table):
            return [f"{table.get('sheet_name', 'default')}-r{i}" for i in range(2)]

        def count_tokens(self, text):
            return 1

    processor = DocumentProcessor()
    fake_parser = FakeParser()
    processor._get_parser = lambda ext: fake_parser  # type: ignore[method-assign]
    processor.extract_metadata = lambda file_input, text: {"file_name": "multi.xlsx"}  # type: ignore[method-assign]

    buf = io.BytesIO(b"x")
    buf.name = "multi.xlsx"
    chunks = processor.process_document(
        buf, None, excel_splitter=FakeSplitter(), excel_all_sheets=True
    )

    assert fake_parser.sheet_calls == [None]
    assert len(chunks) == 4
    assert {c.metadata["sheet_name"] for c in chunks} == {"s1", "s2"}
    assert {c.metadata["table_index"] for c in chunks} == {0, 1}
    assert all(c.metadata["split_method"] == "excel_header_preserving" for c in chunks)


def test_process_document_single_sheet_keeps_backward_compat():
    pytest.importorskip("langchain_core")
    from app.adapters.doc_processing.doc_reader import DocumentProcessor

    class FakeParser:
        def __init__(self):
            self.sheet_calls = []

        def __call__(self, file_input, sheet_idx=0):
            self.sheet_calls.append(sheet_idx)
            return "raw", [{"headers": ["h"], "rows": [["v"]]}]

    class FakeSplitter:
        def split_excel_data(self, table):
            return ["r0", "r1"]

        def count_tokens(self, text):
            return 1

    processor = DocumentProcessor()
    fake_parser = FakeParser()
    processor._get_parser = lambda ext: fake_parser  # type: ignore[method-assign]
    processor.extract_metadata = lambda file_input, text: {"file_name": "multi.xlsx"}  # type: ignore[method-assign]

    buf = io.BytesIO(b"x")
    buf.name = "multi.xlsx"
    chunks = processor.process_document(
        buf, None, excel_splitter=FakeSplitter()
    )

    assert fake_parser.sheet_calls == [0]
    assert len(chunks) == 2
    assert all("sheet_name" not in c.metadata for c in chunks)


# ---------------------------------------------------------------------------
# 5. 配置与结构一致性
# ---------------------------------------------------------------------------


def test_settings_size_defaults_match_domain_constants():
    from app.core.config import settings
    from app.domain.knowledge.upload_rules import (
        MAX_DOCUMENT_FILE_SIZE_BYTES,
        MAX_EXCEL_FILE_SIZE_BYTES,
    )

    assert (
        settings.KNOWLEDGE_MAX_DOCUMENT_FILE_SIZE_MB
        == MAX_DOCUMENT_FILE_SIZE_BYTES // MB
    )
    assert (
        settings.KNOWLEDGE_MAX_EXCEL_FILE_SIZE_MB
        == MAX_EXCEL_FILE_SIZE_BYTES // MB
    )


def test_pipeline_enables_all_sheets_for_excel_db_collection():
    """Excel 多 sheet 由 collection 驱动（usecase / worker 签名零改动）。"""
    source = (REPO_ROOT / "app/adapters/doc_processing/pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "excel_all_sheets" in source
    assert "EXCEL_DB_COLLECTION_NAME" in source


def test_env_example_documents_upload_limits():
    source = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "KNOWLEDGE_MAX_DOCUMENT_FILE_SIZE_MB=50" in source
    assert "KNOWLEDGE_MAX_EXCEL_FILE_SIZE_MB=20" in source


# ---------------------------------------------------------------------------
# 6. 接口分家：/retriever/db ↔ 文档集合，/retriever/excel ↔ Excel 集合
# ---------------------------------------------------------------------------


def test_retriever_allowlists_split_by_interface():
    """settings 默认值即接口分家：db 仅文档集合，excel 仅 Excel 集合。"""
    from app.core.config import settings

    assert settings.RETRIEVER_ALLOWED_DOCUMENT_COLLECTIONS == ["knowledge_chunks"]
    assert settings.RETRIEVER_ALLOWED_EXCEL_COLLECTIONS == ["excel_db_chunks"]


def _plain_user(role: str = "user"):
    return SimpleNamespace(role=role)


def test_collection_access_db_rejects_excel_collection():
    from fastapi import HTTPException
    from app.api.v1 import retriever

    with pytest.raises(HTTPException) as exc_info:
        retriever._ensure_collection_access(
            "excel_db_chunks", _plain_user(), ["knowledge_chunks"]
        )
    assert exc_info.value.status_code == 403


def test_collection_access_excel_rejects_document_collection():
    from fastapi import HTTPException
    from app.api.v1 import retriever

    with pytest.raises(HTTPException) as exc_info:
        retriever._ensure_collection_access(
            "knowledge_chunks", _plain_user(), ["excel_db_chunks"]
        )
    assert exc_info.value.status_code == 403


def test_collection_access_accepts_matching_collection():
    from app.api.v1 import retriever

    assert (
        retriever._ensure_collection_access("knowledge_chunks", _plain_user(), ["knowledge_chunks"])
        == "knowledge_chunks"
    )
    # 白名单兼容 data_ 前缀写法
    assert (
        retriever._ensure_collection_access("excel_db_chunks", _plain_user(), ["data_excel_db_chunks"])
        == "excel_db_chunks"
    )


def test_collection_access_superuser_bypasses_split_allowlists():
    from app.api.v1 import retriever

    assert retriever._ensure_collection_access("anything", _plain_user("superuser"), []) == "anything"
