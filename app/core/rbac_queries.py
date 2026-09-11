"""Shared RBAC permission-loading queries used by security and repository layers."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def load_user_permissions(db: AsyncSession, user_id) -> list[str]:
    """Return list of permission name strings for a user via user_roles → roles → role_permissions → permissions."""
    from app.models.orm.platform.user_role import user_role_table
    from app.models.orm.platform.role_permission import role_permission_table
    from app.models.orm.platform.permission import Permission
    from app.models.orm.platform.role import Role

    uid = uuid.UUID(str(user_id)) if isinstance(user_id, str) else user_id
    stmt = (
        select(Permission.name)
        .select_from(user_role_table)
        .join(Role, Role.id == user_role_table.c.role_id)
        .join(role_permission_table, role_permission_table.c.role_id == Role.id)
        .join(Permission, Permission.id == role_permission_table.c.permission_id)
        .where(user_role_table.c.user_id == uid)
    )
    result = await db.execute(stmt)
    return [row[0] for row in result.all()]
