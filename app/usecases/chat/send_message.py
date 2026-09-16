"""Use case: send one streaming chat message to the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from app.ports.dto.chat import ChatMessageCommand, ChatStreamEvent
from app.ports.outbound.chat import ChatOrchestratorPort


@dataclass
class SendMessageCommand:
    user_id: str
    query: str
    search_mode: str = "本地&网络"
    inputs: dict[str, Any] = field(default_factory=dict)
    response_mode: str = "streaming"
    conversation_id: Optional[str] = None


class SendMessageUseCase:
    """Thin orchestration boundary.

    The HTTP route owns streaming transport; this use case only validates the
    command shape and delegates to the Port.  The placeholder adapter raises
    immediately, so FastAPI can still return a JSON 501 instead of starting a
    misleading empty SSE stream.
    """

    def __init__(self, orchestrator: ChatOrchestratorPort):
        self._orchestrator = orchestrator

    async def execute(self, cmd: SendMessageCommand) -> AsyncIterator[ChatStreamEvent]:
        command = ChatMessageCommand(
            user_id=cmd.user_id,
            query=cmd.query,
            search_mode=cmd.search_mode,
            inputs=dict(cmd.inputs or {}),
            response_mode=cmd.response_mode,
            conversation_id=cmd.conversation_id,
        )
        # The port is a synchronous factory returning an async iterator; the
        # await here lets callers use the same ``await usecase.execute()``
        # convention as every other use case in this repository.
        return self._orchestrator.stream_message(command)
