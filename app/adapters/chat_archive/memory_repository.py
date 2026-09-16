"""SQLAlchemy-backed per-user chat memory repository.

Implements both driven ports of the reserved memory layer:

* :class:`app.ports.outbound.chat.ConversationStorePort` — conversation and
  message CRUD used by the HTTP memory API (short-term memory).
* :class:`app.ports.outbound.chat.ChatMessageRepositoryPort` — the read-only
  message source consumed by the long-term summary and context-compression
  adapters.

Every method scopes on the internal user id (JWT ``sub``), so rows of different
users never mix.  The session-per-method pattern matches the other SQLAlchemy
adapters in this repository.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, func, select, update

from app.core.database import AsyncSessionLocal
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.logging import get_logger
from app.core.time_utils import utcnow_naive
from app.models.orm.chat import ChatConversation, ChatMessage
from app.ports.dto.chat import (
    AppendMessagesCommand,
    ConversationDTO,
    ConversationPage,
    ConversationPageQuery,
    CreateConversationCommand,
    DeleteConversationCommand,
    MessageDTO,
    MessageInput,
    MessagePage,
    MessagePageQuery,
    RenameConversationCommand,
)

logger = get_logger("chat_archive.memory_repository")

# Mirrors the DB column widths so a too-long id fails fast with a clear error
# instead of a driver-level truncation error.
_MAX_ID_LEN = 64
_MAX_USER_ID_LEN = 128
_VALID_ROLES = ("user", "assistant", "system")
_OWNERSHIP_ERROR = "会话不存在或不属于当前用户"


def _to_epoch(value: Optional[datetime]) -> int:
    """Render a stored naive-UTC datetime as epoch seconds (frontend shape)."""

    if value is None:
        return 0
    if value.tzinfo is not None:
        return int(value.timestamp())
    return calendar.timegm(value.utctimetuple())


def _from_epoch(value: Optional[int]) -> datetime:
    """Convert caller-provided epoch seconds to a naive-UTC datetime."""

    if not value:
        return utcnow_naive()
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return utcnow_naive()


def _message_text(item: MessageInput) -> str:
    """Pick the display text of an inbound message.

    Callers may send only ``content`` (the primary field) or only the legacy
    ``query`` / ``answer`` pair, so fall back to whichever is present.
    """

    content = (item.content or "").strip()
    if content:
        return item.content
    role = (item.role or "").strip().lower()
    fallback = item.answer if role == "assistant" else item.query
    return fallback or ""


def _conversation_to_dto(row: ChatConversation) -> ConversationDTO:
    return ConversationDTO(
        id=str(row.id),
        name=str(row.name or ""),
        user_id=str(row.user_id or ""),
        inputs=dict(row.inputs or {}),
        status=str(row.status or "normal"),
        introduction=str(row.introduction or ""),
        created_at=_to_epoch(row.created_at),
        updated_at=_to_epoch(row.updated_at),
    )


def _message_to_dto(row: ChatMessage) -> MessageDTO:
    return MessageDTO(
        id=str(row.id),
        conversation_id=str(row.conversation_id),
        role=str(row.role or ""),
        content=str(row.content or ""),
        query=str(row.query or ""),
        answer=str(row.answer or ""),
        created_at=_to_epoch(row.created_at),
        metadata=dict(row.meta or {}),
    )


def _dialogue_line(row: ChatMessage) -> str:
    """Render one stored message as a single compression/archive line."""

    text = (row.content or "").strip() or (row.answer or "").strip() or (
        row.query or ""
    ).strip()
    if not text:
        return ""
    role = (row.role or "").strip().lower()
    speaker = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role or "未知")
    return f"{speaker}: {text}"


class SqlAlchemyChatMemoryRepositoryAdapter:
    """Conversation + message store backed by PostgreSQL (one user per row)."""

    # ---------------------------------------------------------------- helpers

    @staticmethod
    async def _require_owned_conversation(
        db, user_id: str, conversation_id: str
    ) -> ChatConversation:
        """Return the conversation row or fail with the shared ownership error.

        A conversation owned by somebody else is reported as "not found" so the
        response never leaks the existence of another user's conversation.
        """

        result = await db.execute(
            select(ChatConversation).where(
                ChatConversation.id == conversation_id,
                ChatConversation.user_id == user_id,
            )
        )
        row = result.scalars().first()
        if row is None:
            raise NotFoundError(_OWNERSHIP_ERROR)
        return row

    @staticmethod
    async def _load_dialogues(
        db, user_id: str, conversation_id: str, limit: int, skip_newest: int
    ) -> list[str]:
        """Load dialogue lines, oldest-first, optionally skipping the newest N."""

        limit = max(1, int(limit))
        skip_newest = max(0, int(skip_newest))
        total = await db.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.user_id == user_id,
            )
        )
        available = max(0, int(total or 0) - skip_newest)
        take = min(limit, available)
        if take <= 0:
            return []

        # Walk backwards from the newest row so ``skip_newest`` really skips the
        # most recent turns, then restore chronological order for the prompt.
        result = await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.user_id == user_id,
            )
            .order_by(ChatMessage.id.desc())
            .offset(skip_newest)
            .limit(take)
        )
        lines = [_dialogue_line(row) for row in reversed(result.scalars().all())]
        return [line for line in lines if line]

    # ------------------------------------------- ConversationStorePort (CRUD)

    async def create_conversation(
        self, command: CreateConversationCommand
    ) -> ConversationDTO:
        conversation_id = (command.conversation_id or "").strip() or str(uuid.uuid4())
        if len(conversation_id) > _MAX_ID_LEN:
            raise ValueError(
                f"conversation_id must be at most {_MAX_ID_LEN} characters"
            )

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatConversation).where(ChatConversation.id == conversation_id)
            )
            existing = result.scalars().first()
            if existing is not None:
                if str(existing.user_id) != command.user_id:
                    raise PermissionDeniedError("会话 ID 已被占用")
                # Idempotent create: the caller may safely retry.
                return _conversation_to_dto(existing)

            row = ChatConversation(
                id=conversation_id,
                user_id=command.user_id[:_MAX_USER_ID_LEN],
                name=(command.name or "")[:255],
                inputs=dict(command.inputs or {}),
                status="normal",
                introduction="",
                created_at=utcnow_naive(),
                updated_at=utcnow_naive(),
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            logger.info(
                "Created conversation %s for user %s", conversation_id, command.user_id
            )
            return _conversation_to_dto(row)

    async def append_messages(
        self, command: AppendMessagesCommand
    ) -> list[MessageDTO]:
        if not command.messages:
            return []

        async with AsyncSessionLocal() as db:
            await self._require_owned_conversation(
                db, command.user_id, command.conversation_id
            )

            rows: list[ChatMessage] = []
            for item in command.messages:
                role = (item.role or "").strip().lower()
                if role not in _VALID_ROLES:
                    raise ValueError(
                        f"role must be one of {', '.join(_VALID_ROLES)}"
                    )
                rows.append(
                    ChatMessage(
                        conversation_id=command.conversation_id,
                        user_id=command.user_id[:_MAX_USER_ID_LEN],
                        role=role,
                        content=_message_text(item),
                        query=item.query or "",
                        answer=item.answer or "",
                        meta=dict(item.metadata or {}),
                        created_at=_from_epoch(item.created_at),
                    )
                )

            db.add_all(rows)
            await db.execute(
                update(ChatConversation)
                .where(
                    ChatConversation.id == command.conversation_id,
                    ChatConversation.user_id == command.user_id,
                )
                .values(updated_at=utcnow_naive())
            )
            await db.commit()
            for row in rows:
                await db.refresh(row)

            logger.info(
                "Appended %s messages to conversation %s",
                len(rows),
                command.conversation_id,
            )
            return [_message_to_dto(row) for row in rows]

    async def list_conversations(
        self, query: ConversationPageQuery
    ) -> ConversationPage:
        page = max(1, int(query.page))
        limit = max(1, int(query.limit))
        offset = (page - 1) * limit

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatConversation)
                .where(ChatConversation.user_id == query.user_id)
                .order_by(
                    ChatConversation.updated_at.desc(), ChatConversation.id.desc()
                )
                .offset(offset)
                .limit(limit + 1)
            )
            rows = list(result.scalars().all())

        has_more = len(rows) > limit
        return ConversationPage(
            page=page,
            limit=limit,
            has_more=has_more,
            data=[_conversation_to_dto(row) for row in rows[:limit]],
        )

    async def list_messages(self, query: MessagePageQuery) -> MessagePage:
        page = max(1, int(query.page))
        limit = max(1, int(query.limit))
        offset = (page - 1) * limit

        async with AsyncSessionLocal() as db:
            await self._require_owned_conversation(
                db, query.user_id, query.conversation_id
            )
            # Page 1 is the newest window; rows are returned oldest-first so the
            # frontend can render them top-to-bottom without re-sorting.
            result = await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == query.conversation_id,
                    ChatMessage.user_id == query.user_id,
                )
                .order_by(ChatMessage.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
            rows = list(result.scalars().all())

        has_more = len(rows) > limit
        window = list(reversed(rows[:limit]))
        return MessagePage(
            page=page,
            limit=limit,
            has_more=has_more,
            data=[_message_to_dto(row) for row in window],
        )

    async def rename_conversation(
        self, command: RenameConversationCommand
    ) -> ConversationDTO:
        async with AsyncSessionLocal() as db:
            row = await self._require_owned_conversation(
                db, command.user_id, command.conversation_id
            )
            name = (command.name or "").strip()
            if not name and command.auto_generate:
                name = await self._derive_name(db, command.user_id, command.conversation_id)
            row.name = name[:255]
            row.updated_at = utcnow_naive()
            await db.commit()
            await db.refresh(row)
            logger.info(
                "Renamed conversation %s for user %s",
                command.conversation_id,
                command.user_id,
            )
            return _conversation_to_dto(row)

    @staticmethod
    async def _derive_name(db, user_id: str, conversation_id: str) -> str:
        """Derive a conversation title from its first user query."""

        result = await db.execute(
            select(ChatMessage)
            .where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.user_id == user_id,
                ChatMessage.role == "user",
            )
            .order_by(ChatMessage.id.asc())
            .limit(1)
        )
        first = result.scalars().first()
        if first is None:
            return ""
        text = (first.query or first.content or "").strip().replace("\n", " ")
        return text[:50]

    async def delete_conversation(
        self, command: DeleteConversationCommand
    ) -> None:
        async with AsyncSessionLocal() as db:
            await self._require_owned_conversation(
                db, command.user_id, command.conversation_id
            )
            # Explicit child delete keeps the behaviour identical on engines
            # that do not enforce ON DELETE CASCADE (e.g. SQLite in tests).
            await db.execute(
                delete(ChatMessage).where(
                    ChatMessage.conversation_id == command.conversation_id,
                    ChatMessage.user_id == command.user_id,
                )
            )
            await db.execute(
                delete(ChatConversation).where(
                    ChatConversation.id == command.conversation_id,
                    ChatConversation.user_id == command.user_id,
                )
            )
            await db.commit()
            logger.info(
                "Deleted conversation %s for user %s",
                command.conversation_id,
                command.user_id,
            )

    # ---------------------------------------- ChatMessageRepositoryPort (read)

    async def list_message_queries(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return the newest ``limit`` user queries, oldest-first."""

        limit = max(1, int(limit))
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.user_id == user_id,
                    ChatMessage.role == "user",
                )
                .order_by(ChatMessage.id.desc())
                .limit(limit)
            )
            rows = list(reversed(result.scalars().all()))

        queries = [
            (row.query or row.content or "").strip()
            for row in rows
        ]
        return [query for query in queries if query]

    async def list_recent_dialogues(
        self, user_id: str, conversation_id: str, limit: int
    ) -> list[str]:
        """Return the newest ``limit`` dialogue lines, oldest-first."""

        async with AsyncSessionLocal() as db:
            return await self._load_dialogues(
                db, user_id, conversation_id, limit, skip_newest=0
            )

    async def list_older_dialogues(
        self,
        user_id: str,
        conversation_id: str,
        limit: int,
        recent: int = 0,
    ) -> list[str]:
        """Return up to ``limit`` dialogue lines older than the newest ``recent``."""

        async with AsyncSessionLocal() as db:
            return await self._load_dialogues(
                db, user_id, conversation_id, limit, skip_newest=max(0, int(recent))
            )


__all__ = ["SqlAlchemyChatMemoryRepositoryAdapter"]
