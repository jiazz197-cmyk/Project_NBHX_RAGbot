"""Auth subsystem ports: user repository, authentication, password hashing."""

from __future__ import annotations

from typing import Optional, Protocol


class UserRepositoryPort(Protocol):
    """Persistence boundary for user CRUD operations."""

    async def get_by_username(self, username: str) -> Optional[object]:
        ...

    async def get_by_id(self, user_id: str) -> Optional[object]:
        ...

    async def create(
        self, username: str, email: str, password: str, name: Optional[str]
    ) -> object:
        ...

    async def list_users(self) -> list[object]:
        ...

    async def delete(self, user_id: str) -> None:
        ...

    async def update_role(self, user_id: str, role: str) -> object:
        ...

    async def update_page_permissions(
        self, user_id: str, view_quotation: bool
    ) -> object:
        ...
        
    async def update_password(
        self,
        user_id: str,
        hashed_password: str,
    ) -> None:
        """Update the user's password hash."""
        ...

class PasswordHasherPort(Protocol):
    """Abstraction for password hashing and verification."""

    def hash_password(self, password: str) -> str:
        ...

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        ...


class TokenIssuerPort(Protocol):
    """JWT token issuance boundary."""

    def create_tokens(
        self,
        user_id: str,
        role: str | None = None,
        password_version: str | None = None,
    ) -> dict:
        ...
