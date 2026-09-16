"""Use case: create one conversation for the current (or admin-selected) user."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import ConversationDTO, CreateConversationCommand
from app.ports.outbound.chat import ConversationStorePort
from app.usecases.chat.authorization import resolve_effective_chat_user_id


@dataclass
class CreateConversationInput:
    current_user: CurrentUserPort
    conversation_id: Optional[str] = None
    name: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    requested_user_id: Optional[str] = None


class CreateConversationUseCase:
    def __init__(self, store: ConversationStorePort):
        self._store = store

    async def execute(self, cmd: CreateConversationInput) -> ConversationDTO:
        user_id = resolve_effective_chat_user_id(
            cmd.requested_user_id,
            cmd.current_user,
        )
        return await self._store.create_conversation(
            CreateConversationCommand(
                user_id=user_id,
                conversation_id=cmd.conversation_id,
                name=cmd.name,
                inputs=dict(cmd.inputs or {}),
            )
        )
