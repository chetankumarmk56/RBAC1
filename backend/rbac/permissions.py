"""The permission vocabulary, and which role holds which permission.

This module is the single source of truth used by `seed.py` to populate the
`permissions` and `role_permissions` tables. At request time permissions are
read from the database, never from this file — see `rbac/service.py`.
"""

PAYROLL_READ = "payroll:read"
EMPLOYEE_READ = "employee:read"
ATTENDANCE_READ = "attendance:read"
PERFORMANCE_READ = "performance:read"
LEAVE_READ = "leave:read"
ANALYTICS_READ = "analytics:read"
REPORTS_READ = "reports:read"
AUDIT_READ = "audit:read"
PERMISSIONS_MANAGE = "permissions:manage"
PERMISSIONS_WRITE = "permissions:write"

ALL_PERMISSIONS: dict[str, str] = {
    PAYROLL_READ: "Read salary, bonus and net pay records",
    EMPLOYEE_READ: "Read employee directory records",
    ATTENDANCE_READ: "Read attendance records",
    PERFORMANCE_READ: "Read performance reviews and ratings",
    LEAVE_READ: "Read leave requests and balances",
    ANALYTICS_READ: "Read aggregated, non-identifying statistics",
    REPORTS_READ: "Read summary reports",
    AUDIT_READ: "Read the tool-access audit trail",
    PERMISSIONS_MANAGE: "Read the roles and permissions configuration",
    PERMISSIONS_WRITE: "Change which roles may use which tools",
}

ROLE_SUPERVISOR = "supervisor"
ROLE_ANALYST = "analyst"
ROLE_HR = "hr"
ROLE_ADMIN = "admin"
ROLE_SUPER_ADMIN = "super_admin"

ROLE_DESCRIPTIONS: dict[str, str] = {
    ROLE_SUPERVISOR: "Team lead. Sees payroll, attendance and performance for their own reports.",
    ROLE_ANALYST: "Analytics. Sees aggregate statistics and the employee directory, never compensation.",
    # Not "never compensation": HR holds no payroll permission by default, but its
    # column baseline grants base pay, so a super admin who grants payroll at runtime
    # gets base pay without the bonus and deduction breakdown. This string is what
    # get_role_permissions reports, so it has to describe the real configuration.
    ROLE_HR: "HR operations. Sees employee records, attendance and leave. Holds no payroll "
    "access by default; if granted it, sees base pay but not the bonus and deduction breakdown.",
    ROLE_ADMIN: "System administrator. Full access to data and audit logs; can read the RBAC "
    "configuration but not change it.",
    ROLE_SUPER_ADMIN: "Super administrator. Everything an admin can do, plus granting and revoking "
    "tool access for other roles at runtime.",
}

# Roles whose permissions cannot be changed at runtime. Without this, a super admin
# could revoke their own `permissions:write` and lock everyone out of RBAC management
# with no way back in short of re-seeding.
PROTECTED_ROLES: frozenset[str] = frozenset({ROLE_SUPER_ADMIN})

ROLE_PERMISSIONS: dict[str, list[str]] = {
    ROLE_SUPERVISOR: [
        PAYROLL_READ,
        EMPLOYEE_READ,
        ATTENDANCE_READ,
        PERFORMANCE_READ,
        REPORTS_READ,
    ],
    ROLE_ANALYST: [
        EMPLOYEE_READ,
        ATTENDANCE_READ,
        PERFORMANCE_READ,
        ANALYTICS_READ,
        REPORTS_READ,
    ],
    ROLE_HR: [
        EMPLOYEE_READ,
        ATTENDANCE_READ,
        LEAVE_READ,
        REPORTS_READ,
    ],
    # Admin gets everything except the ability to change the RBAC configuration —
    # that separation is what makes super_admin a distinct role rather than a label.
    ROLE_ADMIN: [name for name in ALL_PERMISSIONS if name != PERMISSIONS_WRITE],
    ROLE_SUPER_ADMIN: list(ALL_PERMISSIONS),
}
