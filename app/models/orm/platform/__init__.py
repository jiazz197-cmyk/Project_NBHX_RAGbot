"""平台模型统一导出。

注意：ProjectSpace / ProjectMember / ProjectTask / DataShare / PlatformAuditLog /
MigrationLog / MigrationBackup / UserLoginHistory / UserPreferences / UserSubscription
及其枚举（ProjectStatus / TaskStatus / TaskPriority）为"项目协作平台"未实现脚手架，
有意暂缓；init_db_tables 仍为其建表。勿重复标记为死代码。
"""
from app.models.orm.platform.base import Base
from app.models.orm.platform.user import User, UserLoginHistory, UserPreferences, UserSubscription
from app.models.orm.platform.role import Role
from app.models.orm.platform.permission import Permission
from app.models.orm.platform.user_role import user_role_table
from app.models.orm.platform.role_permission import role_permission_table
from app.models.orm.platform.project import (
    ProjectSpace, ProjectMember, ProjectTask, DataShare,
    ProjectStatus, TaskStatus, TaskPriority
)
from app.models.orm.platform.audit_log import PlatformAuditLog
from app.models.orm.platform.migration_log import MigrationLog, MigrationBackup

__all__ = [
    # Base
    "Base",
    # 用户相关
    "User", "UserLoginHistory", "UserPreferences", "UserSubscription",
    # 角色权限
    "Role", "Permission", "user_role_table", "role_permission_table",
    # 项目空间
    "ProjectSpace", "ProjectMember", "ProjectTask", "DataShare",
    "ProjectStatus", "TaskStatus", "TaskPriority",
    # 日志
    "PlatformAuditLog", "MigrationLog", "MigrationBackup",
]
