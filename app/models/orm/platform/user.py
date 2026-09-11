import enum
import uuid
from app.core.time_utils import utcnow_naive

from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import relationship

from app.models.orm.platform.base import Base
from app.models.orm.platform.user_role import user_role_table


class UserRole(str, enum.Enum):
    admin = "admin"
    user = "user"
    superuser = "superuser"


class User(Base):
    __tablename__ = 'users'

    id = Column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(128), unique=True, index=True, nullable=False)
    password = Column(String(128), nullable=False)
    is_active = Column(Boolean, default=True)
    role = Column(SAEnum(UserRole, name="userrole"), nullable=False, default=UserRole.user)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    roles = relationship(
        "Role",
        secondary=user_role_table,
        back_populates="users"
    )
    name = Column(String(64), nullable=True)
    phone = Column(String(32), nullable=True)
    department = Column(String(64), nullable=True)
    avatar = Column(String(256), nullable=True)


class UserLoginHistory(Base):
    __tablename__ = 'user_login_history'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(PgUUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    login_time = Column(DateTime, default=utcnow_naive)
    ip = Column(String(64), nullable=True)
    device = Column(String(128), nullable=True)


class UserPreferences(Base):
    __tablename__ = 'user_preferences'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(PgUUID(as_uuid=True), ForeignKey('users.id'), nullable=False, unique=True)
    email_notify = Column(Boolean, default=True)
    in_app_notify = Column(Boolean, default=True)
    theme = Column(String(16), default='system')
    language = Column(String(16), default='zh-CN')


class UserSubscription(Base):
    __tablename__ = 'user_subscriptions'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(PgUUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    product_id = Column(Integer, nullable=False)
    subscribe_time = Column(DateTime, default=utcnow_naive)
