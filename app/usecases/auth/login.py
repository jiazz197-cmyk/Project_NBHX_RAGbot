"""Login use case."""

from __future__ import annotations

from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.security import password_version
from app.ports.outbound.auth import PasswordHasherPort, TokenIssuerPort, UserRepositoryPort
from app.ports.dto.auth import LoginCommand, TokenPair

logger = get_logger("auth.login")


class LoginUseCase:
    """Authenticate user credentials and return JWT token."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        password_hasher: PasswordHasherPort,
        token_issuer: TokenIssuerPort,
    ):
        self._user_repo = user_repo
        self._password_hasher = password_hasher
        self._token_issuer = token_issuer

    async def execute(self, cmd: LoginCommand) -> TokenPair:
        user = await self._user_repo.get_by_username(cmd.username)
        if not user:
            raise AuthenticationError("用户名或密码错误")

        if not getattr(user, "is_active", True):
            raise AuthenticationError("账号已禁用")

        password_hash = getattr(user, "password", "")
        if not self._password_hasher.verify_password(cmd.password, password_hash):
            raise AuthenticationError("用户名或密码错误")

        user_id = str(getattr(user, "id", ""))
        raw_role = getattr(user, "role", None)
        # 与 load_user_for_websocket 一致：兼容 enum 与裸字符串
        role = raw_role.value if hasattr(raw_role, "value") else (str(raw_role) if raw_role is not None else None)
        pv = password_version(password_hash)
        tokens = self._token_issuer.create_tokens(user_id, role, pv)
        logger.info("User logged in: %s (id=%s)", getattr(user, "username", ""), user_id)
        return TokenPair(access_token=tokens["access_token"])
