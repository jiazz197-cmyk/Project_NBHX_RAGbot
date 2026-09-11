from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.orm.platform.base import Base


class MigrationLog(Base):
    """数据库迁移日志表"""
    __tablename__ = "migration_logs"

    id = Column(Integer, primary_key=True, index=True)
    operation = Column(String(50), nullable=False, comment="操作类型：create/upgrade/downgrade/validate")
    revision_id = Column(String(100), comment="迁移版本ID")
    target_revision = Column(String(100), comment="目标版本")
    message = Column(Text, comment="操作描述")
    status = Column(String(20), default="success", comment="操作状态：success/failed/running")
    output = Column(Text, comment="操作输出")
    error = Column(Text, comment="错误信息")
    meta_info = Column(JSON, comment="额外元数据")
    executed_by = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="执行者ID")
    executed_at = Column(DateTime(timezone=True), server_default=func.now(), comment="执行时间")
    duration = Column(Integer, comment="执行时长（秒）")

    # 关系
    user = relationship("User")


class MigrationBackup(Base):
    """迁移备份表"""
    __tablename__ = "migration_backups"

    id = Column(Integer, primary_key=True, index=True)
    backup_id = Column(String(100), unique=True, nullable=False, comment="备份ID")
    revision = Column(String(100), nullable=False, comment="备份的版本")
    backup_path = Column(String(500), nullable=False, comment="备份文件路径")
    file_size = Column(Integer, comment="文件大小（字节）")
    description = Column(Text, comment="备份描述")
    created_by = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="创建者ID")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    is_active = Column(Boolean, default=True, comment="是否有效")

    # 关系
    user = relationship("User")
