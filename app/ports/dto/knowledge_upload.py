"""Knowledge upload DTOs and commands (cross-layer, dataclasses only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from app.ports.contracts.identity import CurrentUserPort


@dataclass
class KnowledgeUploadCommand:
    """文档知识上传命令（对应 POST /knowledge/documents）。"""

    files: List[Any]
    on_conflict: Optional[str] = None  # None=有同名报 409；replace|append
    current_user: Optional[CurrentUserPort] = None


@dataclass
class ExcelDbUploadCommand:
    """Excel 类数据库上传命令（对应 POST /knowledge/excel-db）。"""

    files: List[Any]
    on_conflict: Optional[str] = None  # None=有同名报 409；replace|append
    current_user: Optional[CurrentUserPort] = None


@dataclass
class KnowledgeFileConflict:
    """同名文件冲突摘要（409 响应体）。"""

    file_name: str
    uploader: str
    upload_time: str
    chunk_count: int


@dataclass
class KnowledgeUploadResult:
    """上传任务提交结果。"""

    task_id: str
    status: str
    message: str
    files_count: int
    collection: str
