"""Chat memory + reserved LangChain orchestration HTTP routes.

The external paths and SSE event names are intentionally identical to the
previous chat protocol at:

* ``POST   /api/v1/chat-messages``                 (reserved, 501 for now)
* ``POST   /api/v1/chat-messages/{task_id}/stop``  (reserved, 501 for now)
* ``POST   /api/v1/conversations``                 (local memory, live)
* ``POST   /api/v1/conversations/{id}/messages``   (local memory, live)
* ``GET    /api/v1/conversations``
* ``GET    /api/v1/messages``
* ``POST   /api/v1/conversations/{conversation_id}/name``
* ``DELETE /api/v1/conversations/{conversation_id}``

Conversation/message persistence (short-term memory) is served locally from
PostgreSQL and is fully live.  Only the generation pipeline — the two
``chat-messages`` routes — still fails closed with the agreed
``501 CHAT_ORCHESTRATOR_NOT_CONFIGURED`` until the LangChain orchestrator
adapter is implemented.  All routes require a Bearer JWT and derive the
effective user id from the JWT subject.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette import status

from app.adapters.chat_archive.memory_repository import (
    SqlAlchemyChatMemoryRepositoryAdapter,
)
from app.adapters.langchain_chat.adapter import LangChainChatOrchestratorAdapter
from app.core.exceptions import NotFoundError
from app.core.security import get_current_user
from app.core.validators.conversation_id import validate_conversation_id
from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import (
    ChatStreamEvent,
    ConversationPage,
    MessageInput,
    MessagePage,
)
from app.usecases.chat.append_messages import (
    AppendMessagesInput,
    AppendMessagesUseCase,
)
from app.usecases.chat.create_conversation import (
    CreateConversationInput,
    CreateConversationUseCase,
)
from app.usecases.chat.delete_conversation import (
    DeleteConversationInput,
    DeleteConversationUseCase,
)
from app.usecases.chat.list_conversations import (
    ListConversationsQuery,
    ListConversationsUseCase,
)
from app.usecases.chat.list_messages import ListMessagesQuery, ListMessagesUseCase
from app.usecases.chat.rename_conversation import (
    RenameConversationInput,
    RenameConversationUseCase,
)
from app.usecases.chat.send_message import SendMessageCommand, SendMessageUseCase
from app.usecases.chat.stop_message import StopMessageCommand, StopMessageUseCase

router = APIRouter()

_SSE_HEARTBEAT_SEC = 15
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatMessageRequest(BaseModel):
    """Body for ``POST /api/v1/chat-messages``."""

    query: str = Field(..., min_length=1, max_length=20_000)
    conversation_id: Optional[str] = Field(
        None,
        max_length=128,
        description="Internal conversation id. Omit to create a new conversation.",
    )
    search_mode: str = Field(
        "本地&网络",
        max_length=32,
        description="Retrieval mode selected by the frontend, e.g. 本地检索 / 联网搜索 / 本地&网络.",
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional workflow inputs (search mode, background context, etc.).",
    )
    response_mode: Literal["streaming", "blocking"] = "streaming"

    @field_validator("conversation_id")
    @classmethod
    def conversation_id_path_safe(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_conversation_id(value)


class RenameConversationRequest(BaseModel):
    """Body for ``POST /api/v1/conversations/{conversation_id}/name``."""

    name: str = Field(..., min_length=1, max_length=255)
    auto_generate: bool = False


class CreateConversationRequest(BaseModel):
    """Body for ``POST /api/v1/conversations``."""

    conversation_id: Optional[str] = Field(
        None,
        max_length=64,
        description="Optional caller-supplied id; omit to let the server generate one.",
    )
    name: str = Field("", max_length=255)
    inputs: dict[str, Any] = Field(default_factory=dict)
    user_id: Optional[str] = Field(
        None,
        max_length=128,
        description="仅 admin / superuser 可指定他人 user_id；普通用户始终使用 JWT 自身。",
    )

    @field_validator("conversation_id")
    @classmethod
    def conversation_id_path_safe(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_conversation_id(value)


class ChatMessageInput(BaseModel):
    """One message inside an append request."""

    role: Literal["user", "assistant", "system"]
    content: str = Field("", max_length=200_000)
    query: str = Field("", max_length=200_000)
    answer: str = Field("", max_length=200_000)
    created_at: Optional[int] = Field(
        None, ge=0, description="Epoch seconds; omit to use the server clock."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendMessagesRequest(BaseModel):
    """Body for ``POST /api/v1/conversations/{conversation_id}/messages``."""

    messages: list[ChatMessageInput] = Field(..., min_length=1, max_length=200)
    user_id: Optional[str] = Field(
        None,
        max_length=128,
        description="仅 admin / superuser 可指定他人 user_id；普通用户始终使用 JWT 自身。",
    )


def _validate_path_id(raw: str) -> str:
    """Validate one path/query id and convert ValueError to a 422 response."""

    from fastapi import HTTPException

    try:
        return validate_conversation_id(raw)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _orchestrator() -> LangChainChatOrchestratorAdapter:
    """Composition-root factory for the (still reserved) generation pipeline.

    Future LangChain integration replaces only this adapter implementation;
    route paths, the frontend and Nginx stay unchanged.
    """

    return LangChainChatOrchestratorAdapter()


def _conversation_store() -> SqlAlchemyChatMemoryRepositoryAdapter:
    """Composition-root factory for the live local memory store."""

    return SqlAlchemyChatMemoryRepositoryAdapter()


def _as_payload(value: Any) -> Any:
    """Convert DTO dataclasses (and nested dataclasses) to JSON primitives."""

    if hasattr(value, "__dataclass_fields__"):
        return {key: _as_payload(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [_as_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _as_payload(item) for key, item in value.items()}
    return value


def _page_response(page: ConversationPage | MessagePage) -> JSONResponse:
    """Return the legacy ``{data, page, limit, has_more}`` shape unchanged."""

    return JSONResponse(content=_as_payload(page), status_code=status.HTTP_200_OK)


def _sse_payload(event: ChatStreamEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {"event": event.event or "message"}
    if event.task_id:
        payload["task_id"] = event.task_id
    if event.conversation_id:
        payload["conversation_id"] = event.conversation_id
    # Keep ``content`` present for message events (possibly empty for end/error).
    payload["content"] = event.content
    if event.usage is not None:
        payload["usage"] = event.usage
    if event.error is not None:
        payload["error"] = event.error
    return payload


def _format_sse(event: ChatStreamEvent) -> bytes:
    event_name = event.event or "message"
    data = json.dumps(_sse_payload(event), ensure_ascii=False)
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


async def _stream_sse(events: AsyncIterator[ChatStreamEvent]) -> AsyncIterator[bytes]:
    """Serialize orchestrator events and emit a heartbeat during idle gaps."""

    iterator = events.__aiter__()
    while True:
        try:
            event = await asyncio.wait_for(iterator.__anext__(), timeout=_SSE_HEARTBEAT_SEC)
        except asyncio.TimeoutError:
            yield b"event: ping\ndata: {\"event\":\"ping\"}\n\n"
            continue
        except StopAsyncIteration:
            break
        yield _format_sse(event)


@router.post(
    "/chat-messages",
    summary="发送聊天消息（SSE 流式，LangChain 预留）",
    response_description="SSE stream (text/event-stream)",
    status_code=status.HTTP_200_OK,
)
async def send_chat_message(
    request: ChatMessageRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> StreamingResponse:
    """Send one query; returns an SSE stream once LangChain is configured."""

    usecase = SendMessageUseCase(_orchestrator())
    command = SendMessageCommand(
        user_id=str(current_user.id),
        query=request.query,
        search_mode=request.search_mode,
        inputs=dict(request.inputs or {}),
        response_mode=request.response_mode,
        conversation_id=request.conversation_id,
    )
    events = await usecase.execute(command)
    return StreamingResponse(
        _stream_sse(events),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post(
    "/chat-messages/{task_id}/stop",
    summary="停止聊天生成（LangChain 预留）",
)
async def stop_chat_message(
    task_id: str = Path(..., min_length=1, max_length=128),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> dict[str, str]:
    """Cooperative cancellation of the generation task with ``task_id``."""

    stopped = await StopMessageUseCase(_orchestrator()).execute(
        StopMessageCommand(task_id=task_id, user_id=str(current_user.id))
    )
    if not stopped:
        raise NotFoundError("任务不存在、已结束或不属于当前用户")
    return {"result": "success"}


@router.post(
    "/conversations",
    summary="创建会话（本地记忆库）",
    response_description="新建或已存在的会话对象；同一 ID 重复创建为幂等",
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    request: CreateConversationRequest,
    current_user: CurrentUserPort = Depends(get_current_user),
) -> JSONResponse:
    """Create one conversation owned by the JWT user (or an admin target)."""

    result = await CreateConversationUseCase(_conversation_store()).execute(
        CreateConversationInput(
            current_user=current_user,
            conversation_id=request.conversation_id,
            name=request.name,
            inputs=dict(request.inputs or {}),
            requested_user_id=request.user_id,
        )
    )
    return JSONResponse(content=_as_payload(result), status_code=status.HTTP_201_CREATED)


@router.get(
    "/conversations",
    summary="获取会话列表（本地记忆库）",
    response_description="分页会话列表，保持旧前端字段形状",
)
async def list_conversations(
    page: int = Query(1, ge=1, le=100_000),
    limit: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(
        None,
        max_length=128,
        description="仅 admin / superuser 可指定他人 user_id；普通用户始终查询 JWT 自身。",
    ),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> JSONResponse:
    """List conversations owned by the JWT user (or an admin target)."""

    result = await ListConversationsUseCase(_conversation_store()).execute(
        ListConversationsQuery(
            current_user=current_user,
            requested_user_id=user_id,
            page=page,
            limit=limit,
        )
    )
    return _page_response(result)


@router.get(
    "/messages",
    summary="获取会话消息列表（本地记忆库）",
    response_description="分页消息列表，保持旧前端字段形状",
)
async def list_messages(
    conversation_id: str = Query(..., min_length=1, max_length=128),
    page: int = Query(1, ge=1, le=100_000),
    limit: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(
        None,
        max_length=128,
        description="仅 admin / superuser 可指定他人 user_id；普通用户始终查询 JWT 自身。",
    ),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> JSONResponse:
    """List messages of one owned conversation."""

    cid = _validate_path_id(conversation_id)
    result = await ListMessagesUseCase(_conversation_store()).execute(
        ListMessagesQuery(
            conversation_id=cid,
            current_user=current_user,
            requested_user_id=user_id,
            page=page,
            limit=limit,
        )
    )
    return _page_response(result)


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="追加会话消息（本地记忆库）",
    response_description="落库后的消息数组（含服务端生成的 id 与时间戳）",
    status_code=status.HTTP_201_CREATED,
)
async def append_conversation_messages(
    request: AppendMessagesRequest,
    conversation_id: str = Path(..., min_length=1, max_length=128),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> JSONResponse:
    """Persist user/assistant messages into one owned conversation.

    This is the short-term-memory write endpoint the RAG container calls after a
    generation completes; it accepts the batch shape documented in
    ``docs/langchain-rag-container-api-contract.md`` §9.4.
    """

    cid = _validate_path_id(conversation_id)
    stored = await AppendMessagesUseCase(_conversation_store()).execute(
        AppendMessagesInput(
            conversation_id=cid,
            messages=[
                MessageInput(
                    role=item.role,
                    content=item.content,
                    query=item.query,
                    answer=item.answer,
                    created_at=item.created_at,
                    metadata=dict(item.metadata or {}),
                )
                for item in request.messages
            ],
            current_user=current_user,
            requested_user_id=request.user_id,
        )
    )
    return JSONResponse(
        content={"data": _as_payload(stored), "conversation_id": cid, "stored": len(stored)},
        status_code=status.HTTP_201_CREATED,
    )


@router.post(
    "/conversations/{conversation_id}/name",
    summary="重命名会话（本地记忆库）",
)
async def rename_conversation(
    request: RenameConversationRequest,
    conversation_id: str = Path(..., min_length=1, max_length=128),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> JSONResponse:
    """Rename an owned conversation."""

    cid = _validate_path_id(conversation_id)
    result = await RenameConversationUseCase(_conversation_store()).execute(
        RenameConversationInput(
            conversation_id=cid,
            name=request.name,
            auto_generate=request.auto_generate,
            current_user=current_user,
        )
    )
    return JSONResponse(content=_as_payload(result), status_code=status.HTTP_200_OK)


@router.delete(
    "/conversations/{conversation_id}",
    summary="删除会话（本地记忆库）",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
    conversation_id: str = Path(..., min_length=1, max_length=128),
    current_user: CurrentUserPort = Depends(get_current_user),
) -> Response:
    """Delete an owned conversation."""

    cid = _validate_path_id(conversation_id)
    await DeleteConversationUseCase(_conversation_store()).execute(
        DeleteConversationInput(conversation_id=cid, current_user=current_user)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
