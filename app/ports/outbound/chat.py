"""Outbound ports for the reserved LangChain chat orchestration.

The generation implementation lives in ``app/adapters/langchain_chat``; the
local per-user conversation/message store lives in
``app/adapters/chat_archive``.  Routes and usecases only depend on these
Protocols, so replacing the placeholder orchestrator with a real LangChain
implementation does not require Nginx, frontend or HTTP route changes.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from app.ports.dto.chat import (
    AppendMessagesCommand,
    ChatMessageCommand,
    ChatStreamEvent,
    ConversationDTO,
    ConversationPage,
    ConversationPageQuery,
    CreateConversationCommand,
    DeleteConversationCommand,
    MessageDTO,
    MessagePage,
    MessagePageQuery,
    RenameConversationCommand,
)


class ChatOrchestratorPort(Protocol):
    """Chat orchestration contract expected from the LangChain implementation.

    Only the generation pipeline belongs here: conversation/message persistence
    is served by the local :class:`ConversationStorePort`, so implementing the
    RAG core chain is exactly two methods (stream + cooperative stop).
    """

    def stream_message(
        self, command: ChatMessageCommand
    ) -> AsyncIterator[ChatStreamEvent]:
        """Return an SSE event stream for one user query."""
        ...

    async def stop(self, task_id: str, user: str) -> bool:
        """Request cooperative cancellation of a running generation task."""
        ...


class ConversationStorePort(Protocol):
    """Local per-user conversation/message persistence (short-term memory).

    Every method scopes on ``user_id`` (the internal JWT subject); an adapter
    must never return or mutate another user's rows.
    """

    async def create_conversation(
        self, command: CreateConversationCommand
    ) -> ConversationDTO:
        """Create one conversation owned by ``command.user_id``."""
        ...

    async def append_messages(
        self, command: AppendMessagesCommand
    ) -> list[MessageDTO]:
        """Append messages to one owned conversation and return the stored rows."""
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
        """Delete an owned conversation and its messages."""
        ...


class ChatMessageRepositoryPort(Protocol):
    """Read-only message source used by archive / compression adapters.

    A real implementation reads the messages persisted in PostgreSQL by
    :class:`ConversationStorePort`; both are satisfied by the same adapter.
    """

    async def list_message_queries(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return the most recent user query texts of one conversation."""
        ...

    async def list_recent_dialogues(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return the newest ``limit`` dialogue lines, oldest-first."""
        ...

    async def list_older_dialogues(
        self, user_id: str, conversation_id: str, limit: int, recent: int = 0
    ) -> list[str]:
        """Return up to ``limit`` dialogue lines older than the newest ``recent``.

        ``recent`` is the size of the recent window the caller kept separately,
        so the two returned lists never overlap.
        """
        ...
