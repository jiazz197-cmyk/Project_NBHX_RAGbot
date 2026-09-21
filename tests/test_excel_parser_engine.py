"""Excel 解析引擎回归测试（issue15）。

背景：腾讯文档 / WPS 导出的 xlsx 会在 styles.xml 的 <fills> 里写自闭合的空
``<fill/>``，openpyxl 3.1.5 解析 stylesheet 时抛
``TypeError: Fill() takes no arguments``（外层为 expected Fill），
导致上传任务失败、无 chunk 产出（2026-09-17 实测批次 8 个文件 3 个中招，
生产者为 "Tencent office"）。

修复：ExcelParser 经 ``_read_excel_frames`` 优先用 calamine 引擎
（Rust，只读数据不读样式，天然免疫），失败回退 pandas 默认引擎；
双引擎都失败时抛带明确指引的 DocumentParseError，并由 pipeline /
task_runner 把原因反馈到任务状态（不再仅日志可见）。

CI 说明：doc_reader / pipeline 的 import 链含 langchain_core /
llama_index 等重依赖，CI pytest job 不装它们——沿用
test_knowledge_upload.py 的 importorskip 模式：CI 里跳过，dev 容器全量跑。
"""

from __future__ import annotations

import contextlib
import io
import re
import zipfile

import pytest


# ---------------------------------------------------------------------------
# 辅助：构造「正常 xlsx」与「坏样式 xlsx」（复刻腾讯文档导出的空 <fill/>）
# ---------------------------------------------------------------------------


def _write_base_xlsx(path) -> None:
    pd = pytest.importorskip("pandas")
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"产品": ["A", "B", "C"], "数量": [1.5, 42, 7]}).to_excel(
            writer, sheet_name="产品表", index=False
        )
        pd.DataFrame({"编码": ["X", "Y"]}).to_excel(
            writer, sheet_name="元数据", index=False
        )


