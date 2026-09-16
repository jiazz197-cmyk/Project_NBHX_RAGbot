"""Use case: append messages to one owned conversation (short-term memory write)."""

from __future__ import annotations

from dataclasses import dataclass

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import (
    AppendMessagesCommand,
    MessageDTO,
    MessageInput,
)
from app.ports.outbound.chat import ConversationStorePort
from app.usecases.chat.authorization import resolve_effective_chat_user_id


@dataclass
class AppendMessagesInput:
    conversation_id: str
    messages: list[MessageInput]
    current_user: CurrentUserPort
    requested_user_id: str | None = None


class AppendMessagesUseCase:
    def __init__(self, store: ConversationStorePort):
        self._store = store

    async def execute(self, cmd: AppendMessagesInput) -> list[MessageDTO]:
        user_id = resolve_effective_chat_user_id(
            cmd.requested_user_id,
            cmd.current_user,
        )
        return await self._store.append_messages(
            AppendMessagesCommand(
                user_id=user_id,
                conversation_id=cmd.conversation_id,
                messages=list(cmd.messages),
            )
        )
