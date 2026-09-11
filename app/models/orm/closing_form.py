from app.core.time_utils import utcnow_naive

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class PendingForm(Base):
    """待审批表单暂存表，审批通过后移入向量库，此表记录随即删除。拒绝则保留在此表中并标记状态。"""

    __tablename__ = "data_pending"

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    uploader = Column(String(128), nullable=False, index=True)
    upload_time = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    created_at = Column(DateTime, default=utcnow_naive)
    image_url_1 = Column(String(512), nullable=True)
    image_url_2 = Column(String(512), nullable=True)
