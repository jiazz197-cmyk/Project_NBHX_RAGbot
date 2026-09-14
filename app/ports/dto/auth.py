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
class ResetUserPasswordCommand:
    """Command for resetting a user's password."""

    target_user_id: str
    new_password: str
    current_user_id: str
    current_user_name: str