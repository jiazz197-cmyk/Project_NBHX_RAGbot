"""SSE 帧格式化（.dsh/ragchain-interfaces.md §5）。"""

from __future__ import annotations

import json
from typing import Any


def sse_frame(event: str, data: dict[str, Any]) -> str:
    """返回完整 SSE 帧，中文不转义。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
