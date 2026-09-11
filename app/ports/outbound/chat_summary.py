"""Chat summary outbound ports."""

from __future__ import annotations

from typing import Optional, Protocol

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat_summary import ChatSummaryResult


class UserLookupPort(Protocol):
    """Resolve request user identifier into canonical user key."""

    async def resolve_effective_user_id(
        self, requested_user_id: str, current_user: CurrentUserPort
    ) -> str:
        ...


class ChatSummaryRepoPort(Protocol):
    """Read/write summary persistence."""

    def get_latest_summary(self, user_id: str) -> Optional[str]:
        ...


class ChatArchivePort(Protocol):
    """Integration boundary to chat-archive extraction + LLM summarization."""

    async def update_user_profile(self, user_id: str, conversation_id: str, limit: int) -> ChatSummaryResult:
        ...
