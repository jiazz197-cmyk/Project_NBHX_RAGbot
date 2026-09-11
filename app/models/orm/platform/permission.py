from app.core.time_utils import utcnow_naive

from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship

from app.models.orm.platform.base import Base
from app.models.orm.platform.role_permission import role_permission_table


class Permission(Base):
    __tablename__ = 'permissions'

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), unique=True, index=True, nullable=False)
    description = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    roles = relationship(
        "Role",
        secondary=role_permission_table,
        back_populates="permissions"
    )
