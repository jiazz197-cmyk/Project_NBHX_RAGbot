"""Chat summary adapters implementing ports with existing infrastructure."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import normalize_self_user_identifier
from app.adapters.chat_archive.memory_repository import (
    SqlAlchemyChatMemoryRepositoryAdapter,
)
from app.adapters.chat_archive.message_extractor import MessageExtractor
from app.adapters.chat_archive.user_profile_repository import (
    SqlAlchemyUserProfileRepositoryAdapter,
)
from app.models.orm.platform.user import User
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.chat import ChatMessageRepositoryPort
from app.ports.outbound.chat_summary import (
    ChatArchivePort,
    ChatSummaryRepoPort,
    UserLookupPort,
)
from app.ports.dto.chat_summary import ChatSummaryResult

logger = get_logger("chat_summary.adapters")


class SqlAlchemyUserLookupAdapter(UserLookupPort):
    """Resolve the effective user identifier with current RBAC rules.

    The canonical key is always the **internal user id** (``users.id`` rendered
    as a string), never the username: memory rows key on it, so they survive any
    later rename and match the JWT ``sub`` the RAG container sends.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def resolve_effective_user_id(
        self, requested_user_id: str, current_user: CurrentUserPort
    ) -> str:
        candidate = (requested_user_id or "").strip()
        current_id = str(current_user.id).strip()
        current_aliases = {
            current_id,
            (current_user.username or "").strip(),
            (getattr(current_user, "name", "") or "").strip(),
        }
        current_aliases.discard("")

        if not candidate or candidate in current_aliases:
            return current_id

        if not current_user.is_admin_like():
            normalize_self_user_identifier(candidate, current_user)
            return current_id

        # Admin-like callers may target another user by UUID or by username.
        try:
            lookup_uuid = uuid.UUID(candidate)
        except ValueError:
            lookup_uuid = None

        if lookup_uuid is not None:
            result = await self._db.execute(select(User).filter(User.id == lookup_uuid))
        else:
            result = await self._db.execute(
                select(User).filter(User.username == candidate)
            )
        target_user = result.scalars().first()
        if not target_user:
            # Unknown identifier: keep it verbatim so the caller sees an empty
            # summary instead of somebody else's data.
            return candidate
        return str(target_user.id)


class UserProfileSummaryRepoAdapter(ChatSummaryRepoPort):
    """Read/write long-term memory rows (async, ORM-backed)."""

    def __init__(
        self, repository: Optional[SqlAlchemyUserProfileRepositoryAdapter] = None
    ):
        self._repo = repository or SqlAlchemyUserProfileRepositoryAdapter()

    async def get_latest_summary(self, user_id: str) -> Optional[str]:
        return await self._repo.get_latest_summary(user_id)

    async def upsert_latest_summary(self, user_id: str, latest_summary: str) -> bool:
        return await self._repo.upsert_latest_summary(user_id, latest_summary)


class MessageExtractorChatArchiveAdapter(ChatArchivePort):
    """Generate the long-term profile summary from locally stored messages."""

    def __init__(
        self,
        message_repository: Optional[ChatMessageRepositoryPort] = None,
        profile_repository: Optional[ChatSummaryRepoPort] = None,
    ):
        self._message_repository = (
            message_repository or SqlAlchemyChatMemoryRepositoryAdapter()
        )
        self._profiles = profile_repository or UserProfileSummaryRepoAdapter()

    async def update_user_profile(
        self, user_id: str, conversation_id: str, limit: int
    ) -> ChatSummaryResult:
        extractor = MessageExtractor(self._message_repository)

        previous_summary = await self._profiles.get_latest_summary(user_id)
        is_first_time = previous_summary is None

        queries = await extractor.extract_queries(user_id, conversation_id, limit)
        if not queries:
            # Nothing to learn from yet: report the state without touching the
            # stored profile (an LLM call here would only invent content).
            logger.info(
                "No queries for user %s in conversation %s; profile untouched",
                user_id,
                conversation_id,
            )
            return ChatSummaryResult(
                user_id=user_id,
                conversation_id=conversation_id,
                query_count=0,
                previous_summary=previous_summary,
                new_summary=None,
                is_first_time=is_first_time,
                db_updated=False,
            )

        new_summary = await extractor.summarize_queries_with_llm(
            queries=queries,
            previous_summary=previous_summary,
        )
        db_updated = await self._profiles.upsert_latest_summary(user_id, new_summary)
        if db_updated:
            logger.info("Successfully updated user profile for %s", user_id)
        else:
            logger.error("Failed to update database for user %s", user_id)

        return ChatSummaryResult(
            user_id=user_id,
            conversation_id=conversation_id,
            query_count=len(queries),
            previous_summary=previous_summary,
            new_summary=new_summary,
            is_first_time=is_first_time,
            db_updated=db_updated,
        )
