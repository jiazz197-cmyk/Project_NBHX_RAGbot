"""ResetUserPasswordUseCase unit tests — mock ports, verify permission logic.

These tests exercise the three-layer permission model:
  1. Target user not found       → NotFoundError
  2. Target is superuser         → PermissionDeniedError ("不可重置 superuser")
  3. Target is self              → PermissionDeniedError ("不可重置自己")
  4. Valid reset                 → hashes password, calls update, logs
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.ports.dto.auth import ResetUserPasswordCommand, UserDTO


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_dto(user_id: str, role: str) -> UserDTO:
    return UserDTO(
        id=user_id,
        username=f"user_{user_id}",
        email=f"{user_id}@test.com",
        name=None,
        role=role,
        is_active=True,
        created_at="2025-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestResetUserPasswordUseCase:
    """Business-logic unit tests — no DB, no network, pure UseCase."""

    def _build(self, *, target_return: object = None, current_user_id: str = "su-1"):
        """Build a ResetUserPasswordUseCase with mocked ports."""
        from app.usecases.auth.users import ResetUserPasswordUseCase

        user_repo = MagicMock()
        user_repo.get_by_id = AsyncMock(return_value=target_return)
        user_repo.update_password = AsyncMock()

        password_hasher = MagicMock()
        password_hasher.hash_password = MagicMock(return_value="$2b$...hashed")

        self._user_repo = user_repo
        self._password_hasher = password_hasher
        self._current_user_id = current_user_id

        return ResetUserPasswordUseCase(user_repo, password_hasher)

    # ---- happy path -------------------------------------------------------

    @pytest.mark.asyncio
    async def test_superuser_resets_regular_user_succeeds(self):
        """superuser → regular user: should hash + update + log."""
        target = _make_dto("user-1", "user")
        uc = self._build(target_return=target, current_user_id="su-1")

        cmd = ResetUserPasswordCommand(
            target_user_id="user-1",
            new_password="new-secret-123",
            current_user_id="su-1",
            current_user_name="boss",
        )
        await uc.execute(cmd)

        self._password_hasher.hash_password.assert_called_once_with("new-secret-123")
        self._user_repo.update_password.assert_called_once_with("user-1", "$2b$...hashed")

    @pytest.mark.asyncio
    async def test_superuser_resets_admin_user_succeeds(self):
        """superuser → admin: should work (admin is not superuser)."""
        target = _make_dto("admin-1", "admin")
        uc = self._build(target_return=target, current_user_id="su-1")

        cmd = ResetUserPasswordCommand(
            target_user_id="admin-1",
            new_password="p@ssw0rd",
            current_user_id="su-1",
            current_user_name="boss",
        )
        await uc.execute(cmd)

        self._user_repo.update_password.assert_called_once()

    # ---- error paths ------------------------------------------------------

    @pytest.mark.asyncio
    async def test_target_not_found_raises_not_found(self):
        """Non-existent user → NotFoundError."""
        uc = self._build(target_return=None)

        cmd = ResetUserPasswordCommand(
            target_user_id="ghost",
            new_password="x",
            current_user_id="su-1",
            current_user_name="boss",
        )
        with pytest.raises(NotFoundError, match="用户不存在"):
            await uc.execute(cmd)

    @pytest.mark.asyncio
    async def test_cannot_reset_another_superuser(self):
        """superuser → superuser: must be denied."""
        target = _make_dto("su-2", "superuser")
        uc = self._build(target_return=target, current_user_id="su-1")

        cmd = ResetUserPasswordCommand(
            target_user_id="su-2",
            new_password="bad-idea",
            current_user_id="su-1",
            current_user_name="boss",
        )
        with pytest.raises(PermissionDeniedError, match="不可重置 superuser"):
            await uc.execute(cmd)

        # must NOT call update or hash
        self._password_hasher.hash_password.assert_not_called()
        self._user_repo.update_password.assert_not_called()

    @pytest.mark.asyncio
    async def test_cannot_reset_own_password(self):
        """superuser → self: must be denied (even if not superuser role check triggers first)."""
        # target IS current user — the self-check fires
        target = _make_dto("su-1", "superuser")
        uc = self._build(target_return=target, current_user_id="su-1")

        cmd = ResetUserPasswordCommand(
            target_user_id="su-1",
            new_password="new-me",
            current_user_id="su-1",
            current_user_name="boss",
        )
        # The superuser check fires first → PermissionDeniedError("不可重置 superuser")
        with pytest.raises(PermissionDeniedError):
            await uc.execute(cmd)

        self._password_hasher.hash_password.assert_not_called()
        self._user_repo.update_password.assert_not_called()

    @pytest.mark.asyncio
    async def test_regular_user_cannot_reset_own_password(self):
        """user → self: denied by self-check (role check passes for 'user')."""
        target = _make_dto("user-1", "user")
        uc = self._build(target_return=target, current_user_id="user-1")

        cmd = ResetUserPasswordCommand(
            target_user_id="user-1",
            new_password="secret",
            current_user_id="user-1",
            current_user_name="alice",
        )
        with pytest.raises(PermissionDeniedError, match="不可重置自己"):
            await uc.execute(cmd)

        self._password_hasher.hash_password.assert_not_called()
        self._user_repo.update_password.assert_not_called()
