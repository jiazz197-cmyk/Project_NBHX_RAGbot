"""DTO: TaskManager-backed task read model (no ORM)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TaskManagerTaskSnapshot:
    task_id: str
    task_type: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
