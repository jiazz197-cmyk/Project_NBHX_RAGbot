"""Auth subsystem DTOs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UserDTO:
    """User data transfer object for API responses."""

    id: str
    username: str
    email: str
    name: Optional[str]
    role: str
    is_active: bool
    created_at: str
    phone: Optional[str] = None
    department: Optional[str] = None
    avatar: Optional[str] = None
    permissions: list[str] = field(default_factory=list)


@dataclass
class LoginCommand:
    """Command for user login."""

    username: str
    password: str


@dataclass
class RegisterCommand:
    """Command for user registration."""

    username: str
    email: str
    password: str
    name: Optional[str] = None


@dataclass
class TokenPair:
    """JWT token pair response."""

    access_token: str
    token_type: str = "bearer"


@dataclass
class UpdateUserRoleCommand:
    """Command to update a user's role."""

    target_user_id: str
    new_role: str
    current_user_id: str
    current_user_name: str


@dataclass
class UpdatePagePermissionsCommand:
    """Command to update a user's generic page-visibility permissions.

    ``page_permissions`` maps a page key (e.g. ``report``) to the desired
    visibility.  The RBAC convention is ``view_<key>`` permission +
    ``page_<key>`` role.  The framework is retained for later use and is
    disabled by default via ``PAGE_PERMISSION_MANAGEMENT_ENABLED``.
    """

    target_user_id: str
    page_permissions: dict[str, bool]
    current_user_id: str


@dataclass
class ResetUserPasswordCommand:
    """Command for resetting a user's password."""

    target_user_id: str
    new_password: str
    current_user_id: str
    current_user_name: str