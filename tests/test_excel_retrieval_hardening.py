"""表格检索加固回归测试（2026-09-18 线上问题）。

背景：用户问“项目 V254 (GLC) 的负责人是谁？查查表”时
  1) 意图被误判为 general → 本地检索整体跳过；
  2) 即使调 /retriever/excel，openpyxl 也打不开 3/8 个 xlsx（styles.xml 空 <fill/>），
     只返回错误串；
  3) 表里有空 chunk，重排网关对空文档返回 400 → 静默降级原始顺序；
  4) 写入端切割器会产出空 chunk。

覆盖面：
  1. excel_to_json：calamine 优先，WPS/腾讯文档坏样式文件可解析；calamine 缺席回退 openpyxl；
  2. prepare_rerank_documents + HTTPReranker：空文档占位、按预算截断、下标不错位；
  3. ExcelHeaderPreservingSplitter：空表/全空行不产空 chunk；
  4. OptimizedRetriever.get_chunks：过滤历史空 chunk。

doc_reader / pipeline 的 import 链含 langchain_core / llama_index 等重依赖，
CI 不装它们——沿用 test_excel_parser_engine.py 的 importorskip 模式。
"""

from __future__ import annotations

import json
import re
import zipfile

import pytest


# ---------------------------------------------------------------------------
# 辅助：构造「正常 xlsx」与「坏样式 xlsx」（复刻腾讯文档导出的空 <fill/>）
# ---------------------------------------------------------------------------


def _write_base_xlsx(path) -> None:
    """复刻 excel_to_json 的预期版式：1 行标题 + 2 行表头 + 数据行。"""
    pd = pytest.importorskip("pandas")
    rows = [
        ["项目利润表总览", None],
        ["项目名称", "负责人"],
        ["", ""],
        ["V254 (GLC)", "洪鑫浩"],
    ]
    pd.DataFrame(rows).to_excel(path, sheet_name="项目表", index=False, header=False)


