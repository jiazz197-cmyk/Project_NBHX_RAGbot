# api/chat_api.py
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


def _ensure_collection_access(collection_name: str, current_user: CurrentUserPort) -> str:
    if current_user.role == ROLE_SUPERUSER:
        return collection_name

    allowed_collections = {name.strip() for name in settings.RETRIEVER_ALLOWED_COLLECTIONS if name.strip()}
    compatible_allowed = set(allowed_collections)
    for name in allowed_collections:
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
        description="知识库集合名（如 knowledge_chunks，对应 data_<collection> 向量表）",
    ),
    rag_instance=Depends(get_rag_instance),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    # 语义集合名即 PGVector 逻辑表名（物理表 data_<collection>），
    # 直接传逻辑表名，避免 data_data_* 前缀错位。
    collection_name = _ensure_collection_access(collection, current_user)
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
        description="知识库集合名（如 knowledge_chunks，对应 data_<collection> 向量表）",
    ),
    rag_instance=Depends(get_rag_instance),
    current_user: CurrentUserPort = Depends(get_current_user),
):
    collection_name = _ensure_collection_access(collection, current_user)
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
