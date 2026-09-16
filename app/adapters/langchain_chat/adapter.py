"""Placeholder adapter for the reserved LangChain chat orchestrator.

Every method raises :class:`ChatOrchestratorNotConfiguredError` so the HTTP
layer keeps returning the agreed ``501 CHAT_ORCHESTRATOR_NOT_CONFIGURED``
instead of a legacy ``404/502``.  Replacing this class with a real LangChain
implementation is the only step required to enable the unmodified
``POST /chat-messages`` and ``POST /chat-messages/{task_id}/stop`` routes.

Conversation/message persistence is *not* part of this port: the local
``ConversationStorePort`` adapter already serves those routes from PostgreSQL.
"""

from __future__ import annotations

from typing import AsyncIterator

from app.core.exceptions import ChatOrchestratorNotConfiguredError
from app.ports.dto.chat import ChatMessageCommand, ChatStreamEvent


class LangChainChatOrchestratorAdapter:
    """Reserved implementation of ``ChatOrchestratorPort``.

    Current status: 预留未实现.  Both methods fail closed with the same
    explicit error; the composition root / global exception handler converts
    it to HTTP 501 + ``CHAT_ORCHESTRATOR_NOT_CONFIGURED``.
    """

    def stream_message(
        self, command: ChatMessageCommand
    ) -> AsyncIterator[ChatStreamEvent]:
        raise ChatOrchestratorNotConfiguredError()

    async def stop(self, task_id: str, user: str) -> bool:
        raise ChatOrchestratorNotConfiguredError()
