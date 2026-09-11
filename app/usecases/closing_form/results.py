"""Closing form use-case result DTOs.

Plain dataclasses returned by use-cases so the use-case layer does not depend on
the presentation (FastAPI/pydantic) schemas. The API layer maps these to HTTP
response schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ClosingFormSubmitResult:
    success: bool = True
    message: str = "提交成功，等待审批"
    form_text: Optional[str] = None
    image_url_1: Optional[str] = None
    image_url_2: Optional[str] = None


@dataclass
class ClosingFormListResult:
    success: bool = True
    total: int = 0
    records: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ClosingFormApproveResult:
    success: bool = True
    message: str = "审批通过"


@dataclass
class ClosingFormRejectResult:
    success: bool = True
    message: str = "审批不通过，已退回待修改"


@dataclass
class ClosingFormReviseResult:
    success: bool = True
    message: str = "修改已提交，等待审批"


@dataclass
class Collection2ListResult:
    success: bool = True
    total: int = 0
    records: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ClosingFormDeleteResult:
    success: bool = True
    message: str = "删除成功"
    deleted_id: str = ""
