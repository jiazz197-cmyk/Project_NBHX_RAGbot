"""RAG retriever use cases (thin delegation to RetrieverPort / ChartAnalysisPort).

Composition root (router module) constructs the adapter and injects it here, so
the API no longer calls driven-adapter methods directly (plan 1785223863115).
"""

from __future__ import annotations

from typing import Any

from app.ports.outbound.retriever import (
    ChartAnalysisPort,
    RetrievalQuery,
    RetrievalResult,
    RetrieverPort,
)


class RetrieverUseCase:
    def __init__(self, port: RetrieverPort) -> None:
        self._port = port

    def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        return self._port.query_db(q)

    def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        return self._port.query_excel(q)


class ChartAnalysisUseCase:
    def __init__(self, port: ChartAnalysisPort) -> None:
        self._port = port

    async def analyze(self, data_source: Any, requirements: str) -> Any:
        return await self._port.analyze(data_source, requirements)
