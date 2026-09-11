"""HTTP header helpers shared across API routes."""

from __future__ import annotations

from urllib.parse import quote


def build_content_disposition(filename: str, *, inline: bool = False) -> str:
    """Build a sanitized Content-Disposition header value.

    Strips CR/LF and double quotes from the filename to prevent header
    injection, provides an ASCII fallback, and includes the RFC 5987 UTF-8
    encoded form for non-ASCII filenames.
    """
    disposition = "inline" if inline else "attachment"
    cleaned = str(filename or "download.bin").replace("\r", "").replace("\n", "").replace('"', "")
    ascii_fallback = cleaned.encode("ascii", errors="ignore").decode("ascii").strip() or "download.bin"
    utf8_encoded = quote(cleaned)
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_encoded}'
