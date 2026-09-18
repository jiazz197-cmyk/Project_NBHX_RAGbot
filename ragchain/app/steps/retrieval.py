"""本地检索步骤：doc 向量检索+reranker 重排，excel 表格数据，失败降级为空资料。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DocumentChunk:
    content: str
    source: str = "未知来源"
    score: float = 0.0
    rerank_score: float | None = None


@dataclass
class LocalRetrievalResult:
    documents: list[DocumentChunk] = field(default_factory=list)
    excel_answer: str = ""
    excel_sources: list[str] = field(default_factory=list)
    attempted: bool = False
    available: bool = True
    errors: list[str] = field(default_factory=list)

    @property
    def doc_sources(self) -> list[str]:
        names: list[str] = []
        for chunk in self.documents:
            if chunk.source and chunk.source not in names:
                names.append(chunk.source)
        return names


def build_query(rewritten_query: str, keywords: list[str] | None) -> str:
    parts = [str(rewritten_query or "").strip()]
    parts.extend(str(kw).strip() for kw in (keywords or []) if str(kw).strip())
    return " ".join(p for p in parts if p)


def _extract_chunks(raw: Any) -> list[DocumentChunk]:
    """兼容 {"chunks":[...]} / {"data":{"chunks":[...]}} / list 等多种返回。"""
    items: list[Any] = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        chunks = raw.get("chunks")
        if isinstance(chunks, list):
            items = chunks
        elif isinstance(raw.get("data"), list):
            items = raw["data"]
        elif isinstance(raw.get("data"), dict) and isinstance(raw["data"].get("chunks"), list):
            items = raw["data"]["chunks"]

    result: list[DocumentChunk] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, str):
            content, source, score = item.strip(), "未知来源", 0.0
        elif isinstance(item, dict):
            content = str(item.get("content") or item.get("text") or "").strip()
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            source = str(item.get("source") or metadata.get("source") or "未知来源")
            try:
                score = float(item.get("score") or item.get("relevance_score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
        else:
            content = str(getattr(item, "content", "") or "").strip()
            source = str(getattr(item, "source", "") or "未知来源")
            try:
                score = float(getattr(item, "score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
        if content:
            result.append(DocumentChunk(content=content, source=source, score=score))
    return result


def _apply_rerank(
    chunks: list[DocumentChunk],
    ranked: Any,
    top_n: int,
) -> list[DocumentChunk]:
    """按 reranker 返回的 (原索引, score) 降序重排；格式异常/为空时降级截断。"""
    limit = top_n if top_n and top_n > 0 else len(chunks)
    normalized: list[tuple[int, float]] = []
    if isinstance(ranked, list):
        for item in ranked:
            try:
                if isinstance(item, dict):
                    idx = int(item.get("index", item.get("doc_index")))
                    score = float(item.get("relevance_score", item.get("score")))
                else:
                    idx = int(item[0])
                    score = float(item[1])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            if 0 <= idx < len(chunks):
                normalized.append((idx, score))
    # 强制按重排分数降序，确保 prompt 中 [来源i] 的顺序可信
    normalized.sort(key=lambda pair: pair[1], reverse=True)

    ordered: list[DocumentChunk] = []
    seen: set[int] = set()
    for idx, score in normalized:
        if idx in seen:
            continue
        seen.add(idx)
        chunk = chunks[idx]
        ordered.append(
            DocumentChunk(
                content=chunk.content,
                source=chunk.source,
                score=chunk.score,
                rerank_score=score,
            )
        )
        if len(ordered) >= limit:
            break
    if ordered:
        return ordered
    return chunks[:limit]


async def _retrieve_docs(
    *,
    token: str,
    question: str,
    deps,
    settings,
) -> tuple[list[DocumentChunk], bool, str]:
    try:
        raw = await deps.retriever.query_db(
            token,
            settings.DOC_COLLECTION,
            question,
            settings.RAG_RETRIEVE_TOP_K,
            rerank=False,
        )
        chunks = _extract_chunks(raw)
    except Exception as exc:  # noqa: BLE001 - 单点失败降级
        logger.warning("文档检索失败：%s", exc)
        return [], False, f"文档检索失败：{exc}"

    if not chunks:
        return [], True, ""

    ranked_chunks: list[DocumentChunk] | None = None
    try:
        ranked = await deps.reranker.rerank(
            query=question,
            documents=[chunk.content for chunk in chunks],
            top_n=settings.RAG_RERANK_TOP_N,
        )
        ranked_chunks = _apply_rerank(chunks, ranked, settings.RAG_RERANK_TOP_N)
    except Exception as exc:  # noqa: BLE001 - 重排失败降级截断
        logger.warning("reranker 调用/解析失败，降级按原始顺序截断：%s", exc)
        ranked_chunks = chunks[: max(1, settings.RAG_RERANK_TOP_N)]
    return ranked_chunks, True, ""


async def _retrieve_excel(
    *,
    token: str,
    question: str,
    deps,
    settings,
) -> tuple[str, list[str], bool, str]:
    try:
        raw = await deps.retriever.query_excel(
            token,
            settings.EXCEL_COLLECTION,
            question,
            settings.RAG_RETRIEVE_TOP_K,
        )
    except Exception as exc:  # noqa: BLE001 - 单点失败降级
        logger.warning("表格检索失败：%s", exc)
        return "", [], False, f"表格检索失败：{exc}"

    if not isinstance(raw, dict):
        raw = {}
    answer = raw.get("answer")
    if answer is None:
        answer = raw.get("data") or ""
    if not isinstance(answer, str):
        import json

        try:
            answer = json.dumps(answer, ensure_ascii=False)
        except (TypeError, ValueError):
            answer = str(answer)
    sources_raw = raw.get("sources") or []
    if isinstance(sources_raw, str):
        sources = [sources_raw]
    else:
        try:
            sources = [str(s) for s in sources_raw if str(s).strip()]
        except TypeError:
            sources = []

    limit = int(getattr(settings, "EXCEL_CONTEXT_MAX_CHARS", 8000) or 8000)
    if len(answer) > limit:
        logger.warning("表格数据超长（%d > %d），已截断", len(answer), limit)
        answer = answer[:limit] + "\n…（表格内容过长已截断）"
    return answer, sources, True, ""


async def retrieve_local(
    *,
    intent: str,
    rewritten_query: str,
    keywords: list[str] | None,
    token: str,
    deps,
) -> LocalRetrievalResult:
    """按 intent 并行执行 doc/excel 检索；两者全失败时 available=False。"""
    settings = deps.settings
    result = LocalRetrievalResult()
    question = build_query(rewritten_query, keywords)

    jobs: list[str] = []
    if intent in ("doc", "both"):
        jobs.append("doc")
    if intent in ("excel", "both"):
        jobs.append("excel")
    if not jobs:
        result.attempted = False
        result.available = True
        return result

    result.attempted = True
    coros = []
    if "doc" in jobs:
        coros.append(_retrieve_docs(token=token, question=question, deps=deps, settings=settings))
    if "excel" in jobs:
        coros.append(_retrieve_excel(token=token, question=question, deps=deps, settings=settings))

    outcomes = await asyncio.gather(*coros, return_exceptions=True)
    ok_flags: list[bool] = []
    for job, outcome in zip(jobs, outcomes):
        if isinstance(outcome, BaseException):
            logger.warning("%s 检索步骤异常：%s", job, outcome)
            result.errors.append(f"{job} 检索异常：{outcome}")
            ok_flags.append(False)
            continue
        if job == "doc":
            chunks, ok, error = outcome
            result.documents = chunks
            ok_flags.append(ok)
            if error:
                result.errors.append(error)
        else:
            answer, sources, ok, error = outcome
            result.excel_answer = answer
            result.excel_sources = sources
            ok_flags.append(ok)
            if error:
                result.errors.append(error)

    result.available = any(ok_flags) if ok_flags else True
    return result
