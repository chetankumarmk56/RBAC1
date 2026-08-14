"""Employee, attendance, performance and leave tools."""

from datetime import date, timedelta

from sqlalchemy import func, select

from models import Attendance, Employee, LeaveRecord, Performance
from rbac.permissions import ATTENDANCE_READ, EMPLOYEE_READ, LEAVE_READ, PERFORMANCE_READ
from tools.base import Tool, ToolContext, ToolResult, scope_note, visible_employee_ids


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _employee_lookup(ctx: ToolContext, ids: list[int] | None) -> dict[int, Employee]:
    stmt = select(Employee)
    if ids is not None:
        stmt = stmt.where(Employee.id.in_(ids))
    return {employee.id: employee for employee in ctx.db.scalars(stmt)}


# --------------------------------------------------------------------------- #
# get_employee
# --------------------------------------------------------------------------- #

def get_employee(ctx: ToolContext, args: dict) -> ToolResult:
    ids = visible_employee_ids(ctx)
    stmt = select(Employee).order_by(Employee.department, Employee.full_name)

    if ids is not None:
        stmt = stmt.where(Employee.id.in_(ids))
    if name := (args.get("employee_name") or "").strip():
        stmt = stmt.where(Employee.full_name.ilike(f"%{name}%"))
    if department := (args.get("department") or "").strip():
        stmt = stmt.where(Employee.department.ilike(f"%{department}%"))
    if status := (args.get("status") or "").strip():
        stmt = stmt.where(Employee.status == status)

    employees = list(ctx.db.scalars(stmt))
    managers = {
        employee.id: employee.full_name
        for employee in ctx.db.scalars(
            select(Employee).where(Employee.id.in_({e.manager_id for e in employees if e.manager_id}))
        )
    }

    rows = [
        {
            "name": employee.full_name,
            "email": employee.email,
            "department": employee.department,
            "title": employee.title,
            "manager": managers.get(employee.manager_id),
            "hire_date": employee.hire_date.isoformat(),
            "status": employee.status,
        }
        for employee in employees
    ]
    return ToolResult(
        summary=f"{len(rows)} employee record(s).",
        data=rows,
        row_count=len(rows),
        scope_note=scope_note(ctx, ids),
    )


# --------------------------------------------------------------------------- #
# get_attendance
# --------------------------------------------------------------------------- #

def get_attendance(ctx: ToolContext, args: dict) -> ToolResult:
    ids = visible_employee_ids(ctx)
    days = _clamp(args.get("days"), 1, 365, 30)
    since = date.today() - timedelta(days=days)

    stmt = (
        select(
            Attendance.employee_id,
            Attendance.status,
            func.count().label("days"),
            func.coalesce(func.sum(Attendance.hours_worked), 0).label("hours"),
        )
        .where(Attendance.work_date >= since)
        .group_by(Attendance.employee_id, Attendance.status)
    )
    if ids is not None:
        stmt = stmt.where(Attendance.employee_id.in_(ids))

    employees = _employee_lookup(ctx, ids)
    name_filter = (args.get("employee_name") or "").strip().lower()

    per_employee: dict[int, dict] = {}
    for employee_id, status, day_count, hours in ctx.db.execute(stmt):
        employee = employees.get(employee_id)
        if employee is None:
            continue
        if name_filter and name_filter not in employee.full_name.lower():
            continue
        entry = per_employee.setdefault(
            employee_id,
            {
                "name": employee.full_name,
                "department": employee.department,
                "present": 0,
                "remote": 0,
                "late": 0,
                "absent": 0,
                "total_days": 0,
                "hours_worked": 0.0,
            },
        )
        entry[status] = entry.get(status, 0) + day_count
        entry["total_days"] += day_count
        entry["hours_worked"] = round(entry["hours_worked"] + float(hours), 2)

    rows = []
    for entry in per_employee.values():
        attended = entry["present"] + entry["remote"] + entry["late"]
        entry["attendance_rate_pct"] = (
            round(100 * attended / entry["total_days"], 1) if entry["total_days"] else 0.0
        )
        rows.append(entry)
    rows.sort(key=lambda row: row["name"])

    overall_total = sum(row["total_days"] for row in rows)
    overall_attended = sum(row["present"] + row["remote"] + row["late"] for row in rows)
    average_rate = round(100 * overall_attended / overall_total, 1) if overall_total else 0.0

    return ToolResult(
        summary=(
            f"Attendance for the last {days} days across {len(rows)} employee(s). "
            f"Average attendance rate {average_rate}%."
        ),
        data={"window_days": days, "since": since.isoformat(), "average_rate_pct": average_rate, "employees": rows},
        row_count=len(rows),
        scope_note=scope_note(ctx, ids),
    )


# --------------------------------------------------------------------------- #
# get_performance
# --------------------------------------------------------------------------- #

