"""Usecase: create or update chat summary."""

from __future__ import annotations

from dataclasses import dataclass

from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.chat_summary import ChatArchivePort, UserLookupPort
from app.ports.dto.chat_summary import ChatSummaryResult


@dataclass
class CreateChatSummaryCommand:
    user_id: str
    conversation_id: str
    limit: int
    current_user: CurrentUserPort


class CreateChatSummaryUseCase:
    def __init__(self, user_lookup: UserLookupPort, chat_archive: ChatArchivePort):
        self._user_lookup = user_lookup
        self._chat_archive = chat_archive

    async def execute(self, cmd: CreateChatSummaryCommand) -> ChatSummaryResult:
        effective_user_id = await self._user_lookup.resolve_effective_user_id(
            requested_user_id=cmd.user_id,
            current_user=cmd.current_user,
        )
        return await self._chat_archive.update_user_profile(
            user_id=effective_user_id,
            conversation_id=cmd.conversation_id,
            limit=cmd.limit,
        )
