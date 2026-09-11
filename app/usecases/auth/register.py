"""Register use case."""

from __future__ import annotations

from app.core.exceptions import APIException
from app.core.logging import get_logger
from app.ports.outbound.auth import PasswordHasherPort, UserRepositoryPort
from app.ports.dto.auth import RegisterCommand

logger = get_logger("auth.register")


class RegisterUseCase:
    """Create a new user account."""

    def __init__(self, user_repo: UserRepositoryPort, password_hasher: PasswordHasherPort):
        self._user_repo = user_repo
        self._password_hasher = password_hasher

    async def execute(self, cmd: RegisterCommand):
        existing = await self._user_repo.get_by_username(cmd.username)
        if existing:
            raise APIException("用户名已存在", status_code=409, error_code="USERNAME_EXISTS")

        hashed = self._password_hasher.hash_password(cmd.password)
        user = await self._user_repo.create(
            username=cmd.username,
            email=cmd.email,
            password=hashed,
            name=cmd.name,
        )

        logger.info("User registered: %s", cmd.username)
        return user
