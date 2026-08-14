"""Aggregate analytics and summary reports.

Neither tool returns compensation figures — analytics is deliberately a
non-payroll view, so `analytics:read` can never be used as a back door to salary.

Each block of statistics is also tied to the column it aggregates, so a role that may
not see a column does not get its average either: withholding
`performance.rating` withholds the rating statistics too. Without that, an aggregate
would be a back door around the field policy.
"""

from datetime import date, timedelta

from sqlalchemy import func, select

from models import Attendance, Employee, LeaveRecord, Performance
from rbac.permissions import ANALYTICS_READ, REPORTS_READ
from tools.base import Tool, ToolContext, ToolResult, scope_note, shows, visible_employee_ids

# (metric name, payload key, dataset, the column the block aggregates)
BLOCKS: list[tuple[str, str, str, str]] = [
    ("headcount", "headcount_by_department", "employees", "department"),
    ("attendance", "attendance", "attendance", "attendance_rate_pct"),
    ("performance", "performance", "performance", "rating"),
    ("leave", "leave", "leave", "status"),
]


def _headcount_by_department(ctx: ToolContext, ids: list[int] | None) -> list[dict]:
    stmt = select(Employee.department, func.count()).group_by(Employee.department).order_by(Employee.department)
    if ids is not None:
        stmt = stmt.where(Employee.id.in_(ids))
    return [{"department": department, "headcount": count} for department, count in ctx.db.execute(stmt)]


def _attendance_stats(ctx: ToolContext, ids: list[int] | None, days: int) -> dict:
    since = date.today() - timedelta(days=days)
    stmt = (
        select(Attendance.status, func.count())
        .where(Attendance.work_date >= since)
        .group_by(Attendance.status)
    )
    if ids is not None:
        stmt = stmt.where(Attendance.employee_id.in_(ids))

    counts = {status: count for status, count in ctx.db.execute(stmt)}
    total = sum(counts.values())
    attended = counts.get("present", 0) + counts.get("remote", 0) + counts.get("late", 0)
    return {
        "window_days": days,
        "records": total,
        "by_status": counts,
        "average_attendance_rate_pct": round(100 * attended / total, 1) if total else 0.0,
    }


def _performance_stats(ctx: ToolContext, ids: list[int] | None) -> dict:
    stmt = select(
        func.count(),
        func.avg(Performance.rating),
        func.min(Performance.rating),
        func.max(Performance.rating),
    )
    if ids is not None:
        stmt = stmt.where(Performance.employee_id.in_(ids))
    count, average, lowest, highest = ctx.db.execute(stmt).one()

    by_period_stmt = (
        select(Performance.review_period, func.avg(Performance.rating), func.count())
        .group_by(Performance.review_period)
        .order_by(Performance.review_period)
    )
    if ids is not None:
        by_period_stmt = by_period_stmt.where(Performance.employee_id.in_(ids))

    return {
        "reviews": count,
        "average_rating": round(float(average), 2) if average is not None else None,
        "lowest_rating": float(lowest) if lowest is not None else None,
        "highest_rating": float(highest) if highest is not None else None,
        "by_period": [
            {"review_period": period, "average_rating": round(float(avg), 2), "reviews": n}
            for period, avg, n in ctx.db.execute(by_period_stmt)
        ],
    }


def _leave_stats(ctx: ToolContext, ids: list[int] | None) -> dict:
    today = date.today()
    stmt = select(LeaveRecord.status, func.count()).group_by(LeaveRecord.status)
    current_stmt = select(func.count()).where(
        LeaveRecord.start_date <= today, LeaveRecord.end_date >= today
    )
    if ids is not None:
        stmt = stmt.where(LeaveRecord.employee_id.in_(ids))
        current_stmt = current_stmt.where(LeaveRecord.employee_id.in_(ids))

    return {
        "by_status": {status: count for status, count in ctx.db.execute(stmt)},
        "currently_on_leave": ctx.db.scalar(current_stmt) or 0,
    }


