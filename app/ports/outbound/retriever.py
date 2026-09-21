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
    # issue #16：改写步骤产出的结构化关键词，供稀疏（字面）检索路使用。
    # 缺省空列表 = 老调用方行为完全不变（只走 dense 向量路）。
    keywords: List[str] = field(default_factory=list)


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


@dataclass
class LexicalHit:
    """稀疏（字面/关键词）检索路的一条命中（issue #16）。"""
    node_id: str
    content: str
    source: str = "Unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 命中的关键词个数：用于同分时排序，也便于调用方观测命中强度
    hits: int = 0


class LexicalSearchPort(Protocol):
    """按关键词在集合内做字面检索（issue #16 路线 1：PG 全文 / pg_trgm）。

    实现方接收**逻辑集合名**（如 ``knowledge_chunks``），自行映射到物理表
    ``data_<collection>``，并对集合名做白名单校验。
    """

    async def search(
        self, collection: str, keywords: List[str], top_k: int
    ) -> List[LexicalHit]:
        ...


class ChartAnalysisPort(Protocol):
    """Abstraction for data-source chart analysis (decouples API from ragsystem)."""

    async def analyze(self, data_source: Any, requirements: str) -> Any:
        ...
