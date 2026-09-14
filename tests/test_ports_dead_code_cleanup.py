"""Regression tests for ports-layer dead-code cleanup (plan 1785223863115).

Structural safety checks that need no external services (no DB / Redis / MinIO /
SQL Server / ragsystem). They guard against accidental re-introduction of
removed ports / port methods / DTOs and verify that the new retriever usecases
land in the right architectural layer (depend on ports only).
"""
import importlib

import pytest


class TestRetrieverPortNarrowed:
    def test_retriever_port_has_db_excel_no_query(self):
        from app.ports.outbound.retriever import RetrieverPort
        assert hasattr(RetrieverPort, "query_db")
        assert hasattr(RetrieverPort, "query_excel")
        assert not hasattr(RetrieverPort, "query"), "RetrieverPort.query must be removed"

    def test_rag_adapter_has_db_excel_no_query(self):
        from app.adapters.retriever import RAGRetrieverAdapter
        assert hasattr(RAGRetrieverAdapter, "query_db")
        assert hasattr(RAGRetrieverAdapter, "query_excel")
        assert not hasattr(RAGRetrieverAdapter, "query"), "RAGRetrieverAdapter.query must be removed"

    def test_chart_analysis_port_kept(self):
        from app.ports.outbound.retriever import ChartAnalysisPort
        assert hasattr(ChartAnalysisPort, "analyze")


class TestDeadDtosRemoved:
    def test_query_result_dto_absent(self):
        import app.ports.dto.sqlserver_queries as mod
        assert not hasattr(mod, "QueryResultDTO"), "QueryResultDTO should have been removed"

    def test_sqlserver_query_result_dto_kept(self):
        from app.ports.dto.sqlserver_queries import SqlserverQueryResultDTO
        assert SqlserverQueryResultDTO is not None


class TestRetrieverUseCases:
    def test_usecases_importable(self):
        from app.usecases.retriever.retrieve import ChartAnalysisUseCase, RetrieverUseCase
        assert RetrieverUseCase is not None
        assert ChartAnalysisUseCase is not None

    def test_usecases_depend_on_ports_only(self):
        """Architecture guard: usecases must not import adapters/api/etc."""
        mod = importlib.import_module("app.usecases.retriever.retrieve")
        source = open(mod.__file__, encoding="utf-8").read()
        assert "app.adapters" not in source
        assert "app.api" not in source
