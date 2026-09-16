"""Chat memory ORM models: conversations, messages and the long-term profile.

These tables back the reserved memory interfaces used by the future RAG
container:

* ``chat_conversation`` / ``chat_message`` — short-term memory (per-user
  conversation and message history).
* ``user_chat_profile`` — long-term memory (per-user latest profile summary).
  The table name is historical and kept unchanged.

``user_id`` is always the **internal user id** (the JWT ``sub`` UUID rendered as
a string), never the username: the internal id is stable, which is what
per-user memory isolation must key on.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.core.time_utils import utcnow_naive
from app.models.orm.platform.base import Base


class ChatConversation(Base):
    """One chat conversation owned by exactly one user."""

    __tablename__ = "chat_conversation"

    id = Column(
        String(64),
        primary_key=True,
        comment="会话 ID（调用方提供或服务端生成 uuid4）",
    )
    user_id = Column(
        String(128),
        nullable=False,
        index=True,
        comment="归属用户（users.id 的字符串形式）",
    )
    name = Column(String(255), nullable=False, default="", comment="会话名称")
    inputs = Column(
        JSON,
        nullable=False,
        default=dict,
        comment="创建时附加输入（检索模式等）",
    )
    status = Column(
        String(16), nullable=False, default="normal", comment="normal / archived"
    )
    introduction = Column(Text, nullable=False, default="", comment="会话简介")
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)
    updated_at = Column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )

    __table_args__ = (
        Index("ix_chat_conversation_user_updated", "user_id", "updated_at"),
    )


class ChatMessage(Base):
    """One persisted chat message belonging to a conversation and a user."""

    __tablename__ = "chat_message"

    # SQLite (used by the DB-less test suite) only auto-increments an exact
    # ``INTEGER PRIMARY KEY``; PostgreSQL keeps the wider BIGINT.
    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    conversation_id = Column(
        String(64),
        ForeignKey("chat_conversation.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised owner column: every per-user read filters on it directly, so
    # message queries never depend on a join to prove ownership.
    user_id = Column(String(128), nullable=False, index=True)
    role = Column(
        String(16), nullable=False, default="user", comment="user / assistant / system"
    )
    content = Column(Text, nullable=False, default="")
    query = Column(
        Text,
        nullable=False,
        default="",
        comment="用户提问原文（前端按 query/answer 成对渲染）",
    )
    answer = Column(Text, nullable=False, default="", comment="助手回答原文")
    # ``metadata`` is reserved on the declarative class; the column keeps its
    # external name while the attribute is ``meta``.
    meta = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=utcnow_naive)

    __table_args__ = (
        Index("ix_chat_message_conversation_id_id", "conversation_id", "id"),
        Index("ix_chat_message_user_role", "user_id", "role"),
    )


class UserChatProfile(Base):
    """Long-term memory: the latest profile summary of one user."""

    __tablename__ = "user_chat_profile"

    user_id = Column(
        String(128),
        primary_key=True,
        comment="归属用户（users.id 的字符串形式）",
    )
    latest_summary = Column(Text, nullable=True, comment="最新画像摘要")
    update_time = Column(
        DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive
    )


__all__ = ["ChatConversation", "ChatMessage", "UserChatProfile"]
