"""Issue #6 回归：页面权限框架保留但默认关闭。

验收口径：
- 只下线已删除页面的两个权限 key，不再有业务代码引用；
- 通用页面权限框架（DTO / Port / UseCase / PATCH 路由 / 前端 service）保留；
- ``PAGE_PERMISSION_MANAGEMENT_ENABLED=False`` 时端点返回 404，且没有
  任何页面 key / RBAC 角色被自动启用。
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.adapters.web.platform.user import UserPagePermissionsUpdate
from app.core.config import settings
from app.core.exceptions import ValidationError
from app.domain.auth.page_permissions import (
    is_valid_page_key,
    normalize_page_permissions,
    page_permission_name,
    page_role_name,
)
from app.ports.dto.auth import UpdatePagePermissionsCommand, UserDTO

REPO_ROOT = Path(__file__).resolve().parent.parent

# Issue #6 E 节要求业务代码零命中的已删页面权限符号。
LEGACY_PAGE_PERMISSION_SYMBOLS = (
    "closing_form",
    "closing-form",
    "view_closing_form",
    "page_closing_form",
    "quotation_generation",
    "QuotationTask",
    "view_quotation",
    "page_quotation",
)

SCAN_PATHS = (
    REPO_ROOT / "app",
    REPO_ROOT / "frontend" / "apps" / "chat" / "src",
    REPO_ROOT / "main.py",
    REPO_ROOT / "nginx",
    REPO_ROOT / ".env.example",
)

REGRESSION_SCRIPTS = (
    REPO_ROOT / "tests" / "scripts" / "fix_full_regression.sh",
    REPO_ROOT / "tests" / "scripts" / "full_acceptance_regression.sh",
)
SCAN_SUFFIXES = {".py", ".ts", ".vue", ".js", ".sh", ".conf", ".template", ".example"}


def _iter_scanned_files():
    for base in SCAN_PATHS:
        if base.is_file():
            yield base
            continue
        for path in base.rglob("*"):
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            if path.is_file() and path.suffix in SCAN_SUFFIXES:
                yield path


@pytest.mark.parametrize("symbol", LEGACY_PAGE_PERMISSION_SYMBOLS, ids=str)
def test_legacy_page_symbols_absent_from_business_code(symbol):
    hits = []
    for path in _iter_scanned_files():
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if symbol in source:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert not hits, f"{symbol} 仍出现在业务代码: {hits}"



@pytest.mark.parametrize("symbol", LEGACY_PAGE_PERMISSION_SYMBOLS, ids=str)
def test_legacy_page_symbols_absent_from_regression_scripts(symbol):
    """Issue #6 B：回归脚本不得再依赖已删页面 / 报价链路。"""
    hits = [
        str(path.relative_to(REPO_ROOT))
        for path in REGRESSION_SCRIPTS
        if symbol in path.read_text(encoding="utf-8")
    ]
    assert not hits, f"{symbol} 仍出现在回归脚本: {hits}"


def test_page_permission_framework_disabled_by_default():
    assert settings.PAGE_PERMISSION_MANAGEMENT_ENABLED is False


def test_page_permission_key_helpers_follow_rbac_convention():
    assert is_valid_page_key("report")
    assert not is_valid_page_key("Report")
    assert not is_valid_page_key("")
    assert page_permission_name("report") == "view_report"
    assert page_role_name("report") == "page_report"
    assert normalize_page_permissions({"report": True, "dashboard": False}) == {
        "report": True,
        "dashboard": False,
    }
    with pytest.raises(ValueError):
        normalize_page_permissions({"Not Valid": True})


def test_generic_command_and_payload_are_page_key_agnostic():
    command = UpdatePagePermissionsCommand(
        target_user_id="user-1",
        page_permissions={"report": True},
        current_user_id="super-1",
    )
    assert command.page_permissions == {"report": True}

    payload = UserPagePermissionsUpdate(page_permissions={"report": False})
    assert payload.page_permissions == {"report": False}

    from app.adapters.auth.user_repository import SqlAlchemyUserRepositoryAdapter

    assert hasattr(SqlAlchemyUserRepositoryAdapter, "update_page_permissions")


@pytest.mark.asyncio
async def test_update_usecase_delegates_generic_page_permissions():
    from app.usecases.auth.users import UpdateUserPagePermissionsUseCase

    target = UserDTO(
        id="user-1",
        username="user",
        email="user@example.com",
        name=None,
        role="user",
        is_active=True,
        created_at="2026-01-01T00:00:00",
    )
    updated = UserDTO(
        id="user-1",
        username="user",
        email="user@example.com",
        name=None,
        role="user",
        is_active=True,
        created_at="2026-01-01T00:00:00",
        permissions=["view_report"],
    )
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=target)
    repo.update_page_permissions = AsyncMock(return_value=updated)

    result = await UpdateUserPagePermissionsUseCase(repo).execute(
        UpdatePagePermissionsCommand(
            target_user_id="user-1",
            page_permissions={"report": True},
            current_user_id="super-1",
        )
    )

    repo.update_page_permissions.assert_awaited_once_with(
        "user-1", {"report": True}
    )
    assert result.permissions == ["view_report"]


@pytest.mark.asyncio
async def test_update_usecase_rejects_invalid_page_key():
    from app.usecases.auth.users import UpdateUserPagePermissionsUseCase

    repo = MagicMock()
    repo.get_by_id = AsyncMock(
        return_value=UserDTO(
            id="user-1",
            username="user",
            email="user@example.com",
            name=None,
            role="user",
            is_active=True,
            created_at="2026-01-01T00:00:00",
        )
    )
    repo.update_page_permissions = AsyncMock()

    with pytest.raises(ValidationError):
        await UpdateUserPagePermissionsUseCase(repo).execute(
            UpdatePagePermissionsCommand(
                target_user_id="user-1",
                page_permissions={"Not Valid": True},
                current_user_id="super-1",
            )
        )
    repo.update_page_permissions.assert_not_awaited()


def test_page_permission_endpoint_exists_but_returns_404_when_disabled(monkeypatch):
    from app.api.v1 import auth as auth_api

    route_paths = {route.path for route in auth_api.router.routes}
    assert "/users/{user_id}/page-permissions" in route_paths

    monkeypatch.setattr(
        auth_api.settings, "PAGE_PERMISSION_MANAGEMENT_ENABLED", False
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            auth_api.update_user_page_permissions(
                uuid.uuid4(),
                UserPagePermissionsUpdate(page_permissions={"report": True}),
                current_user=SimpleNamespace(id="super-1", username="super"),
            )
        )
    assert exc_info.value.status_code == 404
