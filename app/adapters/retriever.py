"""RAG retriever adapter wrapping ragsystem implementations."""

from __future__ import annotations

from typing import Any

from app.ports.outbound.retriever import ChartAnalysisPort, RetrievalQuery, RetrievalResult, RetrieverPort


class RAGRetrieverAdapter(RetrieverPort):
    """Adapter wrapping the ragsystem retriever implementations behind a port interface."""

    def __init__(self, rag_instance, collection_name: str = ""):
        self._rag_instance = rag_instance
        self._collection_name = collection_name

    def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        from app.adapters.ragsystem import retriever_for_nbhx
        collection = q.collection_name or self._collection_name
        retriever = retriever_for_nbhx.retriever(
            rag_system=self._rag_instance,
            collection_name=collection,
        )
        result = retriever.get_response(q.question)
        return RetrievalResult(
            answer="\n".join(result.get("content", [])),
            sources=result.get("source", []),
        )

    def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        from app.adapters.ragsystem import retriever_for_nbhx
        import json
        collection = q.collection_name or self._collection_name
        retriever = retriever_for_nbhx.retriever(
            rag_system=self._rag_instance,
            collection_name=collection,
        )
        result = retriever.get_charts(q.question)
        # get_charts returns excel_to_json result or {"error": "..."}
        if isinstance(result, dict) and "error" in result:
            return RetrievalResult(answer=result["error"], sources=[])
        return RetrievalResult(
            answer=json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
            sources=[],
        )


class ChartAnalysisAdapter(ChartAnalysisPort):
    """Adapter wrapping the ragsystem chart analyzer behind a port interface."""

    async def analyze(self, data_source: Any, requirements: str) -> Any:
        from app.adapters.ragsystem import chart_analyze
        analyzer = chart_analyze.analyze()
        return await analyzer.get_response(data_source, requirements)
