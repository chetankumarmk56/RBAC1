"""HR domain tables. Tools read from these — but only after an RBAC check passes."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    department: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    hire_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    manager: Mapped["Employee | None"] = relationship(remote_side=[id])


class Payroll(Base):
    __tablename__ = "payroll"
    __table_args__ = (Index("ix_payroll_employee_period", "employee_id", "period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # "2026-07"
    base_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bonus: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    deductions: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    net_pay: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (Index("ix_attendance_employee_date", "employee_id", "work_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # present | remote | late | absent
    hours_worked: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False, default=0)


class Performance(Base):
    __tablename__ = "performance"
    __table_args__ = (Index("ix_performance_employee", "employee_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    review_period: Mapped[str] = mapped_column(String(20), nullable=False)  # "2026-H1"
    rating: Mapped[Decimal] = mapped_column(Numeric(2, 1), nullable=False)  # 1.0 - 5.0
    reviewer: Mapped[str] = mapped_column(String(120), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text)


class LeaveRecord(Base):
    __tablename__ = "leave_records"
    __table_args__ = (
        Index("ix_leave_employee_dates", "employee_id", "start_date", "end_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    leave_type: Mapped[str] = mapped_column(String(30), nullable=False)  # annual | sick | parental | unpaid
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # approved | pending | rejected
    reason: Mapped[str | None] = mapped_column(Text)
