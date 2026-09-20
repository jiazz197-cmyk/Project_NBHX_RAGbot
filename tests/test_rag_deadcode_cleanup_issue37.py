"""issue #37 回归：RAG 侧死代码清理、VectorStoreManager 收敛与 reranker 异步钩子。

结构性检查为主，不连 DB / HTTP；llama_index 缺失时整体跳过
（与 test_excel_retrieval_hardening.py 相同的 importorskip 模式）。
"""

from __future__ import annotations

import inspect

import pytest

pytest.importorskip("llama_index")


def test_vector_store_manager_single_definition():
    """两个同名 VectorStoreManager 已收敛为一个实现。"""
    from app.adapters.vector_store_manager import VectorStoreManager
    from app.adapters.doc_processing import embedding_store
    from app.adapters.ragsystem import RAGretriever

    assert inspect.isclass(VectorStoreManager)
    # 旧实现位置不再自带同名类
    assert not hasattr(embedding_store, "VectorStoreManager")
    # 检索侧引用的是同一个类对象，而不是第二份实现
    assert RAGretriever.VectorStoreManager is VectorStoreManager


def test_rag_unreferenced_methods_removed():
    from app.adapters.ragsystem import RAGretriever
    from app.adapters.ragsystem.RAGretriever import RAGRetrieverSystem

    for name in (
        "get_retriever_by_instance_id",
        "get_query_engine_for_collection",
        "get_all_retrievers",
    ):
        assert not hasattr(RAGRetrieverSystem, name), f"{name} 应随死代码清理删除"

    from app.adapters.ragsystem import retriever_for_nbhx
    from app.adapters.vector_store_manager import VectorStoreManager

    assert not hasattr(retriever_for_nbhx, "format_docs")
    assert not hasattr(retriever_for_nbhx, "cleanup_all_resources")
    assert not hasattr(VectorStoreManager, "drop_vector_store")


_DB_CONFIG = {"database": "db", "host": "h", "password": "p", "port": 5432, "user": "u"}


def test_vector_store_manager_instance_cache_preserved(monkeypatch):
    """检索侧行为保留：同一 {prefix}_{id} 只建一次 PGVectorStore 并复用实例。"""
    from app.adapters import vector_store_manager as store_module

    calls: list[str] = []

    def fake_from_params(**kwargs):
        calls.append(kwargs["table_name"])
        return object()

    monkeypatch.setattr(store_module.PGVectorStore, "from_params", staticmethod(fake_from_params))

    manager = store_module.VectorStoreManager(db_config=_DB_CONFIG, table_prefix="doc_collection")
    first = manager.create_vector_store(7)
    second = manager.create_vector_store(7)

    assert first is second
    assert calls == ["doc_collection_7"]


def test_vector_store_manager_upsert_wraps_error(monkeypatch):
    """入库侧行为保留：建 PGVectorStore 失败统一抛 VectorStoreError。"""
    from app.adapters import vector_store_manager as store_module
    from app.adapters.doc_processing.exceptions import VectorStoreError

    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(store_module.PGVectorStore, "from_params", staticmethod(boom))

    manager = store_module.VectorStoreManager(db_config=_DB_CONFIG)
    with pytest.raises(VectorStoreError):
        manager.upsert_chunks([], "knowledge_chunks", embedding_model=object())


def test_chart_analyze_unreachable_print_removed():
    from app.adapters.ragsystem import chart_analyze

    source = inspect.getsource(chart_analyze)
    assert "print(charts_info)" not in source


def test_http_reranker_async_hook_overrides_base():
    """issue #37 方案②：_apostprocess_nodes 必须是真异步实现，而非基类线程池兜底。"""
    from llama_index.core.postprocessor.types import BaseNodePostprocessor

    from app.adapters.ragsystem.RAGretriever import HTTPReranker

    assert inspect.iscoroutinefunction(HTTPReranker._rerank_request)
    assert inspect.iscoroutinefunction(HTTPReranker._apostprocess_nodes)
    assert HTTPReranker._apostprocess_nodes is not BaseNodePostprocessor._apostprocess_nodes


def test_langchain_compat_logs_warning_on_import_error(monkeypatch):
    """兼容层注入失败必须 warning，不再静默 pass（补丁文件本身保留）。"""
    import sys
    from unittest import mock

    import langchain_compat

    monkeypatch.delitem(sys.modules, "langchain.docstore", raising=False)
    monkeypatch.delitem(sys.modules, "langchain.docstore.document", raising=False)
    monkeypatch.delitem(sys.modules, "langchain.text_splitter", raising=False)
    # None 会让 `from langchain_core.documents import Document` 抛 ImportError
    monkeypatch.setitem(sys.modules, "langchain_core.documents", None)

    with mock.patch.object(langchain_compat, "logger") as fake_logger:
        langchain_compat.apply_langchain_compat()

    assert fake_logger.warning.called
    assert "兼容层注入失败" in fake_logger.warning.call_args[0][0]
