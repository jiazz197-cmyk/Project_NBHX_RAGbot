"""DTO: chat summary use case results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ChatSummaryResult:
    user_id: str
    conversation_id: str
    query_count: int
    previous_summary: Optional[str]
    new_summary: str
    is_first_time: bool
    db_updated: bool
