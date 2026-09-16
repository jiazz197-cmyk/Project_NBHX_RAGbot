import uuid
from datetime import datetime
from typing import Dict, Optional, List

from pydantic import BaseModel, EmailStr, Field

from app.models.orm.platform.user import UserRole


# 精简版角色
class RoleSimple(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str


class UserCreate(BaseModel):
    username: str
    name: Optional[str] = None
    email: EmailStr
    phone: Optional[str] = None
    department: Optional[str] = None
    avatar: Optional[str] = None
    password: str
    role: UserRole = UserRole.user
    role_ids: Optional[List[int]] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    avatar: Optional[str] = None
    password: Optional[str] = None
    role_ids: Optional[List[int]] = None


class UserRoleUpdate(BaseModel):
    """Superuser-only: change a user's role. Cannot grant superuser."""
    role: UserRole


class UserRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    username: str
    name: Optional[str] = None
    email: EmailStr
    phone: Optional[str] = None
    department: Optional[str] = None
    avatar: Optional[str] = None
    is_active: Optional[bool] = True
    role: UserRole = UserRole.user
    roles: List[RoleSimple] = []
    permissions: List[str] = []


class UserPagePermissionsUpdate(BaseModel):
    """Generic page-permission update payload.

    ``page_permissions`` maps a page key to the desired visibility.  The
    framework is kept for later use and disabled by default through
    ``PAGE_PERMISSION_MANAGEMENT_ENABLED``; no page keys are defined now.
    """

    page_permissions: Dict[str, bool] = Field(default_factory=dict)


class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class UserRegister(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)
    name: Optional[str] = None


class UserPreferences(BaseModel):
    id: int
    user_id: uuid.UUID
    email_notify: bool = True
    in_app_notify: bool = True
    theme: str = 'system'
    language: str = 'zh-CN'


class UserLoginHistory(BaseModel):
    id: int
    user_id: uuid.UUID
    login_time: datetime
    ip: Optional[str] = None
    device: Optional[str] = None

    class Config:
        from_attributes = True


class UserSubscription(BaseModel):
    id: int
    user_id: uuid.UUID
    product_id: int
    subscribe_time: Optional[str] = None

class UserPasswordReset(BaseModel):
    """Reset another user's password."""

    password: str = Field(
        ...,
        min_length=6,
        max_length=128,
    )