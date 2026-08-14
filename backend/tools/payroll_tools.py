"""Payroll tool — the most sensitive data source in the POC."""

from sqlalchemy import select

from models import Employee, Payroll
from rbac.permissions import PAYROLL_READ
from tools.base import Tool, ToolContext, ToolResult, scope_note, visible_employee_ids


def get_payroll(ctx: ToolContext, args: dict) -> ToolResult:
    ids = visible_employee_ids(ctx)

    stmt = select(Payroll).order_by(Payroll.period.desc())
    if ids is not None:
        stmt = stmt.where(Payroll.employee_id.in_(ids))
    if period := (args.get("period") or "").strip():
        stmt = stmt.where(Payroll.period == period)
    else:
        # Default to the most recent period so "show me payroll" stays readable.
        latest_stmt = select(Payroll.period).order_by(Payroll.period.desc()).limit(1)
        if ids is not None:
            latest_stmt = latest_stmt.where(Payroll.employee_id.in_(ids))
        if latest := ctx.db.scalar(latest_stmt):
            stmt = stmt.where(Payroll.period == latest)

    records = list(ctx.db.scalars(stmt))

    employee_stmt = select(Employee)
    if ids is not None:
        employee_stmt = employee_stmt.where(Employee.id.in_(ids))
    employees = {employee.id: employee for employee in ctx.db.scalars(employee_stmt)}

    name_filter = (args.get("employee_name") or "").strip().lower()

    rows = []
    for record in records:
        employee = employees.get(record.employee_id)
        if employee is None:
            continue
        if name_filter and name_filter not in employee.full_name.lower():
            continue
        rows.append(
            {
                "name": employee.full_name,
                "department": employee.department,
                "title": employee.title,
                "period": record.period,
                "base_salary": float(record.base_salary),
                "bonus": float(record.bonus),
                "deductions": float(record.deductions),
                "net_pay": float(record.net_pay),
                "currency": record.currency,
            }
        )
    rows.sort(key=lambda row: row["name"])

    total_net = round(sum(row["net_pay"] for row in rows), 2)
    periods = sorted({row["period"] for row in rows})

    return ToolResult(
        summary=(
            f"{len(rows)} payroll record(s) for period(s) {', '.join(periods) or 'n/a'}. "
            f"Total net pay {total_net}."
        ),
        data={"periods": periods, "total_net_pay": total_net, "records": rows},
        row_count=len(rows),
        scope_note=scope_note(ctx, ids),
    )


PAYROLL_TOOL = Tool(
    name="get_payroll",
    description=(
        "Payroll records: base salary, bonus, deductions and net pay per employee per monthly "
        "period, plus the total net pay. Use for any question about salary, pay, compensation, "
        "earnings, bonuses or payroll cost. Defaults to the most recent period."
    ),
    required_permission=PAYROLL_READ,
    domain="payroll",
    input_schema={
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "description": "Payroll period as YYYY-MM, e.g. 2026-07. Omit for the latest period.",
            },
            "employee_name": {"type": "string", "description": "Filter by full or partial employee name."},
        },
    },
    handler=get_payroll,
)
