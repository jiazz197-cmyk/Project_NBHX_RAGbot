"""通用「页面权限」框架的命名与校验工具。

Issue #6 口径：只下线营业订单 / 报价生成两个页面的权限管理，通用框架保留但默认
关闭（``PAGE_PERMISSION_MANAGEMENT_ENABLED=False``），待后续新增业务页面时启用。
页面 key 与 RBAC 的对应约定：

- 页面 key：``example_page``
- 权限名：``view_example_page``（Permission.name）
- 角色名：``page_example_page``（Role.name）

本模块只做纯函数校验，不依赖 ORM / 配置 / IO。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

PAGE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class InvalidPagePermissionKey(ValueError):
    """页面权限 key 不符合 ``^[a-z][a-z0-9_]{0,47}$``。"""


def is_valid_page_key(page_key: object) -> bool:
    """Return True when *page_key* can be safely used in RBAC names."""
    return isinstance(page_key, str) and PAGE_KEY_PATTERN.fullmatch(page_key) is not None


def page_permission_name(page_key: str) -> str:
    """Map a page key to the RBAC permission name (``view_<key>``)."""
    return f"view_{page_key}"


def page_role_name(page_key: str) -> str:
    """Map a page key to the RBAC role name (``page_<key>``)."""
    return f"page_{page_key}"


def normalize_page_permissions(
    page_permissions: Mapping[str, bool] | None,
) -> dict[str, bool]:
    """Validate and copy a ``{page_key: enabled}`` mapping.

    Unknown page keys are intentionally *not* rejected here: whether a page key
    is configured is an RBAC seed concern.  The repository simply ignores keys
    for which no ``page_<key>`` role exists.
    """
    if not page_permissions:
        return {}

    normalized: dict[str, bool] = {}
    for page_key, enabled in page_permissions.items():
        if not is_valid_page_key(page_key):
            raise InvalidPagePermissionKey(f"页面权限 key 非法: {page_key!r}")
        normalized[page_key] = bool(enabled)
    return normalized