def get_performance(ctx: ToolContext, args: dict) -> ToolResult:
    ids = visible_employee_ids(ctx)
    stmt = select(Performance).order_by(Performance.review_period.desc(), Performance.rating.desc())
    if ids is not None:
        stmt = stmt.where(Performance.employee_id.in_(ids))
    if period := (args.get("review_period") or "").strip():
        stmt = stmt.where(Performance.review_period == period)

    reviews = list(ctx.db.scalars(stmt))
    employees = _employee_lookup(ctx, ids)
    name_filter = (args.get("employee_name") or "").strip().lower()

    rows = []
    for review in reviews:
        employee = employees.get(review.employee_id)
        if employee is None:
            continue
        if name_filter and name_filter not in employee.full_name.lower():
            continue
        rows.append(
            {
                "name": employee.full_name,
                "department": employee.department,
                "review_period": review.review_period,
                "rating": float(review.rating),
                "reviewer": review.reviewer,
                "comments": review.comments,
            }
        )

    average = round(sum(row["rating"] for row in rows) / len(rows), 2) if rows else None
    return ToolResult(
        summary=f"{len(rows)} performance review(s)." + (f" Average rating {average}/5." if average else ""),
        data={"average_rating": average, "reviews": rows},
        row_count=len(rows),
        scope_note=scope_note(ctx, ids),
    )


# --------------------------------------------------------------------------- #
# get_leave
# --------------------------------------------------------------------------- #

def get_leave(ctx: ToolContext, args: dict) -> ToolResult:
    ids = visible_employee_ids(ctx)
    today = date.today()

    stmt = select(LeaveRecord).order_by(LeaveRecord.start_date.desc())
    if ids is not None:
        stmt = stmt.where(LeaveRecord.employee_id.in_(ids))
    if status := (args.get("status") or "").strip():
        stmt = stmt.where(LeaveRecord.status == status)
    if leave_type := (args.get("leave_type") or "").strip():
        stmt = stmt.where(LeaveRecord.leave_type == leave_type)

    only_current = bool(args.get("only_current"))
    if only_current:
        stmt = stmt.where(LeaveRecord.start_date <= today, LeaveRecord.end_date >= today)

    records = list(ctx.db.scalars(stmt))
    employees = _employee_lookup(ctx, ids)

    rows = []
    for record in records:
        employee = employees.get(record.employee_id)
        if employee is None:
            continue
        rows.append(
            {
                "name": employee.full_name,
                "department": employee.department,
                "leave_type": record.leave_type,
                "start_date": record.start_date.isoformat(),
                "end_date": record.end_date.isoformat(),
                "days": (record.end_date - record.start_date).days + 1,
                "status": record.status,
                "reason": record.reason,
                "currently_on_leave": record.start_date <= today <= record.end_date,
            }
        )

    qualifier = "currently on leave" if only_current else "leave record(s)"
    return ToolResult(
        summary=f"{len(rows)} {qualifier} as of {today.isoformat()}.",
        data={"as_of": today.isoformat(), "records": rows},
        row_count=len(rows),
        scope_note=scope_note(ctx, ids),
    )


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #

EMPLOYEE_TOOL = Tool(
    name="get_employee",
    description=(
        "Look up employee directory records: name, email, department, job title, manager, "
        "hire date and employment status. Use for questions about who works here, team "
        "rosters, headcount by name, departments or reporting lines. Does NOT return salary "
        "or compensation data — use get_payroll for that."
    ),
    required_permission=EMPLOYEE_READ,
    domain="employee directory",
    input_schema={
        "type": "object",
        "properties": {
            "employee_name": {"type": "string", "description": "Filter by full or partial employee name."},
            "department": {"type": "string", "description": "Filter by department, e.g. Engineering."},
            "status": {
                "type": "string",
                "enum": ["active", "on_leave", "terminated"],
                "description": "Filter by employment status.",
            },
        },
    },
    handler=get_employee,
)

ATTENDANCE_TOOL = Tool(
    name="get_attendance",
    description=(
        "Attendance records summarised per employee over a lookback window: days present, "
        "remote, late and absent, hours worked, and the attendance rate. Use for questions "
        "about attendance, absence, punctuality, remote-work patterns, or average attendance."
    ),
    required_permission=ATTENDANCE_READ,
    domain="attendance",
    input_schema={
        "type": "object",
        "properties": {
            "employee_name": {"type": "string", "description": "Filter by full or partial employee name."},
            "days": {
                "type": "integer",
                "description": "Lookback window in days, 1-365. Defaults to 30.",
            },
        },
    },
    handler=get_attendance,
)

PERFORMANCE_TOOL = Tool(
    name="get_performance",
    description=(
        "Performance reviews: rating out of 5, review period, reviewer and comments, plus the "
        "average rating across matching reviews. Use for questions about performance, ratings, "
        "reviews, top or low performers."
    ),
    required_permission=PERFORMANCE_READ,
    domain="performance",
    input_schema={
        "type": "object",
        "properties": {
            "employee_name": {"type": "string", "description": "Filter by full or partial employee name."},
            "review_period": {"type": "string", "description": "Exact review period, e.g. 2026-H1."},
        },
    },
    handler=get_performance,
)

LEAVE_TOOL = Tool(
    name="get_leave",
    description=(
        "Leave and time-off records: leave type, date range, number of days, approval status and "
        "whether the employee is on leave right now. Use for questions about who is on leave, "
        "time off, holidays, sick leave, or pending leave requests. Set only_current to true for "
        "'who is on leave now' style questions."
    ),
    required_permission=LEAVE_READ,
    domain="leave",
    input_schema={
        "type": "object",
        "properties": {
            "only_current": {
                "type": "boolean",
                "description": "True to return only leave that covers today's date.",
            },
            "status": {
                "type": "string",
                "enum": ["approved", "pending", "rejected"],
                "description": "Filter by approval status.",
            },
            "leave_type": {
                "type": "string",
                "enum": ["annual", "sick", "parental", "unpaid"],
                "description": "Filter by leave type.",
            },
        },
    },
    handler=get_leave,
)
