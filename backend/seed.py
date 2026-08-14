"""Seed the database with roles, permissions, test users and dummy HR data.

Run from the backend directory, after `alembic upgrade head`:
    python seed.py

Idempotent: it clears the demo data and re-inserts it, so it is safe to re-run.
Dates are generated relative to today, so "who is on leave right now" always has
something to find.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from auth.security import hash_password
from config import settings
from db.session import SessionLocal
from models import (
    Attendance,
    AuditLog,
    ChatMessage,
    Conversation,
    Employee,
    LeaveRecord,
    Payroll,
    Performance,
    Permission,
    Role,
    User,
    role_permissions,
)
from rbac.permissions import (
    ALL_PERMISSIONS,
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_DESCRIPTIONS,
    ROLE_HR,
    ROLE_PERMISSIONS,
    ROLE_SUPER_ADMIN,
    ROLE_SUPERVISOR,
)

RNG = random.Random(20260814)  # fixed seed → reproducible demo data
TODAY = date.today()

# (full_name, email, department, title, manager_key, base_salary)
# manager_key refers to another row's email; None means no manager.
EMPLOYEES: list[tuple[str, str, str, str, str | None, int]] = [
    ("Priya Sharma", "priya.sharma@example.com", "Engineering", "Engineering Manager", None, 165000),
    ("Daniel Okafor", "daniel.okafor@example.com", "Engineering", "Senior Backend Engineer", "priya.sharma@example.com", 142000),
    ("Mei Tanaka", "mei.tanaka@example.com", "Engineering", "Frontend Engineer", "priya.sharma@example.com", 121000),
    ("Luis Fernandez", "luis.fernandez@example.com", "Engineering", "DevOps Engineer", "priya.sharma@example.com", 128000),
    ("Aisha Bello", "aisha.bello@example.com", "Engineering", "QA Engineer", "priya.sharma@example.com", 98000),
    ("Tom Whitfield", "tom.whitfield@example.com", "Sales", "Sales Director", None, 158000),
    ("Nina Kovalenko", "nina.kovalenko@example.com", "Sales", "Account Executive", "tom.whitfield@example.com", 104000),
    ("Omar Haddad", "omar.haddad@example.com", "Sales", "Account Executive", "tom.whitfield@example.com", 99000),
    ("Grace Lin", "grace.lin@example.com", "People", "HR Business Partner", None, 112000),
    ("Ravi Menon", "ravi.menon@example.com", "People", "Recruiter", "grace.lin@example.com", 87000),
    ("Sofia Rossi", "sofia.rossi@example.com", "Data", "Data Analyst", None, 118000),
    ("Ben Carter", "ben.carter@example.com", "Data", "Data Engineer", "sofia.rossi@example.com", 133000),
]

# (email, full_name, role, linked employee email)
USERS: list[tuple[str, str, str, str | None]] = [
    ("supervisor@example.com", "Priya Sharma", ROLE_SUPERVISOR, "priya.sharma@example.com"),
    ("analyst@example.com", "Sofia Rossi", ROLE_ANALYST, "sofia.rossi@example.com"),
    ("hr@example.com", "Grace Lin", ROLE_HR, "grace.lin@example.com"),
    ("admin@example.com", "Alex Mercer", ROLE_ADMIN, None),
    ("superadmin@example.com", "Dana Reyes", ROLE_SUPER_ADMIN, None),
]

REVIEW_PERIODS = ["2025-H2", "2026-H1"]
REVIEW_COMMENTS = [
    "Consistently delivers ahead of schedule.",
    "Strong collaborator; could take more ownership of design decisions.",
    "Excellent technical depth, clear written communication.",
    "Meets expectations. Growth area: stakeholder communication.",
    "Outstanding quarter — led two cross-team initiatives.",
    "Solid contributor; onboarding new tooling well.",
]


def _month_key(offset: int) -> str:
    """'YYYY-MM' for `offset` months before the current month."""
    year, month = TODAY.year, TODAY.month - offset
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}-{month:02d}"


def reset(db: Session) -> None:
    """Delete demo data, children first."""
    for model in (ChatMessage, Conversation, AuditLog, Payroll, Attendance, Performance, LeaveRecord):
        db.execute(delete(model))
    db.execute(delete(User))
    db.execute(delete(Employee))
    db.execute(delete(role_permissions))
    db.execute(delete(Role))
    db.execute(delete(Permission))
    db.commit()


def seed_rbac(db: Session) -> dict[str, Role]:
    permissions = {
        name: Permission(name=name, description=description)
        for name, description in ALL_PERMISSIONS.items()
    }
    db.add_all(permissions.values())

    roles: dict[str, Role] = {}
    for role_name, permission_names in ROLE_PERMISSIONS.items():
        role = Role(name=role_name, description=ROLE_DESCRIPTIONS[role_name])
        role.permissions = [permissions[name] for name in permission_names]
        roles[role_name] = role
    db.add_all(roles.values())

    db.commit()
    return roles


def seed_employees(db: Session) -> dict[str, Employee]:
    employees: dict[str, Employee] = {}

    for full_name, email, department, title, _manager, _salary in EMPLOYEES:
        employee = Employee(
            full_name=full_name,
            email=email,
            department=department,
            title=title,
            hire_date=TODAY - timedelta(days=RNG.randint(200, 2200)),
            status="active",
        )
        employees[email] = employee
        db.add(employee)
    db.flush()  # assign ids before wiring managers

    for _name, email, _dept, _title, manager_email, _salary in EMPLOYEES:
        if manager_email:
            employees[email].manager_id = employees[manager_email].id

    db.commit()
    return employees


def seed_users(db: Session, roles: dict[str, Role], employees: dict[str, Employee]) -> None:
    hashed = hash_password(settings.seed_password)
    for email, full_name, role_name, employee_email in USERS:
        db.add(
            User(
                email=email,
                full_name=full_name,
                hashed_password=hashed,
                role_id=roles[role_name].id,
                employee_id=employees[employee_email].id if employee_email else None,
                is_active=True,
            )
        )
    db.commit()


def seed_payroll(db: Session, employees: dict[str, Employee]) -> None:
    periods = [_month_key(offset) for offset in (2, 1, 0)]
    salaries = {email: salary for _n, email, _d, _t, _m, salary in EMPLOYEES}

    for email, employee in employees.items():
        annual = Decimal(salaries[email])
        base = (annual / 12).quantize(Decimal("0.01"))
        for period in periods:
            bonus = Decimal(RNG.choice([0, 0, 0, 500, 1500, 2500]))
            deductions = (base * Decimal("0.24")).quantize(Decimal("0.01"))
            db.add(
                Payroll(
                    employee_id=employee.id,
                    period=period,
                    base_salary=base,
                    bonus=bonus,
                    deductions=deductions,
                    net_pay=base + bonus - deductions,
                    currency="USD",
                )
            )
    db.commit()


def seed_attendance(db: Session, employees: dict[str, Employee]) -> None:
    """60 calendar days of weekday attendance per employee."""
    statuses = ["present"] * 14 + ["remote"] * 4 + ["late"] * 1 + ["absent"] * 1

    for employee in employees.values():
        for offset in range(60):
            day = TODAY - timedelta(days=offset)
            if day.weekday() >= 5:  # skip weekends
                continue
            status = RNG.choice(statuses)
            hours = {
                "present": Decimal(RNG.choice(["7.50", "8.00", "8.50", "9.00"])),
                "remote": Decimal(RNG.choice(["7.00", "8.00", "8.25"])),
                "late": Decimal(RNG.choice(["6.50", "7.00"])),
                "absent": Decimal("0.00"),
            }[status]
            db.add(
                Attendance(
                    employee_id=employee.id,
                    work_date=day,
                    status=status,
                    hours_worked=hours,
                )
            )
    db.commit()


def seed_performance(db: Session, employees: dict[str, Employee]) -> None:
    managers = {email: manager for _n, email, _d, _t, manager, _s in EMPLOYEES}
    names = {email: name for name, email, _d, _t, _m, _s in EMPLOYEES}

    for email, employee in employees.items():
        manager_email = managers[email]
        reviewer = names.get(manager_email, "Alex Mercer (Admin)") if manager_email else "Alex Mercer (Admin)"
        for period in REVIEW_PERIODS:
            db.add(
                Performance(
                    employee_id=employee.id,
                    review_period=period,
                    rating=Decimal(RNG.choice(["2.5", "3.0", "3.5", "3.5", "4.0", "4.0", "4.5", "5.0"])),
                    reviewer=reviewer,
                    comments=RNG.choice(REVIEW_COMMENTS),
                )
            )
    db.commit()


def seed_leave(db: Session, employees: dict[str, Employee]) -> None:
    """A mix of past, current and upcoming leave.

    The first three entries deliberately span today so "who is currently on leave"
    returns results whenever the demo is run.
    """
    plan: list[tuple[str, str, int, int, str, str]] = [
        # (employee email, leave_type, days before today, days after today, status, reason)
        ("mei.tanaka@example.com", "annual", 2, 3, "approved", "Family holiday"),
        ("omar.haddad@example.com", "sick", 1, 1, "approved", "Flu"),
        ("ravi.menon@example.com", "parental", 10, 25, "approved", "Parental leave"),
        ("daniel.okafor@example.com", "annual", 40, -34, "approved", "Trip abroad"),
        ("nina.kovalenko@example.com", "annual", -14, 20, "pending", "Wedding"),
        ("aisha.bello@example.com", "unpaid", -30, 35, "pending", "Sabbatical request"),
        ("ben.carter@example.com", "sick", 21, -19, "approved", "Minor surgery recovery"),
        ("luis.fernandez@example.com", "annual", -7, 11, "rejected", "Clashes with release week"),
    ]

    for email, leave_type, before, after, status, reason in plan:
        db.add(
            LeaveRecord(
                employee_id=employees[email].id,
                leave_type=leave_type,
                start_date=TODAY - timedelta(days=before),
                end_date=TODAY + timedelta(days=after),
                status=status,
                reason=reason,
            )
        )
    db.commit()


def main() -> None:
    with SessionLocal() as db:
        print("Clearing existing demo data...")
        reset(db)

        print("Seeding roles and permissions...")
        roles = seed_rbac(db)

        print("Seeding employees...")
        employees = seed_employees(db)

        print("Seeding users...")
        seed_users(db, roles, employees)

        print("Seeding payroll, attendance, performance and leave...")
        seed_payroll(db, employees)
        seed_attendance(db, employees)
        seed_performance(db, employees)
        seed_leave(db, employees)

        print("\nRow counts:")
        for model in (Role, Permission, Employee, User, Payroll, Attendance, Performance, LeaveRecord):
            total = db.scalar(select(func.count()).select_from(model))
            print(f"  {model.__tablename__:<15} {total}")

        print(f"\nDone. Test users (password: {settings.seed_password}):")
        for email, full_name, role_name, _employee in USERS:
            print(f"  {email:<24} {role_name:<11} {full_name}")


if __name__ == "__main__":
    main()
