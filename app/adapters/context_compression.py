"""Adapter: context compression integration using the local message repository."""

from __future__ import annotations

from typing import Any, Optional

from app.core.logging import get_logger
from app.adapters.chat_archive.local_message_repository import (
    LocalChatMessageRepositoryAdapter,
)
from app.adapters.context_compressor import (
    LlmEndpointMisconfiguredError,
    compress_context,
)
from app.core.exceptions import ExternalServiceError
from app.ports.outbound.chat import ChatMessageRepositoryPort
from app.ports.outbound.context_compression import ContextCompressorPort

logger = get_logger("context_compression.adapter")


class IntegrationContextCompressorAdapter(ContextCompressorPort):
    """Feed local dialogue history into the LangChain compressor."""

    def __init__(
        self,
        message_repository: Optional[ChatMessageRepositoryPort] = None,
    ):
        self._messages = message_repository or LocalChatMessageRepositoryAdapter()

    async def _with_local_dialogues(self, context_data: dict) -> dict:
        data = dict(context_data)
        if "recent_dialogues" in data and "older_dialogues" in data:
            return data

        user_id = str(data.get("user_id", "") or "")
        conversation_id = str(data.get("conversation_id", "") or "")
        if not user_id or not conversation_id:
            data.setdefault("recent_dialogues", [])
            data.setdefault("older_dialogues", [])
            return data

        n_recent = int(data.get("n_recent", 5) or 5)
        try:
            data["recent_dialogues"] = await self._messages.list_recent_dialogues(
                user_id=user_id,
                conversation_id=conversation_id,
                limit=max(1, min(n_recent, 100)),
            )
            data["older_dialogues"] = await self._messages.list_older_dialogues(
                user_id=user_id,
                conversation_id=conversation_id,
                limit=max(1, min(n_recent * 4, 400)),
            )
        except Exception as exc:  # noqa: BLE001 - compression can still run with request inputs
            logger.warning(
                "Local dialogue repository unavailable; compressing with the "
                "request-provided context only: %s",
                exc,
            )
            data.setdefault("recent_dialogues", [])
            data.setdefault("older_dialogues", [])
        return data

    async def compress(self, context_data: dict) -> Any:
        try:
            enriched = await self._with_local_dialogues(context_data)
            return await compress_context(enriched)
        except LlmEndpointMisconfiguredError as exc:
            raise ExternalServiceError("context_compression", str(exc)) from exc
