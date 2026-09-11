from app.core.time_utils import utcnow_naive

from sqlalchemy import Column, Integer, String, DateTime, Text

from app.models.orm.platform.base import Base


class KnowledgeInstance(Base):
    __tablename__ = "knowledge_instance"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, comment="知识库实例名称")
    description = Column(Text, default="", comment="知识库描述")
    type = Column(String(32), nullable=False, default="default", comment="知识库类型")  # 新增
    created_at = Column(DateTime, default=utcnow_naive)
    updated_at = Column(DateTime, default=utcnow_naive, onupdate=utcnow_naive)
    # 可扩展：向量模型、访问权限等
