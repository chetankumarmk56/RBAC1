"""Initial schema: RBAC tables, HR domain tables, audit log.

Revision ID: 0001
Revises:
Create Date: 2026-08-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False, unique=True),
        sa.Column("description", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False, unique=True),
        sa.Column("description", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            sa.Integer(),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("department", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("manager_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("hire_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        # unique=True lives on the index below, matching `unique=True, index=True`
        # on the model column (SQLAlchemy renders that as a single unique index).
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "payroll",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("base_salary", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("bonus", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("deductions", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("net_pay", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
    )
    op.create_index("ix_payroll_employee_period", "payroll", ["employee_id", "period"])

    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("hours_worked", sa.Numeric(precision=4, scale=2), nullable=False, server_default="0"),
    )
    op.create_index("ix_attendance_employee_date", "attendance", ["employee_id", "work_date"])

    op.create_table(
        "performance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("review_period", sa.String(length=20), nullable=False),
        sa.Column("rating", sa.Numeric(precision=2, scale=1), nullable=False),
        sa.Column("reviewer", sa.String(length=120), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
    )
    op.create_index("ix_performance_employee", "performance", ["employee_id"])

    op.create_table(
        "leave_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "employee_id",
            sa.Integer(),
            sa.ForeignKey("employees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("leave_type", sa.String(length=30), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_leave_employee_dates", "leave_records", ["employee_id", "start_date", "end_date"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("agent", sa.String(length=50), nullable=True),
        sa.Column("tool", sa.String(length=60), nullable=True),
        sa.Column("required_permission", sa.String(length=50), nullable=True),
        sa.Column("decision", sa.String(length=10), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("request_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_leave_employee_dates", table_name="leave_records")
    op.drop_table("leave_records")
    op.drop_index("ix_performance_employee", table_name="performance")
    op.drop_table("performance")
    op.drop_index("ix_attendance_employee_date", table_name="attendance")
    op.drop_table("attendance")
    op.drop_index("ix_payroll_employee_period", table_name="payroll")
    op.drop_table("payroll")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    op.drop_table("employees")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
