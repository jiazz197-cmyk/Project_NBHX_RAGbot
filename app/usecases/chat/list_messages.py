"""Use case: list messages for one owned conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import MessagePage, MessagePageQuery
from app.ports.outbound.chat import ConversationStorePort
from app.usecases.chat.authorization import resolve_effective_chat_user_id


@dataclass
class ListMessagesQuery:
    conversation_id: str
    current_user: CurrentUserPort
    requested_user_id: Optional[str] = None
    page: int = 1
    limit: int = 20


class ListMessagesUseCase:
    def __init__(self, store: ConversationStorePort):
        self._store = store

    async def execute(self, query: ListMessagesQuery) -> MessagePage:
        user_id = resolve_effective_chat_user_id(
            query.requested_user_id,
            query.current_user,
        )
        return await self._store.list_messages(
            MessagePageQuery(
                user_id=user_id,
                conversation_id=query.conversation_id,
                page=query.page,
                limit=query.limit,
            )
        )