def get_analytics(ctx: ToolContext, args: dict) -> ToolResult:
    ids = visible_employee_ids(ctx)
    metric = (args.get("metric") or "all").strip().lower()
    try:
        days = max(1, min(365, int(args.get("days") or 30)))
    except (TypeError, ValueError):
        days = 30

    builders = {
        "headcount_by_department": lambda: _headcount_by_department(ctx, ids),
        "attendance": lambda: _attendance_stats(ctx, ids, days),
        "performance": lambda: _performance_stats(ctx, ids),
        "leave": lambda: _leave_stats(ctx, ids),
    }

    payload: dict = {}
    withheld: list[str] = []
    for name, key, dataset, column in BLOCKS:
        if metric not in ("all", name):
            continue
        if not shows(ctx, dataset, column):
            withheld.append(f"{dataset}.{column}")
            continue
        payload[key] = builders[key]()

    note = (
        f" Statistics for {', '.join(withheld)} are not available to this role."
        if withheld
        else ""
    )
    return ToolResult(
        summary=(
            f"Aggregate analytics ({metric}). Compensation data is not included in "
            f"analytics.{note}"
        ),
        data=payload,
        row_count=len(payload),
        scope_note=scope_note(ctx, ids),
        withheld_fields=withheld,
    )


def get_reports(ctx: ToolContext, args: dict) -> ToolResult:
    """A rolled-up organisational summary: headcount, attendance, performance, leave."""
    ids = visible_employee_ids(ctx)
    try:
        days = max(1, min(365, int(args.get("days") or 30)))
    except (TypeError, ValueError):
        days = 30

    employee_count_stmt = select(func.count()).select_from(Employee)
    if ids is not None:
        employee_count_stmt = employee_count_stmt.where(Employee.id.in_(ids))

    payload = {
        "generated_on": date.today().isoformat(),
        "total_employees": ctx.db.scalar(employee_count_stmt) or 0,
    }
    builders = {
        "headcount_by_department": lambda: _headcount_by_department(ctx, ids),
        "attendance": lambda: _attendance_stats(ctx, ids, days),
        "performance": lambda: _performance_stats(ctx, ids),
        "leave": lambda: _leave_stats(ctx, ids),
    }

    withheld: list[str] = []
    for _name, key, dataset, column in BLOCKS:
        if shows(ctx, dataset, column):
            payload[key] = builders[key]()
        else:
            withheld.append(f"{dataset}.{column}")

    note = f" {', '.join(withheld)} are withheld from this role." if withheld else ""
    return ToolResult(
        summary=(
            f"Summary report for {payload['total_employees']} employee(s) over the last "
            f"{days} days.{note}"
        ),
        data=payload,
        row_count=payload["total_employees"],
        scope_note=scope_note(ctx, ids),
        withheld_fields=withheld,
    )


ANALYTICS_TOOL = Tool(
    name="get_analytics",
    description=(
        "Aggregated, non-identifying statistics: headcount by department, average attendance "
        "rate, average/min/max performance rating by period, and leave counts. Use for questions "
        "about averages, trends, distributions, rates and totals rather than individual records. "
        "Never returns salary or compensation figures."
    ),
    required_permission=ANALYTICS_READ,
    domain="analytics",
    input_schema={
        "type": "object",
        "properties": {
            "metric": {
                "type": "string",
                "enum": ["all", "headcount", "attendance", "performance", "leave"],
                "description": "Which statistic to compute. Defaults to all.",
            },
            "days": {"type": "integer", "description": "Attendance lookback window in days, 1-365."},
        },
    },
    handler=get_analytics,
)

REPORTS_TOOL = Tool(
    name="get_reports",
    description=(
        "A single rolled-up organisational report combining headcount, attendance, performance "
        "and leave summaries. Use when the user asks for 'a report', 'an overview', 'a summary' "
        "or 'how are we doing' rather than one specific metric. Contains no compensation data."
    ),
    required_permission=REPORTS_READ,
    domain="reporting",
    input_schema={
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "Attendance lookback window in days, 1-365."},
        },
    },
    handler=get_reports,
)
