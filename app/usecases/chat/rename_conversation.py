"""Use case: rename one owned conversation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat import ConversationDTO, RenameConversationCommand
from app.ports.outbound.chat import ChatOrchestratorPort
from app.usecases.chat.authorization import resolve_effective_chat_user_id


@dataclass
class RenameConversationInput:
    conversation_id: str
    name: str
    auto_generate: bool
    current_user: CurrentUserPort
    requested_user_id: Optional[str] = None


class RenameConversationUseCase:
    def __init__(self, orchestrator: ChatOrchestratorPort):
        self._orchestrator = orchestrator

    async def execute(self, cmd: RenameConversationInput) -> ConversationDTO:
        user_id = resolve_effective_chat_user_id(
            cmd.requested_user_id,
            cmd.current_user,
        )
        return await self._orchestrator.rename_conversation(
            RenameConversationCommand(
                user_id=user_id,
                conversation_id=cmd.conversation_id,
                name=cmd.name,
                auto_generate=cmd.auto_generate,
            )
        )
