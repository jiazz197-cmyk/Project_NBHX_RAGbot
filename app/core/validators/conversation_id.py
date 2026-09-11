"""Dify conversation_id validation (path-safe segment)."""

from __future__ import annotations

import re

_MAX_CONV_ID_LEN = 128
_CONV_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def validate_conversation_id(value: str) -> str:
    """Return stripped id or raise ValueError (safe single path segment for Dify URL)."""
    s = (value or "").strip()
    if not s or len(s) > _MAX_CONV_ID_LEN or ".." in s or not _CONV_ID_PATTERN.fullmatch(s):
        raise ValueError(
            "conversation_id must be 1-128 characters: letters, digits, . _ - only"
        )
    return s
