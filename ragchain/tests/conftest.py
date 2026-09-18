"""平台测试公共夹具：测试用 JWT 签发。"""

from __future__ import annotations

import time
from typing import Any, Callable

import jwt
import pytest

from app.config import settings


@pytest.fixture
def jwt_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    secret = "ragchain-platform-unit-test-secret"
    monkeypatch.setattr(settings, "SECRET_KEY", secret)
    monkeypatch.setattr(settings, "ALGORITHM", "HS256")
    return secret


@pytest.fixture
def make_token(jwt_secret: str) -> Callable[..., str]:
    def _make(
        sub: str | None = "00000000-0000-0000-0000-0000000000aa",
        *,
        secret: str | None = None,
        algorithm: str = "HS256",
        expires_in: float = 3600,
        **claims: Any,
    ) -> str:
        payload: dict[str, Any] = dict(claims)
        if sub is not None:
            payload["sub"] = sub
        if expires_in is not None:
            payload["exp"] = int(time.time() + expires_in)
        return jwt.encode(payload, secret or jwt_secret, algorithm=algorithm)

    return _make
