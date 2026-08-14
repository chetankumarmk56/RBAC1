"""Importing this package registers every table on Base.metadata."""

from models.audit import AuditLog
from models.chat import ChatMessage, Conversation
from models.hr import Attendance, Employee, LeaveRecord, Payroll, Performance
from models.rbac import (
    Permission,
    Role,
    User,
    role_data_scope,
    role_field_access,
    role_models,
    role_permissions,
)

__all__ = [
    "Attendance",
    "AuditLog",
    "ChatMessage",
    "Conversation",
    "Employee",
    "LeaveRecord",
    "Payroll",
    "Performance",
    "Permission",
    "Role",
    "User",
    "role_data_scope",
    "role_field_access",
    "role_models",
    "role_permissions",
]
