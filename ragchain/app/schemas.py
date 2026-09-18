"""ragchain 请求/响应模型（.dsh/ragchain-interfaces.md §4）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SEARCH_MODE_WEB = "联网搜索"
SEARCH_MODE_LOCAL = "本地检索"
SEARCH_MODE_BOTH = "本地&网络"
VALID_SEARCH_MODES = {SEARCH_MODE_WEB, SEARCH_MODE_LOCAL, SEARCH_MODE_BOTH}


class ChatMessageRequest(BaseModel):
    query: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = Field(default=None, max_length=128)
    search_mode: str = SEARCH_MODE_BOTH
    inputs: dict[str, Any] = Field(default_factory=dict)
    response_mode: str = "streaming"


class ErrorResponse(BaseModel):
    message: str
    error_code: str
