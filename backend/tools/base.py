"""Tool contract.

A tool is the only path from an agent to the database. Every tool declares a
required permission, and `execute_tool` checks it before the handler runs — so a
handler is never reached by an unauthorized caller.

Tools that read a catalogued dataset also declare it. `execute_tool` then applies
the caller's row scope (via `visible_employee_ids`, which handlers call) and strips
the fields their role may not see from the result, before the agent — or the model
writing the reply — ever sees them.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Employee
from rbac.datasets import (
    SCOPE_ALL,
    SCOPE_DEPARTMENT,
    SCOPE_SELF,
    SCOPE_TEAM,
    get_dataset,
)
from rbac.service import Principal, check_permission


@dataclass(frozen=True)
class ToolContext:
    """Everything a handler may use. The principal comes from the database."""

    db: Session
    principal: Principal


@dataclass(frozen=True)
class ToolResult:
    """A handler's output, handed back to the agent as a tool result."""

    summary: str
    data: Any = None
    row_count: int = 0
    scope_note: str | None = None
    # What to write to the audit log instead of a row count. Write tools set this so
    # the trail records what changed, not how many rows came back.
    audit_note: str | None = None
    # A material limitation on what just happened — "the permission was already held,
    # but these columns are still withheld". Kept out of `summary` because the
    # responder rewrites prose and will drop a clause it reads as an aside; this is
    # handed over as its own field with an instruction never to omit it.
    caveat: str | None = None
    # Fields removed by the field-level policy. Filled in by `execute_tool`, not by
    # handlers, and surfaced in the reply and the trace.
    withheld_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    required_permission: str
    input_schema: dict
    handler: Callable[[ToolContext, dict], ToolResult]
    # Purely descriptive; used in the agent's system prompt.
    domain: str = ""
    # True for tools that change state rather than read it. Drives the wording of a
    # denial message and is surfaced in the tool access matrix.
    mutates: bool = False
    # The catalogued dataset this tool reads, if any. Drives field-level redaction.
    dataset: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_claude_schema(self) -> dict:
        """The shape Claude's tool-use API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def shows(ctx: ToolContext, dataset_key: str, field_key: str) -> bool:
    """Whether the caller's role may see one field.

    Handlers only need this for figures they compute into the *summary* text, which
    is prose and cannot be redacted after the fact. Row data is handled centrally.
    """
    return ctx.principal.field_access.allows(dataset_key, field_key)


# --------------------------------------------------------------------------- #
# Row-level scope
# --------------------------------------------------------------------------- #

def visible_employee_ids(ctx: ToolContext) -> list[int] | None:
    """Row-level data scope, read from the caller's role.

    `all` returns None, meaning "no row restriction". The narrower scopes resolve
    against the employee row this login is linked to; a login with no linked
    employee sees nothing under them, because there is no anchor to scope from.

    This is scoping, not authorization — a role still needs `payroll:read` to reach
    payroll at all; this only narrows *which rows* it gets.
    """
    scope = ctx.principal.row_scope
    if scope == SCOPE_ALL:
        return None

    if ctx.principal.employee_id is None:
        return []

    if scope == SCOPE_SELF:
        return [ctx.principal.employee_id]

    if scope == SCOPE_DEPARTMENT:
        department = ctx.db.scalar(
            select(Employee.department).where(Employee.id == ctx.principal.employee_id)
        )
        if department is None:
            return [ctx.principal.employee_id]
        return list(ctx.db.scalars(select(Employee.id).where(Employee.department == department)))

    # SCOPE_TEAM: the caller plus their direct reports.
    reports = ctx.db.scalars(
        select(Employee.id).where(Employee.manager_id == ctx.principal.employee_id)
    ).all()
    return [ctx.principal.employee_id, *reports]


_SCOPE_WORDING = {
    SCOPE_DEPARTMENT: "the caller's own department",
    SCOPE_TEAM: "the caller's own team",
    SCOPE_SELF: "the caller's own record",
}


def scope_note(ctx: ToolContext, ids: list[int] | None) -> str | None:
    if ids is None:
        return None

    scope = ctx.principal.row_scope
    if not ids:
        return (
            f"No rows are visible: the '{ctx.principal.role}' role is scoped to "
            f"{_SCOPE_WORDING.get(scope, scope)}, and this login is not linked to an employee record."
        )

    where = _SCOPE_WORDING.get(scope, scope)
    return (
        f"Scoped to {where} ({len(ids)} employee{'' if len(ids) == 1 else 's'}) "
        f"because the '{ctx.principal.role}' role has row scope '{scope}'."
    )


# --------------------------------------------------------------------------- #
# Field-level redaction
# --------------------------------------------------------------------------- #

def _strip(value: Any, drop: set[str]) -> Any:
    """Remove `drop` keys from every dict in a nested result payload."""
    if isinstance(value, dict):
        return {key: _strip(item, drop) for key, item in value.items() if key not in drop}
    if isinstance(value, list):
        return [_strip(item, drop) for item in value]
    return value


def apply_field_policy(tool: Tool, ctx: ToolContext, result: ToolResult) -> ToolResult:
    """Strip the fields the caller's role may not see out of a tool result."""
    dataset = get_dataset(tool.dataset) if tool.dataset else None
    if dataset is None:
        return result

    withheld = [
        spec
        for spec in dataset.fields
        if not spec.locked and not ctx.principal.field_access.allows(dataset.key, spec.key)
    ]
    if not withheld:
        return result

    drop: set[str] = set()
    for spec in withheld:
        drop.add(spec.key)
        drop.update(spec.derived)  # aggregates computed from a withheld column

    return replace(
        result,
        data=_strip(result.data, drop),
        withheld_fields=[spec.key for spec in withheld],
    )


def execute_tool(tool: Tool, ctx: ToolContext, tool_input: dict) -> ToolResult:
    """The enforcement point: RBAC check first, database access second.

    Raises rbac.PermissionDenied when the check fails, so no query is ever issued.
    Field-level policy is applied to whatever the handler returns, so a column the
    role may not see never leaves this function.
    """
    check_permission(ctx.principal, tool.required_permission)
    return apply_field_policy(tool, ctx, tool.handler(ctx, tool_input or {}))
