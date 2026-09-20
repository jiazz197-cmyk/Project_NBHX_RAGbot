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
    """检索用例：async 透传给 RetrieverPort（端口实现负责真正的异步检索）。"""

    def __init__(self, port: RetrieverPort) -> None:
        self._port = port

    async def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        return await self._port.query_db(q)

    async def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        return await self._port.query_excel(q)


class ChartAnalysisUseCase:
    def __init__(self, port: ChartAnalysisPort) -> None:
        self._port = port

    async def analyze(self, data_source: Any, requirements: str) -> Any:
        return await self._port.analyze(data_source, requirements)
