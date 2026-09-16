"""Outbound ports for the reserved LangChain chat orchestration.

The implementation lives in ``app/adapters/langchain_chat``.  Routes and
usecases only depend on these Protocols, so replacing the placeholder adapter
with a real LangChain implementation does not require Nginx, frontend or HTTP
route changes.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from app.ports.dto.chat import (
    ChatMessageCommand,
    ChatStreamEvent,
    ConversationDTO,
    ConversationPage,
    ConversationPageQuery,
    DeleteConversationCommand,
    MessagePage,
    MessagePageQuery,
    RenameConversationCommand,
)


class ChatOrchestratorPort(Protocol):
    """Chat orchestration contract expected from the LangChain implementation."""

    def stream_message(
        self, command: ChatMessageCommand
    ) -> AsyncIterator[ChatStreamEvent]:
        """Return an SSE event stream for one user query."""
        ...

    async def stop(self, task_id: str, user: str) -> bool:
        """Request cooperative cancellation of a running generation task."""
        ...

    async def list_conversations(
        self, query: ConversationPageQuery
    ) -> ConversationPage:
        """List conversations owned by ``query.user_id``."""
        ...

    async def list_messages(self, query: MessagePageQuery) -> MessagePage:
        """List messages belonging to one owned conversation."""
        ...

    async def rename_conversation(
        self, command: RenameConversationCommand
    ) -> ConversationDTO:
        """Rename an owned conversation."""
        ...

    async def delete_conversation(
        self, command: DeleteConversationCommand
    ) -> None:
        """Delete an owned conversation."""
        ...


class ChatMessageRepositoryPort(Protocol):
    """Local repository used by archive / compression adapters.

    A real implementation will read messages persisted in PostgreSQL by the
    future LangChain orchestrator.  Until then the reserved local adapter
    returns no messages.
    """

    async def list_message_queries(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return user query texts for one local conversation."""
        ...

    async def list_recent_dialogues(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return recent dialogue lines for context compression."""
        ...

    async def list_older_dialogues(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return older dialogue lines for context compression."""
        ...
