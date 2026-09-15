# api/chat_api.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_rag_instance
from app.core.config import settings
from app.core.security import get_current_user
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER
from app.ports.outbound.retriever import RetrievalQuery
from app.adapters.retriever import ChartAnalysisAdapter, RAGRetrieverAdapter
from app.adapters.web.base import ChatRequest, ChartRequest
from app.usecases.retriever.retrieve import ChartAnalysisUseCase, RetrieverUseCase

router = APIRouter()

# 语义集合名：小写字母开头，仅小写字母 / 数字 / 下划线（PGVector 逻辑表名约束）
_COLLECTION_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


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
    rag_instance=Depends(get_rag_instance),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    # 接口分家：/db 只查文档表（白名单 RETRIEVER_ALLOWED_DOCUMENT_COLLECTIONS）。
    collection_name = _ensure_collection_access(
        collection, current_user, settings.RETRIEVER_ALLOWED_DOCUMENT_COLLECTIONS
    )
    port = RAGRetrieverAdapter(rag_instance=rag_instance, collection_name=collection_name)
    q = RetrievalQuery(question=request.question, collection_name=collection_name)
    result = RetrieverUseCase(port).query_db(q)
    return {"answer": result.answer, "sources": result.sources}


@router.post("/excel")
def excel(
    request: ChatRequest,
    collection: str = Query(
        ...,
        pattern=_COLLECTION_NAME_PATTERN,
        description="Excel 集合名（如 excel_db_chunks，仅放行 Excel 集合白名单）",
    ),
    rag_instance=Depends(get_rag_instance),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    # 接口分家：/excel 只查 Excel 表（白名单 RETRIEVER_ALLOWED_EXCEL_COLLECTIONS）。
    collection_name = _ensure_collection_access(
        collection, current_user, settings.RETRIEVER_ALLOWED_EXCEL_COLLECTIONS
    )
    port = RAGRetrieverAdapter(rag_instance=rag_instance, collection_name=collection_name)
    q = RetrievalQuery(question=request.question, collection_name=collection_name)
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
