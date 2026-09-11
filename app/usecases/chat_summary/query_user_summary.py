"""Usecase: query latest user chat summary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.chat_summary import ChatSummaryRepoPort, UserLookupPort


@dataclass
class QueryUserSummaryQuery:
    user_id: str
    current_user: CurrentUserPort


@dataclass
class QueryUserSummaryResult:
    user_id: str
    latest_summary: Optional[str]
    exists: bool


class QueryUserSummaryUseCase:
    def __init__(self, user_lookup: UserLookupPort, summary_repo: ChatSummaryRepoPort):
        self._user_lookup = user_lookup
        self._summary_repo = summary_repo

    async def execute(self, query: QueryUserSummaryQuery) -> QueryUserSummaryResult:
        effective_user_id = await self._user_lookup.resolve_effective_user_id(
            requested_user_id=query.user_id,
            current_user=query.current_user,
        )
        latest_summary = self._summary_repo.get_latest_summary(effective_user_id)
        return QueryUserSummaryResult(
            user_id=effective_user_id,
            latest_summary=latest_summary,
            exists=latest_summary is not None,
        )
