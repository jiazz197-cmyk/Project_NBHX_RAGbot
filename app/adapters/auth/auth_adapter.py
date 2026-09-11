"""JWT token issuer adapter wrapping core.security JWT primitives."""

from __future__ import annotations

from app.core.security import create_access_token
from app.ports.outbound.auth import TokenIssuerPort


class JwtTokenIssuerAdapter(TokenIssuerPort):
    """Thin adapter: delegates token creation to core.security primitives."""

    def create_tokens(
        self,
        user_id: str,
        role: str | None = None,
        password_version: str | None = None,
    ) -> dict:
        access_token = create_access_token(
            subject=user_id,
            role=role,
            password_version=password_version,
        )
        return {"access_token": access_token, "token_type": "bearer"}
