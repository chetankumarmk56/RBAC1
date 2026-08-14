"""Tool contract.

A tool is the only path from an agent to the database. Every tool declares a
required permission, and `execute_tool` checks it before the handler runs — so a
handler is never reached by an unauthorized caller.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Employee
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
    tags: list[str] = field(default_factory=list)

    def to_claude_schema(self) -> dict:
        """The shape Claude's tool-use API expects."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


def visible_employee_ids(ctx: ToolContext) -> list[int] | None:
    """Row-level data scope.

    A supervisor sees themselves plus their direct reports. Every other role sees
    the whole company. Returning None means "no row restriction".

    This is scoping, not authorization — a supervisor still needs `payroll:read`
    to reach payroll at all; this only narrows *which rows* they get.
    """
    if ctx.principal.role != "supervisor":
        return None

    if ctx.principal.employee_id is None:
        return []

    reports = ctx.db.scalars(
        select(Employee.id).where(Employee.manager_id == ctx.principal.employee_id)
    ).all()
    return [ctx.principal.employee_id, *reports]


def scope_note(ctx: ToolContext, ids: list[int] | None) -> str | None:
    if ids is None:
        return None
    return f"Scoped to the caller's own team ({len(ids)} employees) because their role is supervisor."


def execute_tool(tool: Tool, ctx: ToolContext, tool_input: dict) -> ToolResult:
    """The enforcement point: RBAC check first, database access second.

    Raises rbac.PermissionDenied when the check fails, so no query is ever issued.
    """
    check_permission(ctx.principal, tool.required_permission)
    return tool.handler(ctx, tool_input or {})
