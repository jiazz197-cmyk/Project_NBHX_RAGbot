"""Chat summary adapters implementing ports with existing infrastructure."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import normalize_self_user_identifier
from app.adapters.chat_archive.message_extractor import (
    UserProfileDB,
    update_user_profile_with_new_queries,
)
from app.models.orm.platform.user import User
from app.ports.contracts.identity import CurrentUserPort, ROLE_SUPERUSER, ROLE_ADMIN
from app.ports.outbound.chat_summary import ChatArchivePort, ChatSummaryRepoPort, UserLookupPort
from app.ports.dto.chat_summary import ChatSummaryResult


class SqlAlchemyUserLookupAdapter(UserLookupPort):
    """Resolve effective user identifier with current RBAC rules."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def resolve_effective_user_id(
        self, requested_user_id: str, current_user: CurrentUserPort
    ) -> str:
        candidate = (requested_user_id or "").strip()
        current_aliases = {
            str(current_user.id).strip(),
            (current_user.username or "").strip(),
            (getattr(current_user, "name", "") or "").strip(),
        }
        current_aliases.discard("")

        if candidate in current_aliases:
            return (current_user.username or "").strip() or str(current_user.id)

        if not current_user.is_admin_like():
            normalize_self_user_identifier(candidate, current_user)
            return (current_user.username or "").strip() or str(current_user.id)

        try:
            lookup_uuid = uuid.UUID(candidate)
        except ValueError:
            return candidate

        result = await self._db.execute(select(User).filter(User.id == lookup_uuid))
        target_user = result.scalars().first()
        if not target_user:
            return candidate
        return (target_user.username or "").strip() or str(target_user.id)


class UserProfileSummaryRepoAdapter(ChatSummaryRepoPort):
    """Read summary data from chat message archive profile storage."""

    def __init__(self):
        self._repo = UserProfileDB()

    def get_latest_summary(self, user_id: str):
        return self._repo.get_latest_summary(user_id)


class MessageExtractorChatArchiveAdapter(ChatArchivePort):
    """Delegate summary generation workflow to existing integration service."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    async def update_user_profile(self, user_id: str, conversation_id: str, limit: int) -> ChatSummaryResult:
        result = await update_user_profile_with_new_queries(
            api_key=self._api_key,
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
        )
        return ChatSummaryResult(
            user_id=result.get("user_id", user_id),
            conversation_id=result.get("conversation_id", conversation_id),
            query_count=int(result.get("query_count", 0) or 0),
            previous_summary=result.get("previous_summary"),
            new_summary=result.get("new_summary"),
            is_first_time=bool(result.get("is_first_time", False)),
            db_updated=bool(result.get("db_updated", False)),
        )
