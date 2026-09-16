"""Shared authorization helpers for reserved chat use cases."""

from __future__ import annotations

from typing import Optional

from app.core.exceptions import PermissionDeniedError
from app.ports.contracts.identity import CurrentUserPort


def resolve_effective_chat_user_id(
    requested_user_id: Optional[str],
    current_user: CurrentUserPort,
) -> str:
    """Resolve the user id used for conversation ownership checks.

    ``None`` / empty always means "the JWT current user".  Non-admin users may
    only pass their own id/username/name aliases; admin-like users may inspect
    another user's conversations.
    """

    requested = (requested_user_id or "").strip()
    current_id = str(current_user.id).strip()
    aliases = {
        current_id,
        (current_user.username or "").strip(),
        (getattr(current_user, "name", "") or "").strip(),
    }
    aliases.discard("")

    if not requested or requested in aliases:
        # JWT subject is the authoritative internal user id.
        return current_id

    if current_user.is_admin_like():
        return requested

    raise PermissionDeniedError("无权访问其他用户的会话")
