"""DTOs and commands for the reserved chat orchestration interfaces.

These objects are deliberately free of IO and framework types so the future
LangChain implementation can replace ``ChatOrchestratorPort`` without touching
the HTTP routes or frontend paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ChatMessageCommand:
    """Inbound command for ``POST /api/v1/chat-messages``.

    ``user_id`` is always derived from the JWT by the composition root; it is
    never taken from the request body.
    """

    user_id: str
    query: str
    search_mode: str = "本地&网络"
    inputs: dict[str, Any] = field(default_factory=dict)
    response_mode: str = "streaming"
    conversation_id: Optional[str] = None


@dataclass
class ChatStreamEvent:
    """One SSE event produced by the orchestrator.

    Event names are kept compatible with the former frontend protocol:
    ``message``, ``agent_message``, ``message_replace``, ``message_end``,
    ``workflow_finished``, ``error`` and ``ping``.
    """

    event: str
    task_id: str = ""
    conversation_id: str = ""
    content: str = ""
    usage: Optional[dict[str, int]] = None
    error: Optional[dict[str, Any]] = None


@dataclass
class ConversationDTO:
    """Conversation representation returned to the frontend."""

    id: str
    name: str = ""
    user_id: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    status: str = "normal"
    introduction: str = ""
    created_at: int = 0
    updated_at: int = 0


@dataclass
class MessageDTO:
    """Chat message representation returned to the frontend."""

    id: str
    conversation_id: str
    role: str
    content: str = ""
    query: str = ""
    answer: str = ""
    created_at: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationPageQuery:
    """Pagination/filter command for conversation listing."""

    user_id: str
    page: int = 1
    limit: int = 20


@dataclass
class MessagePageQuery:
    """Pagination/filter command for message listing."""

    user_id: str
    conversation_id: str
    page: int = 1
    limit: int = 20


@dataclass
class ConversationPage:
    """Legacy-compatible paginated conversation response shape."""

    page: int
    limit: int
    has_more: bool
    data: list[ConversationDTO] = field(default_factory=list)


@dataclass
class MessagePage:
    """Legacy-compatible paginated message response shape."""

    page: int
    limit: int
    has_more: bool
    data: list[MessageDTO] = field(default_factory=list)


@dataclass
class RenameConversationCommand:
    """Rename one conversation owned by ``user_id``."""

    user_id: str
    conversation_id: str
    name: str
    auto_generate: bool = False


@dataclass
class DeleteConversationCommand:
    """Delete one conversation owned by ``user_id``."""

    user_id: str
    conversation_id: str
