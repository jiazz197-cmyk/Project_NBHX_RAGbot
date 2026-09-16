"""Use case: list conversations owned by the current (or admin-selected) user."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import ConversationPage, ConversationPageQuery
from app.ports.outbound.chat import ChatOrchestratorPort
from app.usecases.chat.authorization import resolve_effective_chat_user_id


@dataclass
class ListConversationsQuery:
    current_user: CurrentUserPort
    requested_user_id: Optional[str] = None
    page: int = 1
    limit: int = 20


class ListConversationsUseCase:
    def __init__(self, orchestrator: ChatOrchestratorPort):
        self._orchestrator = orchestrator

    async def execute(self, query: ListConversationsQuery) -> ConversationPage:
        user_id = resolve_effective_chat_user_id(
            query.requested_user_id,
            query.current_user,
        )
        return await self._orchestrator.list_conversations(
            ConversationPageQuery(
                user_id=user_id,
                page=query.page,
                limit=query.limit,
            )
        )
