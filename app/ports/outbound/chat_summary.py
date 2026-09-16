"""Chat summary outbound ports."""

from __future__ import annotations

from typing import Optional, Protocol

from app.ports.contracts.identity import CurrentUserPort
from app.ports.dto.chat_summary import ChatSummaryResult


class UserLookupPort(Protocol):
    """Resolve request user identifier into the canonical user key."""

    async def resolve_effective_user_id(
        self, requested_user_id: str, current_user: CurrentUserPort
    ) -> str:
        """Return the internal user id (``users.id``) the request applies to."""
        ...


class ChatSummaryRepoPort(Protocol):
    """Read/write persistence for the long-term per-user profile summary."""

    async def get_latest_summary(self, user_id: str) -> Optional[str]:
        """Return the stored summary for ``user_id``, or ``None``."""
        ...

    async def upsert_latest_summary(self, user_id: str, latest_summary: str) -> bool:
        """Insert or update the summary for ``user_id``; ``False`` on failure."""
        ...


class ChatArchivePort(Protocol):
    """Integration boundary to chat-archive extraction + LLM summarization."""

    async def update_user_profile(self, user_id: str, conversation_id: str, limit: int) -> ChatSummaryResult:
        """Regenerate and persist the profile summary from stored messages."""
        ...
