"""Persistent quotation generation task model."""

from app.core.time_utils import utcnow_naive
from app.domain.quotation.entities import QuotationTaskStatus

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text

from app.core.database import Base


class QuotationTask(Base):
    """Stores quotation task state, ownership and result payload."""

    __tablename__ = "quotation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), unique=True, nullable=False, index=True)
    owner_id = Column(String(64), nullable=False, index=True)
    owner_username = Column(String(64), nullable=False, index=True)
    owner_ip = Column(String(64), nullable=True, index=True)
    role_snapshot = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, index=True, default=QuotationTaskStatus.queued.value)
    progress = Column(Integer, nullable=False, default=0)
    message = Column(String(512), nullable=False, default="任务已排队")

    uploaded_file_id = Column(Integer, ForeignKey("file_resource.id"), nullable=True, index=True)
    uploaded_file_name = Column(String(256), nullable=False)
    display_name = Column(String(256), nullable=False)
    uploaded_file_minio_path = Column(String(512), nullable=False)
    uploaded_file_content_type = Column(String(128), nullable=False, default="application/pdf")
    uploaded_file_size = Column(Integer, nullable=False, default=0)

    # Intermediate artifacts that should be deleted once task exits.
    temp_image_minio_path = Column(String(512), nullable=True)

    result_payload = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utcnow_naive, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive, nullable=False)
    awaiting_approval_at = Column(DateTime, nullable=True)

