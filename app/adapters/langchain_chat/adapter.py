"""Placeholder adapter for the reserved LangChain chat orchestrator.

Every method raises :class:`ChatOrchestratorNotConfiguredError` so the HTTP
layer keeps returning the agreed ``501 CHAT_ORCHESTRATOR_NOT_CONFIGURED``
instead of a legacy ``404/502``.  Replacing this class with a real LangChain
implementation is the only step required to enable the unmodified routes.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.core.exceptions import ChatOrchestratorNotConfiguredError
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


class LangChainChatOrchestratorAdapter:
    """Reserved implementation of ``ChatOrchestratorPort``.

    Current status: 预留未实现.  All methods fail closed with the same
    explicit error; the composition root / global exception handler converts
    it to HTTP 501 + ``CHAT_ORCHESTRATOR_NOT_CONFIGURED``.
    """

    def stream_message(
        self, command: ChatMessageCommand
    ) -> AsyncIterator[ChatStreamEvent]:
        raise ChatOrchestratorNotConfiguredError()

    async def stop(self, task_id: str, user: str) -> bool:
        raise ChatOrchestratorNotConfiguredError()

    async def list_conversations(
        self, query: ConversationPageQuery
    ) -> ConversationPage:
        raise ChatOrchestratorNotConfiguredError()

    async def list_messages(self, query: MessagePageQuery) -> MessagePage:
        raise ChatOrchestratorNotConfiguredError()

    async def rename_conversation(
        self, command: RenameConversationCommand
    ) -> ConversationDTO:
        raise ChatOrchestratorNotConfiguredError()

    async def delete_conversation(
        self, command: DeleteConversationCommand
    ) -> None:
        raise ChatOrchestratorNotConfiguredError()
