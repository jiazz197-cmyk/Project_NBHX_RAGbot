# api/chat_api.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_rag_instance
from app.core.config import settings
from app.core.security import get_current_user
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER
from app.ports.outbound.retriever import RetrievalQuery
from app.adapters.retriever import (
    TOP_K_EXPLICIT_META_KEY,
    ChartAnalysisAdapter,
    RAGRetrieverAdapter,
)
from app.adapters.web.base import ChatRequest, ChartRequest
from app.usecases.retriever.retrieve import ChartAnalysisUseCase, RetrieverUseCase

router = APIRouter()

# 语义集合名：小写字母开头，仅小写字母 / 数字 / 下划线（PGVector 逻辑表名约束）
_COLLECTION_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


def _normalize_query_int(value):
    """直接调用路由函数时（测试/脚本）未传参会拿到 FastAPI Query(...) 默认对象。

    HTTP 请求会由 FastAPI 解析成 int/None；这里只做防御性归一化，保证
    ``api_mod.db(...)`` 这类直调也不会把 Query 对象当显式 top_k。
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _normalize_query_bool(value) -> bool:
    return value if isinstance(value, bool) else True


def _ensure_collection_access(
    collection_name: str,
    current_user: CurrentUserPort,
    allowed_collections: List[str],
) -> str:
    """白名单校验：superuser 放行；普通用户仅允许指定集合（兼容 data_ 前缀写法）。"""
    if current_user.role == ROLE_SUPERUSER:
        return collection_name

    compatible_allowed = set()
    for name in allowed_collections:
        name = name.strip()
        if not name:
            continue
        compatible_allowed.add(name)
        if name.startswith("data_"):
            compatible_allowed.add(name[len("data_"):])

    if collection_name not in compatible_allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="collection access denied",
        )
    return collection_name


@router.post("/db")
def db(
    request: ChatRequest,
    collection: str = Query(
        ...,
        pattern=_COLLECTION_NAME_PATTERN,
        description="文档知识集合名（如 knowledge_chunks，仅放行文档集合白名单）",
    ),
    top_k: int | None = Query(
        None,
        ge=1,
        le=50,
        description=(
            "显式指定时走纯向量检索并返回结构化 chunks（上限 top_k，"
            "不经过内部重排）；不传时保持旧路径"
        ),
    ),
    rerank: bool = Query(
        True,
        description=(
            "内部重排语义仅存在于未显式传 top_k 的旧路径；显式传 top_k 时始终走"
            "纯向量 chunks，rerank 参数不生效，重排由调用方负责（容器固定传 false）"
        ),
    ),
    rag_instance=Depends(get_rag_instance),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    top_k = _normalize_query_int(top_k)
    rerank = _normalize_query_bool(rerank)
    # 接口分家：/db 只查文档表（白名单 RETRIEVER_ALLOWED_DOCUMENT_COLLECTIONS）。
    collection_name = _ensure_collection_access(
        collection, current_user, settings.RETRIEVER_ALLOWED_DOCUMENT_COLLECTIONS
    )
    port = RAGRetrieverAdapter(rag_instance=rag_instance, collection_name=collection_name)
    # 旧行为：不传 top_k 时维持 RetrievalQuery 默认值，不改变 get_response 路径。
    q = RetrievalQuery(question=request.question, collection_name=collection_name)
    if top_k is not None:
        q = RetrievalQuery(
            question=request.question,
            collection_name=collection_name,
            top_k=top_k,
            metadata={TOP_K_EXPLICIT_META_KEY: True, "rerank": rerank},
        )
    result = RetrieverUseCase(port).query_db(q)
    return {
        "answer": result.answer,
        "sources": result.sources,
        "chunks": result.metadata.get("chunks") or [],
    }


@router.post("/excel")
def excel(
    request: ChatRequest,
    collection: str = Query(
        ...,
        pattern=_COLLECTION_NAME_PATTERN,
        description="Excel 集合名（如 excel_db_chunks，仅放行 Excel 集合白名单）",
    ),
    top_k: int | None = Query(
        None,
        ge=1,
        le=50,
        description="显式指定时透传给检索（/excel 不做重排）",
    ),
    rerank: bool = Query(
        True,
        description=(
            "/excel 从检索到出参均不做内部重排，参数仅为与 /db 契约一致；"
            "top_k 仅做透传"
        ),
    ),
    rag_instance=Depends(get_rag_instance),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    top_k = _normalize_query_int(top_k)
    rerank = _normalize_query_bool(rerank)
    # 接口分家：/excel 只查 Excel 表（白名单 RETRIEVER_ALLOWED_EXCEL_COLLECTIONS）。
    collection_name = _ensure_collection_access(
        collection, current_user, settings.RETRIEVER_ALLOWED_EXCEL_COLLECTIONS
    )
    port = RAGRetrieverAdapter(rag_instance=rag_instance, collection_name=collection_name)
    # 旧行为：不传 top_k 时维持 RetrievalQuery 默认值。
    q = RetrievalQuery(question=request.question, collection_name=collection_name)
    if top_k is not None:
        q = RetrievalQuery(
            question=request.question,
            collection_name=collection_name,
            top_k=top_k,
            metadata={TOP_K_EXPLICIT_META_KEY: True, "rerank": rerank},
        )
    result = RetrieverUseCase(port).query_excel(q)
    return {"answer": result.answer, "sources": result.sources}


@router.post("/charts")
async def charts(
    request: ChartRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
):
    # Keep expensive chart analysis limited to authenticated users.
    _ = current_user
    return await ChartAnalysisUseCase(ChartAnalysisAdapter()).analyze(request.data_source, request.requirements)
