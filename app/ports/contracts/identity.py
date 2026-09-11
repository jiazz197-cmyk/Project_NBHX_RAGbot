"""Cross-cutting identity view for use cases and ports (no ORM)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

ROLE_SUPERUSER = "superuser"
ROLE_ADMIN = "admin"
ROLE_USER = "user"


class CurrentUserPort(Protocol):
    """Minimal current-user view for authorization and auditing in use cases."""

    id: str
    username: str
    name: str
    role: str
    permissions: list[str]

    def is_admin_like(self) -> bool: ...
    def is_superuser(self) -> bool: ...
    def has_permission(self, perm: str) -> bool: ...


@dataclass
class CurrentUserDTO:
    """Concrete DTO implementation of CurrentUserPort.

    Constructed by infrastructure layer (security.py) from ORM User model,
    passed across layers as the canonical current-user representation.
    """

    id: str = ""
    username: str = ""
    name: str = ""
    role: str = ROLE_USER
    permissions: list[str] = field(default_factory=list)

    def is_admin_like(self) -> bool:
        return self.role in (ROLE_ADMIN, ROLE_SUPERUSER)

    def is_superuser(self) -> bool:
        return self.role == ROLE_SUPERUSER

    def has_permission(self, perm: str) -> bool:
        return self.is_admin_like() or perm in self.permissions
