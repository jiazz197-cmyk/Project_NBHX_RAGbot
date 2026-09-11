"""限流按角色分级单元测试（B2：admin/superuser 获更高预算）。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.middleware.rate_limit import RateLimiter
from app.core.security import create_access_token
from app.ports.contracts.identity import ROLE_ADMIN, ROLE_SUPERUSER, ROLE_USER


class FakeRequest:
    """最小请求桩：headers.get / url.path / client。"""

    def __init__(self, path: str, auth_header: str | None = None) -> None:
        self.headers = {"Authorization": auth_header} if auth_header else {}
        self.url = SimpleNamespace(path=path)
        self.client = None  # anon 路径才读，host 取 "unknown"


def _limiter() -> RateLimiter:
    return RateLimiter()


@pytest.mark.parametrize(
    "role,is_admin",
    [
        (ROLE_ADMIN, True),
        (ROLE_SUPERUSER, True),
        (ROLE_USER, False),
        (None, False),
    ],
)
def test_normal_path_limit_tiered_by_role(role, is_admin):
    limiter = _limiter()
    req = FakeRequest(path=f"{settings.API_V1_STR}/quotation/tasks")
    limit = limiter._get_limit_for_request(req, is_authenticated=True, role=role)
    expected = settings.RATE_LIMIT_AUTH_ADMIN if is_admin else settings.RATE_LIMIT_AUTH
    assert limit == expected


@pytest.mark.parametrize(
    "role,is_admin",
    [
        (ROLE_ADMIN, True),
        (ROLE_SUPERUSER, True),
        (ROLE_USER, False),
        (None, False),
    ],
)
def test_expensive_path_limit_tiered_by_role(role, is_admin):
    limiter = _limiter()
    req = FakeRequest(path=f"{settings.API_V1_STR}/sqlserver/query")
    limit = limiter._get_limit_for_request(req, is_authenticated=True, role=role)
    expected = (
        settings.RATE_LIMIT_EXPENSIVE_AUTH_ADMIN
        if is_admin
        else settings.RATE_LIMIT_EXPENSIVE_AUTH
    )
    assert limit == expected


def test_anon_limits_unchanged():
    limiter = _limiter()
    normal = FakeRequest(path=f"{settings.API_V1_STR}/quotation/tasks")
    assert limiter._get_limit_for_request(normal, is_authenticated=False, role=None) == settings.RATE_LIMIT_ANON
    expensive = FakeRequest(path=f"{settings.API_V1_STR}/sqlserver/query")
    assert (
        limiter._get_limit_for_request(expensive, is_authenticated=False, role=None)
        == settings.RATE_LIMIT_EXPENSIVE_ANON
    )


def test_admin_budget_higher_than_normal():
    assert settings.RATE_LIMIT_AUTH_ADMIN > settings.RATE_LIMIT_AUTH
    assert settings.RATE_LIMIT_EXPENSIVE_AUTH_ADMIN > settings.RATE_LIMIT_EXPENSIVE_AUTH


def test_get_client_identifier_extracts_role_from_jwt():
    limiter = _limiter()
    token = create_access_token(subject="user-123", role=ROLE_ADMIN)
    req = FakeRequest(
        path=f"{settings.API_V1_STR}/quotation/tasks",
        auth_header=f"Bearer {token}",
    )
    identifier, is_authenticated, role = asyncio.run(limiter._get_client_identifier(req))
    assert identifier == "auth:user-123"
    assert is_authenticated is True
    assert role == ROLE_ADMIN


def test_get_client_identifier_role_none_for_old_token():
    limiter = _limiter()
    token = create_access_token(subject="user-123")  # 无 role claim
    req = FakeRequest(
        path=f"{settings.API_V1_STR}/quotation/tasks",
        auth_header=f"Bearer {token}",
    )
    identifier, is_authenticated, role = asyncio.run(limiter._get_client_identifier(req))
    assert identifier == "auth:user-123"
    assert is_authenticated is True
    assert role is None


def test_get_client_identifier_anon_without_token():
    limiter = _limiter()
    req = FakeRequest(path=f"{settings.API_V1_STR}/quotation/tasks")
    identifier, is_authenticated, role = asyncio.run(limiter._get_client_identifier(req))
    assert identifier.startswith("anon:")
    assert is_authenticated is False
    assert role is None
