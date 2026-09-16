"""Local chat message repository placeholder.

The future LangChain orchestrator persists conversations/messages in the local
PostgreSQL database.  Until that implementation exists there is no local chat
message table, so this adapter reports no messages and the archive/compression
pipelines stay in a well-defined empty state instead of calling an external
chat service.
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger("chat_archive.local_repository")


class LocalChatMessageRepositoryAdapter:
    """Reserved implementation of ``ChatMessageRepositoryPort``.

    Returns empty lists until ``ChatOrchestratorPort`` starts persisting
    messages locally.  Keeping the Port/Adapter boundary here means the future
    implementation only has to replace this class.
    """

    async def list_message_queries(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        logger.debug(
            "Local chat messages are not persisted yet; empty archive source. "
            "user_id=%s conversation_id=%s limit=%s",
            user_id,
            conversation_id,
            limit,
        )
        return []

    async def list_recent_dialogues(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        logger.debug(
            "Local recent dialogues unavailable until chat orchestrator is implemented. "
            "user_id=%s conversation_id=%s limit=%s",
            user_id,
            conversation_id,
            limit,
        )
        return []

    async def list_older_dialogues(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        logger.debug(
            "Local older dialogues unavailable until chat orchestrator is implemented. "
            "user_id=%s conversation_id=%s limit=%s",
            user_id,
            conversation_id,
            limit,
        )
        return []
