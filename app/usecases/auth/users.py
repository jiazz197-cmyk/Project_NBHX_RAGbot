"""Admin user management use cases."""

from __future__ import annotations

from app.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from app.core.logging import get_logger
from app.domain.auth.page_permissions import (
    InvalidPagePermissionKey,
    normalize_page_permissions,
)
from app.ports.contracts.identity import CurrentUserPort
from app.ports.outbound.auth import PasswordHasherPort, UserRepositoryPort
from app.ports.dto.auth import (
    UpdatePagePermissionsCommand,
    UpdateUserRoleCommand,
    ResetUserPasswordCommand,
    UserDTO,
)

logger = get_logger("auth.users")


class GetUserUseCase:
    """Fetch a single user by ID."""

    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: str) -> UserDTO:
        user = await self._user_repo.get_by_id(user_id)
        if not user:
            raise NotFoundError("用户不存在")
        return user


class ListUsersUseCase:
    """Fetch all users."""

    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self) -> list[UserDTO]:
        return await self._user_repo.list_users()


class DeleteUserUseCase:
    """Remove a user (admin-only)."""

    def __init__(self, user_repo: UserRepositoryPort, current_user: CurrentUserPort):
        self._user_repo = user_repo
        self._current_user = current_user

    async def execute(self, user_id: str) -> None:
        if not self._current_user.is_admin_like():
            raise PermissionDeniedError("需要管理员权限")

        target = await self._user_repo.get_by_id(user_id)
        if not target:
            raise NotFoundError("用户不存在")

        await self._user_repo.delete(user_id)
        logger.info(
            "User deleted: %s by %s (id=%s)",
            user_id,
            self._current_user.username,
            self._current_user.id,
        )


class UpdateUserRoleUseCase:
    """Change a user's role (admin-only)."""

    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, cmd: UpdateUserRoleCommand) -> UserDTO:
        user = await self._user_repo.update_role(cmd.target_user_id, cmd.new_role)
        if not user:
            raise NotFoundError("用户不存在")

        logger.info(
            "User role updated: %s -> %s by %s (id=%s)",
            cmd.target_user_id,
            cmd.new_role,
            cmd.current_user_name,
            cmd.current_user_id,
        )
        return user

class ResetUserPasswordUseCase:
    """Reset another user's password (superuser-only)."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        password_hasher: PasswordHasherPort,
    ):
        self._user_repo = user_repo
        self._password_hasher = password_hasher

    async def execute(self, cmd: ResetUserPasswordCommand) -> None:
        target = await self._user_repo.get_by_id(cmd.target_user_id)
        if not target:
            raise NotFoundError("用户不存在")

        # 不允许重置 superuser
        if target.role == "superuser":
            raise PermissionDeniedError("不可重置 superuser")

        # 不允许重置自己
        if target.id == cmd.current_user_id:
            raise PermissionDeniedError("不可重置自己")

        hashed = self._password_hasher.hash_password(cmd.new_password)

        await self._user_repo.update_password(
            cmd.target_user_id,
            hashed,
        )

        logger.info(
            "User password reset: %s by %s (id=%s)",
            cmd.target_user_id,
            cmd.current_user_name,
            cmd.current_user_id,
        )


class UpdateUserPagePermissionsUseCase:
    """Toggle generic page-visibility permissions for a user.

    The use case is deliberately page-key agnostic: callers provide a
    ``{page_key: enabled}`` mapping.  No page key is seeded in this release and
    the HTTP endpoint is disabled by default, but the framework is kept for
    later business pages.
    """

    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, cmd: UpdatePagePermissionsCommand) -> UserDTO:
        target = await self._user_repo.get_by_id(cmd.target_user_id)
        if not target:
            raise NotFoundError("用户不存在")

        try:
            page_permissions = normalize_page_permissions(cmd.page_permissions)
        except InvalidPagePermissionKey as exc:
            raise ValidationError(str(exc)) from exc

        updated = await self._user_repo.update_page_permissions(
            cmd.target_user_id,
            page_permissions,
        )
        logger.info(
            "User page permissions updated: %s (%s) by %s",
            cmd.target_user_id,
            ",".join(sorted(page_permissions)) or "-",
            cmd.current_user_id,
        )
        return updated if isinstance(updated, UserDTO) else target
