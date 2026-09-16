"""Use case: delete one owned conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import DeleteConversationCommand
from app.ports.outbound.chat import ChatOrchestratorPort
from app.usecases.chat.authorization import resolve_effective_chat_user_id


@dataclass
class DeleteConversationInput:
    conversation_id: str
    current_user: CurrentUserPort
    requested_user_id: Optional[str] = None


class DeleteConversationUseCase:
    def __init__(self, orchestrator: ChatOrchestratorPort):
        self._orchestrator = orchestrator

    async def execute(self, cmd: DeleteConversationInput) -> None:
        user_id = resolve_effective_chat_user_id(
            cmd.requested_user_id,
            cmd.current_user,
        )
        await self._orchestrator.delete_conversation(
            DeleteConversationCommand(
                user_id=user_id,
                conversation_id=cmd.conversation_id,
            )
        )
