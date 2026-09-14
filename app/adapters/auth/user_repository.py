"""User repository adapter encapsulating SQLAlchemy User ORM."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.exceptions import NotFoundError
from app.core.rbac_queries import load_user_permissions
from app.ports.outbound.auth import UserRepositoryPort
from app.ports.dto.auth import UserDTO


def _orm_user_to_dto(user, permissions: list[str] | None = None) -> UserDTO:
    """Map ORM User to UserDTO."""
    return UserDTO(
        id=str(user.id),
        username=str(user.username or ""),
        email=str(user.email or ""),
        name=str(user.name) if user.name else None,
        role=str(user.role.value if hasattr(user.role, "value") else user.role),
        is_active=bool(user.is_active),
        created_at=str(user.created_at) if user.created_at else "",
        phone=str(user.phone) if user.phone else None,
        department=str(user.department) if user.department else None,
        avatar=str(user.avatar) if user.avatar else None,
        permissions=permissions or [],
    )


class SqlAlchemyUserRepositoryAdapter(UserRepositoryPort):
    """SQLAlchemy-backed user repository (async session per method)."""

    async def get_by_username(self, username: str) -> Optional[object]:
        from app.models.orm.platform.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).filter(User.username == username))
            return result.scalars().first()

    async def get_by_id(self, user_id: str) -> Optional[UserDTO]:
        from app.models.orm.platform.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if not user:
                return None
            perms = await load_user_permissions(db, str(user.id))
            return _orm_user_to_dto(user, perms)

    async def create(
        self, username: str, email: str, password: str, name: Optional[str]
    ) -> UserDTO:
        from app.models.orm.platform.user import User, UserRole
        from app.models.orm.platform.role import Role
        from app.models.orm.platform.user_role import user_role_table

        async with AsyncSessionLocal() as db:
            user = User(
                username=username,
                email=email,
                password=password,
                name=name,
                role=UserRole.user,
                is_active=True,
            )
            db.add(user)
            await db.flush()

            roles_result = await db.execute(
                select(Role).filter(Role.name.in_(["page_closing_form"]))
            )
            existing_roles = {r.name: r.id for r in roles_result.scalars().all()}
            for rname in ("page_closing_form",):
                role_id = existing_roles.get(rname)
                if role_id:
                    from sqlalchemy.dialects.postgresql import insert as pg_insert
                    await db.execute(
                        pg_insert(user_role_table)
                        .values(user_id=user.id, role_id=role_id)
                        .on_conflict_do_nothing()
                    )

            await db.commit()
            await db.refresh(user)
            perms = await load_user_permissions(db, str(user.id))
            return _orm_user_to_dto(user, perms)

    async def list_users(self) -> list[UserDTO]:
        from app.models.orm.platform.user import User
        from app.models.orm.platform.user_role import user_role_table
        from app.models.orm.platform.role_permission import role_permission_table
        from app.models.orm.platform.permission import Permission
        from app.models.orm.platform.role import Role

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).order_by(User.created_at))
            users = result.scalars().all()
            if not users:
                return []

            user_ids = [u.id for u in users]
            perm_stmt = (
                select(user_role_table.c.user_id, Permission.name)
                .select_from(user_role_table)
                .join(Role, Role.id == user_role_table.c.role_id)
                .join(role_permission_table, role_permission_table.c.role_id == Role.id)
                .join(Permission, Permission.id == role_permission_table.c.permission_id)
                .where(user_role_table.c.user_id.in_(user_ids))
            )
            perm_result = await db.execute(perm_stmt)
            perms_by_user: dict[str, list[str]] = {}
            for user_id_val, perm_name in perm_result.all():
                uid_str = str(user_id_val)
                perms_by_user.setdefault(uid_str, []).append(perm_name)

            return [
                _orm_user_to_dto(u, perms_by_user.get(str(u.id), []))
                for u in users
            ]

    async def delete(self, user_id: str) -> None:
        from app.models.orm.platform.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if user:
                await db.delete(user)
                await db.commit()

    async def update_role(self, user_id: str, role: str) -> UserDTO:
        from app.models.orm.platform.user import User, UserRole

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if not user:
                raise NotFoundError(f"User not found: {user_id}")
            role_map = {r.value: r for r in UserRole}
            user.role = role_map.get(role, UserRole.user)
            await db.commit()
            await db.refresh(user)
            perms = await load_user_permissions(db, str(user.id))
            return _orm_user_to_dto(user, perms)


    async def update_password(
        self,
        user_id: str,
        hashed_password: str,
    ) -> None:
        from app.models.orm.platform.user import User

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()

            if not user:
                raise NotFoundError(f"User not found: {user_id}")

            # 更新密码（bcrypt 哈希值）
            user.password = hashed_password

            await db.commit()
            
    async def update_page_permissions(
        self, user_id: str, view_closing_form: bool
    ) -> UserDTO:
        from app.models.orm.platform.user import User
        from app.models.orm.platform.role import Role
        from app.models.orm.platform.user_role import user_role_table
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).filter(User.id == uuid.UUID(user_id))
            )
            user = result.scalars().first()
            if not user:
                raise NotFoundError(f"User not found: {user_id}")

            roles_result = await db.execute(
                select(Role).filter(Role.name.in_(["page_closing_form"]))
            )
            role_map = {r.name: r.id for r in roles_result.scalars().all()}

            uid = uuid.UUID(user_id)

            for rname in ("page_closing_form",):
                role_id = role_map.get(rname)
                if not role_id:
                    continue
                enabled = view_closing_form
                if enabled:
                    await db.execute(
                        pg_insert(user_role_table)
                        .values(user_id=uid, role_id=role_id)
                        .on_conflict_do_nothing()
                    )
                else:
                    await db.execute(
                        user_role_table.delete().where(
                            (user_role_table.c.user_id == uid)
                            & (user_role_table.c.role_id == role_id)
                        )
                    )

            await db.commit()
            await db.refresh(user)
            perms = await load_user_permissions(db, str(user.id))
            return _orm_user_to_dto(user, perms)
