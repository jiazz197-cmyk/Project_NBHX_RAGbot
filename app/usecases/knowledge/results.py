"""Knowledge use-case result DTOs.

Plain dataclasses returned by use-cases so the use-case layer does not depend on
the presentation (FastAPI/pydantic) schemas. The API layer maps these to HTTP
response schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class KnowledgeRecordListResult:
    success: bool = True
    total: int = 0
    records: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class KnowledgeRecordDeleteResult:
    success: bool = True
    message: str = "删除成功"
    deleted_id: str = ""
