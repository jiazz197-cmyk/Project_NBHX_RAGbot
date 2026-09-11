"""DTO: persisted file records for file-manager API (list/detail/download).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileRecordDTO:
    id: int
    file_name: str
    unique_name: str
    minio_object_path: str
    content_type: str
    file_size: int
    uploader: str
    created_at: datetime
    updated_at: datetime
