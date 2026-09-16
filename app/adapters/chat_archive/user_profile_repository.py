"""Long-term memory persistence: the per-user latest profile summary.

Replaces the previous raw-psycopg2 ``UserProfileDB`` helper, which ran a
blocking synchronous driver call inside async request handlers and created its
table lazily outside the ORM schema.  The table itself
(``user_chat_profile``) is unchanged and now managed by ``Base.metadata``.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.models.orm.chat import UserChatProfile

logger = get_logger("chat_archive.user_profile_repository")


class SqlAlchemyUserProfileRepositoryAdapter:
    """Implements ``ChatSummaryRepoPort`` on top of ``user_chat_profile``."""

    async def get_latest_summary(self, user_id: str) -> Optional[str]:
        """Return the stored summary for ``user_id``, or ``None``."""

        try:
            async with AsyncSessionLocal() as db:
                row = await db.get(UserChatProfile, user_id)
        except SQLAlchemyError as exc:
            logger.error("Failed to read summary for user %s: %s", user_id, exc)
            return None

        if row is None:
            logger.info("No existing summary for user %s", user_id)
            return None
        logger.info("Found existing summary for user %s", user_id)
        return row.latest_summary

    async def upsert_latest_summary(self, user_id: str, latest_summary: str) -> bool:
        """Insert or update the latest summary for ``user_id``."""

        now = utcnow_naive()
        try:
            async with AsyncSessionLocal() as db:
                row = await db.get(UserChatProfile, user_id)
                if row is None:
                    db.add(
                        UserChatProfile(
                            user_id=user_id,
                            latest_summary=latest_summary,
                            update_time=now,
                        )
                    )
                else:
                    row.latest_summary = latest_summary
                    row.update_time = now
                await db.commit()
        except IntegrityError:
            # Concurrent first write for the same user: fall back to an update.
            try:
                async with AsyncSessionLocal() as db:
                    row = await db.get(UserChatProfile, user_id)
                    if row is None:
                        logger.error(
                            "Concurrent upsert lost the row for user %s", user_id
                        )
                        return False
                    row.latest_summary = latest_summary
                    row.update_time = now
                    await db.commit()
            except SQLAlchemyError as exc:
                logger.error(
                    "Failed to upsert summary for user %s after conflict: %s",
                    user_id,
                    exc,
                )
                return False
        except SQLAlchemyError as exc:
            logger.error("Failed to upsert summary for user %s: %s", user_id, exc)
            return False

        logger.info("Successfully updated summary for user %s", user_id)
        return True


__all__ = ["SqlAlchemyUserProfileRepositoryAdapter"]
