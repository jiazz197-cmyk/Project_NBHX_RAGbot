from sqlalchemy import Table, Column, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PgUUID

from app.models.orm.platform.base import Base

user_role_table = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', PgUUID(as_uuid=True), ForeignKey('users.id'), primary_key=True),
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True)
)
