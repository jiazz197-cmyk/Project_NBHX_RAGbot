"""RAG retriever ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Union


@dataclass
class RetrievalQuery:
    """Query for RAG retrieval."""
    question: str
    collection_name: str = ""
    top_k: int = 10
    top_n: int = 5
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    """Result from RAG retrieval.

    ``sources`` 允许两种形态：旧的来源对象列表（``[{"name": ...}]``）与
    结构化 chunks 路径的来源名列表（``["a.pdf"]``），HTTP 层原样透传。
    """
    answer: str = ""
    sources: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class RetrieverPort(Protocol):
    """Abstraction for RAG document retrieval.

    issue #37 后续：整条检索链已切异步——异步 query engine（aquery/aretrieve）
    才会触发 ``HTTPReranker._apostprocess_nodes``，从而不阻塞事件循环。
    """

    async def query_db(self, q: RetrievalQuery) -> RetrievalResult:
        ...

    async def query_excel(self, q: RetrievalQuery) -> RetrievalResult:
        ...


class ChartAnalysisPort(Protocol):
    """Abstraction for data-source chart analysis (decouples API from ragsystem)."""

    async def analyze(self, data_source: Any, requirements: str) -> Any:
        ...
