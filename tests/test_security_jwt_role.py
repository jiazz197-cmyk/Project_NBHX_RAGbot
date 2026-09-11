"""JWT role claim 单元测试（B1：登录签发 token 时写入 role）。"""
from __future__ import annotations

import jwt

from app.core.config import settings
from app.core.security import create_access_token


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def test_create_access_token_embeds_role_claim():
    token = create_access_token(subject="user-123", role="admin")
    payload = _decode(token)
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"


def test_create_access_token_omits_role_when_default():
    # 不传 role（默认 None）→ 无 role claim，兼容旧 token
    token = create_access_token(subject="user-123")
    payload = _decode(token)
    assert payload["sub"] == "user-123"
    assert "role" not in payload


def test_create_access_token_explicit_none_omits_claim():
    token = create_access_token(subject="user-123", role=None)
    payload = _decode(token)
    assert "role" not in payload


def test_create_access_token_superuser_role_round_trip():
    token = create_access_token(subject="user-456", role="superuser")
    payload = _decode(token)
    assert payload["role"] == "superuser"