def _break_stylesheet(src, dst) -> None:
    """把第一个 <fill>...</fill> 替换为自闭合空 <fill/>（腾讯文档行为）。

    openpyxl 的 Fill.from_tree 对无子元素的 <fill> 返回 None，随后
    _convert(Fill, None) 调 Fill(None) → TypeError: Fill() takes no arguments。
    """
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        styles = zin.read("xl/styles.xml").decode("utf-8")

    match = re.search(r"<fill>.*?</fill>", styles, flags=re.S)
    assert match, "基准文件缺少 fill 节点（openpyxl 默认样式必含 2 个）"
    broken = styles[: match.start()] + "<fill/>" + styles[match.end() :]

    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(
        dst, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for name in names:
            zout.writestr(name, broken if name == "xl/styles.xml" else zin.read(name))


# ---------------------------------------------------------------------------
# 1. calamine 主路径：坏样式文件也能解析
# ---------------------------------------------------------------------------


def test_broken_stylesheet_xlsx_parses_with_calamine(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    pytest.importorskip("langchain_core")
    from app.adapters.doc_processing.doc_reader import ExcelParser

    base = tmp_path / "base.xlsx"
    broken = tmp_path / "broken.xlsx"
    _write_base_xlsx(base)
    _break_stylesheet(base, broken)

    # 前置 sanity：默认 openpyxl 引擎确实解析不了这个文件（锁定根因）
    with pytest.raises(TypeError, match="Fill"):
        pd.read_excel(broken, sheet_name=None, header=None)

    # 修复后：ExcelParser 走 calamine 引擎，正常出全部 sheet
    text, tables = ExcelParser()(str(broken), sheet_idx=None)

    by_name = {t["sheet_name"]: t for t in tables}
    assert set(by_name) == {"产品表", "元数据"}
    assert "42" in text
    # 空单元格不应泄漏成 "nan" 字符串
    assert "nan" not in text.replace("nanning", "")


def test_normal_xlsx_still_parses(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    pytest.importorskip("langchain_core")
    from app.adapters.doc_processing.doc_reader import ExcelParser

    path = tmp_path / "normal.xlsx"
    _write_base_xlsx(path)

    text, tables = ExcelParser()(str(path), sheet_idx=None)

    assert len(tables) == 2
    assert tables[0]["headers"] == ["产品", "数量"]
    assert "1.5" in text and "42" in text


# ---------------------------------------------------------------------------
# 2. 双引擎兜底：损坏文件 → 带指引的 DocumentParseError
# ---------------------------------------------------------------------------


def test_corrupt_file_raises_friendly_document_parse_error(tmp_path):
    pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    pytest.importorskip("langchain_core")
    from app.adapters.doc_processing.doc_reader import ExcelParser
    from app.adapters.doc_processing.exceptions import DocumentParseError

    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(b"this is definitely not an excel file")

    with pytest.raises(DocumentParseError) as excinfo:
        ExcelParser()(str(path))

    message = str(excinfo.value)
    assert "calamine/openpyxl 双引擎" in message
    assert "重新另存" in message


# ---------------------------------------------------------------------------
# 3. calamine 不可用（旧镜像未重建）→ 回退默认引擎，行为不劣于修复前
# ---------------------------------------------------------------------------


def _make_calamine_unavailable(monkeypatch):
    pd = pytest.importorskip("pandas")
    real_read_excel = pd.read_excel

    def fake_read_excel(*args, **kwargs):
        if kwargs.get("engine") == "calamine":
            raise ImportError("python_calamine is required for calamine engine")
        kwargs.pop("engine", None)
        return real_read_excel(*args, **kwargs)

    monkeypatch.setattr(pd, "read_excel", fake_read_excel)


def test_fallback_to_default_engine_when_calamine_missing(tmp_path, monkeypatch):
    _make_calamine_unavailable(monkeypatch)
    from app.adapters.doc_processing.doc_reader import ExcelParser

    path = tmp_path / "normal.xlsx"
    _write_base_xlsx(path)

    # calamine 缺席时正常文件仍可经 openpyxl 解析
    _, tables = ExcelParser()(str(path), sheet_idx=None)
    assert len(tables) == 2


def test_fallback_failure_surfaces_friendly_error(tmp_path, monkeypatch):
    _make_calamine_unavailable(monkeypatch)
    from app.adapters.doc_processing.doc_reader import ExcelParser
    from app.adapters.doc_processing.exceptions import DocumentParseError

    base = tmp_path / "base.xlsx"
    broken = tmp_path / "broken.xlsx"
    _write_base_xlsx(base)
    _break_stylesheet(base, broken)

    # calamine 缺席 + 坏样式 → openpyxl 也失败 → 明确报错（而不是裸 TypeError）
    with pytest.raises(DocumentParseError) as excinfo:
        ExcelParser()(str(broken))
    assert "calamine/openpyxl 双引擎" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. 失败原因反馈链：pipeline 聚合 failed_files、task_runner 摘要
# ---------------------------------------------------------------------------


def test_pipeline_collects_failed_files(monkeypatch):
    pytest.importorskip("langchain_core")
    pytest.importorskip("llama_index")
    from app.adapters.doc_processing.pipeline import DocumentProcessingPipeline
    from app.adapters.doc_processing.exceptions import DocumentParseError

    class BoomProcessor:
        def process_document(self, *args, **kwargs):
            raise DocumentParseError(
                "Excel 解析失败（已尝试 calamine/openpyxl 双引擎）: boom"
            )

    class NoopVectorStore:
        def upsert_chunks(self, *args, **kwargs):
            calls.append(args[0])
            return len(calls)

        def existing_fingerprints(self, *args, **kwargs):
            # issue #17：写入端会先做内容指纹预检；本用例只关心失败聚合
            return set()

        def fingerprint_write_guard(self, *args, **kwargs):
            # issue #17：预检 + 写入在 advisory lock 临界区内，这里用空上下文替代
            return contextlib.nullcontext()

    calls: list = []
    # 只测聚合逻辑，不起真实 embedding / tokenizer：绕过 __init__ 手工装配
    pipe = object.__new__(DocumentProcessingPipeline)
    pipe.text_splitter = None
    pipe.tag_generator = None
    pipe.num_tags = 5
    pipe.excel_splitter = None
    pipe.document_processor = BoomProcessor()
    pipe.vector_store_manager = NoopVectorStore()

    stream = io.BytesIO(b"fake-bytes")
    stream.name = "bad.xlsx"
    result = pipe.process([stream], collection="documents")

    # issue15 兜底：失败不再被静默吞掉，逐文件登记并随结果返回
    assert result["processed_files"] == 0
    assert result["total_files"] == 1
    assert result["failed_files"] == [
        {
            "file_name": "bad.xlsx",
            "error": "Excel 解析失败（已尝试 calamine/openpyxl 双引擎）: boom",
        }
    ]
    assert calls == []  # 失败文件不应触发向量化写入


def test_pipeline_partial_failure_keeps_successes(monkeypatch):
    pytest.importorskip("langchain_core")
    pytest.importorskip("llama_index")
    from types import SimpleNamespace

    from app.adapters.doc_processing.pipeline import DocumentProcessingPipeline
    from app.adapters.doc_processing.exceptions import DocumentParseError

    class FlakyProcessor:
        def __init__(self):
            self.calls = 0

        def process_document(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return [SimpleNamespace(page_content="ok", metadata={})]
            raise DocumentParseError("第二个文件炸了")

    class NoopVectorStore:
        def upsert_chunks(self, *args, **kwargs):
            return 1

        def existing_fingerprints(self, *args, **kwargs):
            return set()

        def fingerprint_write_guard(self, *args, **kwargs):
            return contextlib.nullcontext()

    pipe = object.__new__(DocumentProcessingPipeline)
    pipe.text_splitter = None
    pipe.tag_generator = None
    pipe.num_tags = 5
    pipe.excel_splitter = None
    pipe.embedding_model = None
    pipe.document_processor = FlakyProcessor()
    pipe.vector_store_manager = NoopVectorStore()

    good = io.BytesIO(b"good")
    good.name = "good.xlsx"
    bad = io.BytesIO(b"bad")
    bad.name = "bad.xlsx"

    result = pipe.process([good, bad], collection="documents")

    assert result["processed_files"] == 1
    assert result["failed_files"] == [
        {"file_name": "bad.xlsx", "error": "第二个文件炸了"}
    ]


def test_summarize_failed_files():
    pytest.importorskip("tenacity")
    from app.adapters.doc_processing.document_task_runner import _summarize_failed_files

    assert _summarize_failed_files([]) == ""

    summary = _summarize_failed_files([{"file_name": "a.xlsx", "error": "boom"}])
    assert "a.xlsx" in summary and "boom" in summary

    many = [{"file_name": f"f{i}.xlsx", "error": "err"} for i in range(5)]
    summary = _summarize_failed_files(many)
    assert "f0.xlsx" in summary and "f2.xlsx" in summary
    assert "共 5 个文件失败" in summary
    assert "f4.xlsx" not in summary  # 超出前 3 个只报总数，避免消息过长
