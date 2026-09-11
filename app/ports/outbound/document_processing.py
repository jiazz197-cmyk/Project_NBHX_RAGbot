"""Document batch processing outbound ports."""

from __future__ import annotations

from typing import Any, List, Protocol


class DocumentRegistrationPort(Protocol):
    async def register_uploaded_files(self, files: Any, normalized_uploader: str) -> List[int]:
        ...


class DocumentProcessWorkerPort(Protocol):
    def submit_process_documents(
        self,
        task_id: str,
        file_ids: List[int],
        instance_id: int,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        ...