def _break_stylesheet(src, dst) -> None:
    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()
        styles = zin.read("xl/styles.xml").decode("utf-8")
    match = re.search(r"<fill>.*?</fill>", styles, flags=re.S)
    assert match, "基准文件缺少 fill 节点"
    broken = styles[: match.start()] + "<fill/>" + styles[match.end() :]
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(
        dst, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for name in names:
            zout.writestr(name, broken if name == "xl/styles.xml" else zin.read(name))


# ---------------------------------------------------------------------------
# 1. excel_to_json 引擎策略
# ---------------------------------------------------------------------------


def test_excel_to_json_reads_wps_broken_stylesheet(tmp_path):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("openpyxl")
    from app.adapters.ragsystem.data_analyze import excel_to_json

    base = tmp_path / "base.xlsx"
    broken = tmp_path / "broken.xlsx"
    _write_base_xlsx(base)
    _break_stylesheet(base, broken)

    # 前置 sanity：默认 openpyxl 引擎确实读不了（锁定根因）
    with pytest.raises(TypeError, match="Fill"):
        pd.ExcelFile(broken, engine="openpyxl")

    payload = json.loads(excel_to_json(str(broken)))

    assert payload["sheet_name"] == "项目利润表总览"
    assert payload["headers"] == ["项目名称", "负责人"]
    assert payload["rows"] == [{"项目名称": "V254 (GLC)", "负责人": "洪鑫浩"}]


def test_excel_to_json_prefers_calamine_engine(tmp_path, monkeypatch):
    """正常文件也必须先试 calamine —— 否则坏样式文件永远走不到可用引擎。"""
    pd = pytest.importorskip("pandas")
    real_excel_file = pd.ExcelFile
    engines = []

    def spy_excel_file(path, *args, **kwargs):
        engines.append(kwargs.get("engine"))
        return real_excel_file(path, *args, **kwargs)

    monkeypatch.setattr(pd, "ExcelFile", spy_excel_file)

    from app.adapters.ragsystem.data_analyze import excel_to_json

    path = tmp_path / "normal.xlsx"
    _write_base_xlsx(path)
    excel_to_json(str(path))

    assert engines and engines[0] == "calamine"


def test_excel_to_json_falls_back_when_calamine_missing(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    real_excel_file = pd.ExcelFile

    def fake_excel_file(path, *args, **kwargs):
        if kwargs.get("engine") == "calamine":
            raise ImportError("python_calamine is required")
        kwargs.pop("engine", None)
        return real_excel_file(path, *args, **kwargs)

    monkeypatch.setattr(pd, "ExcelFile", fake_excel_file)

    from app.adapters.ragsystem.data_analyze import excel_to_json

    path = tmp_path / "normal.xlsx"
    _write_base_xlsx(path)

    payload = json.loads(excel_to_json(str(path)))
    assert payload["rows"] == [{"项目名称": "V254 (GLC)", "负责人": "洪鑫浩"}]


# ---------------------------------------------------------------------------
# 2. 重排入参清洗
# ---------------------------------------------------------------------------


def test_prepare_rerank_documents_placeholders_per_doc_cap_and_order():
    pytest.importorskip("llama_index")
    from app.adapters.ragsystem.RAGretriever import prepare_rerank_documents

    documents = ["", "   ", "正常内容" * 3000]
    prepared = prepare_rerank_documents(documents)

    # 长度与顺序不变（重排返回的 index 是对入参下标，不能丢元素）
    assert len(prepared) == len(documents)
    assert prepared[0] and prepared[0].strip()
    assert prepared[1] and prepared[1].strip()
    # 单条上限 6000 字符（≈4000 token，网关限制是按单条的 8192 token）
    assert len(prepared[2]) == 6000

    # 上限按**条**算：10 条 5000 字符不会被均分成 600 字符（均分会截掉答案行）
    many = ["长" * 5000 for _ in range(10)]
    prepared_many = prepare_rerank_documents(many)
    assert all(text == "长" * 5000 for text in prepared_many)

    assert prepare_rerank_documents([]) == []


def test_http_reranker_sanitizes_documents_before_request(monkeypatch):
    pytest.importorskip("llama_index")
    import httpx
    from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

    from app.adapters.ragsystem.RAGretriever import HTTPReranker

    captured = {}

    def fake_request(self, query_str, documents):
        captured["query"] = query_str
        captured["documents"] = list(documents)
        return {"results": [{"index": 0, "relevance_score": 1.0}]}

    monkeypatch.setattr(HTTPReranker, "_rerank_request_sync", fake_request)

    reranker = HTTPReranker(api_url="http://rerank.test/v1/rerank", top_n=1, timeout=5)
    nodes = [
        NodeWithScore(node=TextNode(text=""), score=None),
        NodeWithScore(node=TextNode(text="  "), score=None),
        NodeWithScore(node=TextNode(text="命中"), score=None),
    ]

    result = reranker._postprocess_nodes(nodes, QueryBundle(query_str="q"))

    assert len(captured["documents"]) == len(nodes)
    assert all(str(doc).strip() for doc in captured["documents"])
    assert captured["documents"][2] == "命中"
    assert result == [nodes[0]]  # top_n=1，index 映射仍指向原 nodes

    # 网关 400（httpx.HTTPError）→ 退回截断后的原始节点，不抛异常
    def boom(self, query_str, documents):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(HTTPReranker, "_rerank_request_sync", boom)
    assert reranker._postprocess_nodes(nodes, QueryBundle(query_str="q")) == nodes[:1]


# ---------------------------------------------------------------------------
# 3. 切割器不产空 chunk（写入端根因）
# ---------------------------------------------------------------------------


def _stub_splitter():
    pytest.importorskip("transformers")
    pytest.importorskip("langchain_text_splitters")
    from app.adapters.doc_processing.text_splitter import ExcelHeaderPreservingSplitter

    splitter = object.__new__(ExcelHeaderPreservingSplitter)
    splitter.chunk_size = 500
    splitter.chunk_overlap = 50
    splitter.count_tokens = lambda text: len(text)
    return splitter


def test_split_excel_data_returns_no_empty_chunks():
    splitter = _stub_splitter()

    # 只有表头 / 空表：以前返回 [""]，正是 data_excel_db_chunks 两条空 chunk 的来源
    assert splitter.split_excel_data({"headers": ["A"], "rows": []}) == []
    assert splitter.split_excel_data({"headers": [], "rows": []}) == []

    # 全空行被跳过，不会堆出空 chunk
    empty_rows = [["", ""], [None, None]]
    assert splitter.split_excel_data({"headers": ["A", "B"], "rows": empty_rows}) == []

    chunks = splitter.split_excel_data(
        {"headers": ["项目", "负责人"], "rows": [["V254 (GLC)", "洪鑫浩"]]}
    )
    assert chunks == ["项目：V254 (GLC), 负责人：洪鑫浩"]


def test_split_text_blank_returns_no_chunks():
    pytest.importorskip("transformers")
    pytest.importorskip("langchain_text_splitters")
    from app.adapters.doc_processing.text_splitter import TokenAwareTextSplitter

    assert TokenAwareTextSplitter.split_text(object.__new__(TokenAwareTextSplitter), "   \n ") == []


# ---------------------------------------------------------------------------
# 4. 检索端丢弃历史空 chunk
# ---------------------------------------------------------------------------


class _FakeNode:
    def __init__(self, text, source="a.xlsx", score=0.5):
        self.text = text
        self.metadata = {"source": source}
        self.score = score


class _FakeVectorRetriever:
    def __init__(self, nodes):
        self.nodes = list(nodes)

    def retrieve(self, question):
        return list(self.nodes)


class _FakeModelManager:
    def __init__(self, nodes):
        self._retriever = _FakeVectorRetriever(nodes)

    def get_retriever(self, collection_name, top_k):
        return self._retriever


def test_get_chunks_drops_empty_nodes():
    pytest.importorskip("llama_index")
    from app.adapters.ragsystem.retriever_for_nbhx import OptimizedRetriever

    retriever = object.__new__(OptimizedRetriever)
    retriever.collection_name = "excel_db_chunks"
    retriever.default_top_n = 3
    retriever.model_manager = _FakeModelManager(
        [_FakeNode(""), _FakeNode("  "), _FakeNode("杨贵宁", "PM项目分配表_0618.xlsx")]
    )

    result = retriever.get_chunks("项目 V254 (GLC) 的负责人？", 5)

    assert [c["content"] for c in result["chunks"]] == ["杨贵宁"]
    assert result["chunks"][0]["source"] == "PM项目分配表_0618.xlsx"


def test_get_chunks_filters_empty_before_top_k_slice():
    """空 chunk 不能白占召回名额：过滤要在 top_k 截断之前。"""
    pytest.importorskip("llama_index")
    from app.adapters.ragsystem.retriever_for_nbhx import OptimizedRetriever

    nodes = [
        _FakeNode(""),               # 脏数据占掉 top_k=2 的一个名额（修复前）
        _FakeNode("一号命中", "a.xlsx"),
        _FakeNode("二号命中", "b.xlsx"),
    ]
    retriever = object.__new__(OptimizedRetriever)
    retriever.collection_name = "excel_db_chunks"
    retriever.default_top_n = 3
    retriever.model_manager = _FakeModelManager(nodes)

    result = retriever.get_chunks("q", 2)

    assert [c["content"] for c in result["chunks"]] == ["一号命中", "二号命中"]
