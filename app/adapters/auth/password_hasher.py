"""Password hashing adapter wrapping bcrypt."""

from __future__ import annotations

from app.core.security import hash_password as _hash_password_impl, verify_password as _verify_password_impl
from app.ports.outbound.auth import PasswordHasherPort


class BcryptPasswordHasherAdapter(PasswordHasherPort):
    """Bcrypt-based password hashing via core.security primitives."""

    def hash_password(self, password: str) -> str:
        return _hash_password_impl(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        return _verify_password_impl(plain_password, hashed_password)
