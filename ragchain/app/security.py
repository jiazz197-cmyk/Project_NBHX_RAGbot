"""JWT 本地验签（.dsh/ragchain-interfaces.md §3）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

from .config import settings
from .errors import AuthError


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    token: str
    claims: dict


def verify_bearer(authorization: str | None) -> AuthContext:
    """校验 ``Authorization: Bearer xxx``，失败抛 :class:`AuthError`。"""
    if not authorization or not isinstance(authorization, str):
        raise AuthError("缺少 Authorization Bearer 凭证")

    parts = authorization.strip().split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError("Authorization 头格式应为 'Bearer <token>'")

    token = parts[1].strip()
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("身份凭证已过期") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("无效的身份凭证") from exc
    except Exception as exc:  # noqa: BLE001 - 任意解码异常统一按认证失败处理
        raise AuthError("身份凭证校验失败") from exc

    sub = claims.get("sub")
    if sub is None or not str(sub).strip():
        raise AuthError("身份凭证缺少 sub")

    return AuthContext(user_id=str(sub), token=token, claims=dict(claims))
