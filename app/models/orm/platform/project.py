import enum

from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.orm.platform.base import Base


class ProjectStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class TaskStatus(str, enum.Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ProjectSpace(Base):
    """项目空间表"""
    __tablename__ = "project_spaces"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, comment="项目空间名称")
    description = Column(Text, comment="项目空间描述")
    status = Column(Enum(ProjectStatus), default=ProjectStatus.ACTIVE, comment="项目状态")
    created_by = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="创建者ID")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")

    # 关系
    members = relationship("ProjectMember", back_populates="project")
    tasks = relationship("ProjectTask", back_populates="project")
    data_shares = relationship(
        "DataShare", 
        back_populates="project",
        foreign_keys=lambda: [DataShare.project_id]
    )


class ProjectMember(Base):
    """项目成员表"""
    __tablename__ = "project_members"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("project_spaces.id"), nullable=False, comment="项目空间ID")
    user_id = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="用户ID")
    role = Column(String(50), nullable=False, comment="成员角色")
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), comment="加入时间")
    is_active = Column(Boolean, default=True, comment="是否活跃")

    # 关系
    project = relationship("ProjectSpace", back_populates="members")
    user = relationship("User")


class ProjectTask(Base):
    """项目任务表"""
    __tablename__ = "project_tasks"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("project_spaces.id"), nullable=False, comment="项目空间ID")
    title = Column(String(200), nullable=False, comment="任务标题")
    description = Column(Text, comment="任务描述")
    assigned_to = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), comment="指派给的用户ID")
    created_by = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="创建者ID")
    status = Column(Enum(TaskStatus), default=TaskStatus.TODO, comment="任务状态")
    priority = Column(Enum(TaskPriority), default=TaskPriority.MEDIUM, comment="任务优先级")
    due_date = Column(DateTime(timezone=True), comment="截止日期")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")

    # 关系
    project = relationship("ProjectSpace", back_populates="tasks")
    assignee = relationship("User", foreign_keys=[assigned_to])
    creator = relationship("User", foreign_keys=[created_by])


class DataShare(Base):
    """跨项目数据共享表"""
    __tablename__ = "data_shares"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("project_spaces.id"), nullable=False, comment="申请方项目空间ID")
    target_project_id = Column(Integer, ForeignKey("project_spaces.id"), nullable=False, comment="目标项目空间ID")
    resource_type = Column(String(50), nullable=False, comment="资源类型")
    resource_id = Column(Integer, nullable=False, comment="资源ID")
    resource_name = Column(String(200), nullable=False, comment="资源名称")
    request_reason = Column(Text, comment="申请原因")
    status = Column(String(20), default="pending", comment="申请状态：pending/approved/rejected")
    requested_by = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, comment="申请人ID")
    reviewed_by = Column(PgUUID(as_uuid=True), ForeignKey("users.id"), comment="审核人ID")
    reviewed_at = Column(DateTime(timezone=True), comment="审核时间")
    review_comment = Column(Text, comment="审核意见")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="申请时间")

    # 关系
    project = relationship("ProjectSpace", back_populates="data_shares", foreign_keys=[project_id])
    target_project = relationship("ProjectSpace", foreign_keys=[target_project_id])
    requester = relationship("User", foreign_keys=[requested_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
