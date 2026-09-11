"""Adapter: context compression integration."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ExternalServiceError
from app.adapters.context_compressor import (
    LlmEndpointMisconfiguredError,
    compress_context,
)
from app.ports.outbound.context_compression import ContextCompressorPort


class IntegrationContextCompressorAdapter(ContextCompressorPort):
    async def compress(self, context_data: dict) -> Any:
        try:
            return await compress_context(context_data)
        except LlmEndpointMisconfiguredError as e:
            raise ExternalServiceError("context_compression", str(e)) from e
