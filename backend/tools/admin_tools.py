"""Administrative tools: the audit trail, and reading or changing the RBAC configuration.

`grant_tool_access` and `revoke_tool_access` are the only tools in the POC that
write. They are gated on `permissions:write`, which only the super_admin role holds,
and they are subject to the same enforcement path as every read tool — the check in
`execute_tool` runs before the handler, so a caller without the permission never
reaches this module.
"""

from sqlalchemy import delete, insert, select

from models import AuditLog, Permission, Role, role_permissions
from rbac.permissions import (
    AUDIT_READ,
    PERMISSIONS_MANAGE,
    PERMISSIONS_WRITE,
    PROTECTED_ROLES,
)
from rbac.service import role_permission_matrix
from tools.base import Tool, ToolContext, ToolResult


def _catalogue() -> list[Tool]:
    """The tool catalogue.

    Imported lazily because `tools.registry` imports this module — a module-level
    import would be circular.
    """
    from tools.registry import ALL_TOOLS

    return ALL_TOOLS


def _role_names(ctx: ToolContext) -> list[str]:
    return list(ctx.db.scalars(select(Role.name).order_by(Role.name)))


def _permissions_of(ctx: ToolContext, role_id: int) -> list[str]:
    """Read a role's permissions straight from the join table, post-commit."""
    return sorted(
        ctx.db.scalars(
            select(Permission.name)
            .join(role_permissions, Permission.id == role_permissions.c.permission_id)
            .where(role_permissions.c.role_id == role_id)
        )
    )


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #

def get_audit_logs(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        limit = max(1, min(200, int(args.get("limit") or 25)))
    except (TypeError, ValueError):
        limit = 25

    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit)
    if decision := (args.get("decision") or "").strip().upper():
        stmt = stmt.where(AuditLog.decision == decision)
    if tool_name := (args.get("tool") or "").strip():
        stmt = stmt.where(AuditLog.tool == tool_name)
    if email := (args.get("user_email") or "").strip().lower():
        stmt = stmt.where(AuditLog.user_email == email)

    entries = list(ctx.db.scalars(stmt))
    rows = [
        {
            "at": entry.created_at.isoformat(),
            "user": entry.user_email,
            "role": entry.role,
            "agent": entry.agent,
            "tool": entry.tool,
            "required_permission": entry.required_permission,
            "decision": entry.decision,
            "reason": entry.reason,
            "request": entry.request_summary,
        }
        for entry in entries
    ]
    denied = sum(1 for row in rows if row["decision"] == "DENIED")

    return ToolResult(
        summary=f"{len(rows)} audit entr(ies), {denied} denied.",
        data=rows,
        row_count=len(rows),
    )


def get_role_permissions(ctx: ToolContext, args: dict) -> ToolResult:
    matrix = role_permission_matrix(ctx.db)
    if role_name := (args.get("role_name") or "").strip().lower():
        matrix = [row for row in matrix if row["role"] == role_name]

    return ToolResult(
        summary=f"RBAC configuration for {len(matrix)} role(s).",
        data=matrix,
        row_count=len(matrix),
    )


def get_tool_permissions(ctx: ToolContext, args: dict) -> ToolResult:
    """The tool-by-role access matrix: which roles can currently run which tool."""
    granted: dict[str, set[str]] = {}
    for role in ctx.db.scalars(select(Role).order_by(Role.name)):
        granted[role.name] = set(_permissions_of(ctx, role.id))

    tool_filter = (args.get("tool_name") or "").strip()
    role_filter = (args.get("role_name") or "").strip().lower()

    rows = []
    for tool in _catalogue():
        if tool_filter and tool.name != tool_filter:
            continue
        roles_with_access = sorted(
            name for name, permissions in granted.items() if tool.required_permission in permissions
        )
        if role_filter and role_filter not in roles_with_access:
            continue
        rows.append(
            {
                "tool": tool.name,
                "domain": tool.domain,
                "required_permission": tool.required_permission,
                "roles_with_access": roles_with_access,
                "writes": tool.mutates,
                "configurable": tool.required_permission != PERMISSIONS_WRITE,
            }
        )

    return ToolResult(
        summary=f"Tool access matrix for {len(rows)} tool(s) across {len(granted)} role(s).",
        data={"roles": sorted(granted), "tools": rows},
        row_count=len(rows),
    )


# --------------------------------------------------------------------------- #
# Writes — super admin only
# --------------------------------------------------------------------------- #

def _change_access(ctx: ToolContext, args: dict, *, grant: bool) -> ToolResult:
    """Grant or revoke a role's access to one tool by editing `role_permissions`."""
    action = "grant" if grant else "revoke"
    role_name = (args.get("role_name") or "").strip().lower()
    tool_name = (args.get("tool_name") or "").strip()

    role = ctx.db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        known = ", ".join(_role_names(ctx))
        return ToolResult(
            summary=f"There is no role named '{role_name}'. Known roles: {known}.",
            audit_note=f"{action} rejected: unknown role '{role_name}'",
        )

    if role.name in PROTECTED_ROLES:
        return ToolResult(
            summary=(
                f"The '{role.name}' role is protected and its permissions cannot be changed. "
                "This prevents a super admin from removing their own ability to manage access."
            ),
            audit_note=f"{action} rejected: '{role.name}' is protected",
        )

    tool = next((candidate for candidate in _catalogue() if candidate.name == tool_name), None)
    if tool is None:
        known = ", ".join(candidate.name for candidate in _catalogue())
        return ToolResult(
            summary=f"There is no tool named '{tool_name}'. Available tools: {known}.",
            audit_note=f"{action} rejected: unknown tool '{tool_name}'",
        )

    if tool.required_permission == PERMISSIONS_WRITE:
        return ToolResult(
            summary=(
                f"'{tool.name}' controls RBAC itself and cannot be granted or revoked from chat. "
                "Change it in seed.py if you need a second super admin."
            ),
            audit_note=f"{action} rejected: '{tool.name}' is not configurable",
        )

    permission = ctx.db.scalar(
        select(Permission).where(Permission.name == tool.required_permission)
    )
    if permission is None:  # pragma: no cover — seeded from the same constants
        return ToolResult(
            summary=f"Permission '{tool.required_permission}' is missing from the database.",
            audit_note=f"{action} failed: permission row missing",
        )

    already = ctx.db.scalar(
        select(role_permissions.c.role_id).where(
            role_permissions.c.role_id == role.id,
            role_permissions.c.permission_id == permission.id,
        )
    )

    if grant and already:
        return ToolResult(
            summary=(
                f"The '{role.name}' role already has access to {tool.name} "
                f"via '{permission.name}'. Nothing changed."
            ),
            data={"role": role.name, "permissions": _permissions_of(ctx, role.id)},
            audit_note=f"grant no-op: '{role.name}' already had '{permission.name}'",
        )

    if not grant and not already:
        return ToolResult(
            summary=(
                f"The '{role.name}' role does not have access to {tool.name} "
                f"('{permission.name}'). Nothing changed."
            ),
            data={"role": role.name, "permissions": _permissions_of(ctx, role.id)},
            audit_note=f"revoke no-op: '{role.name}' did not have '{permission.name}'",
        )

    if grant:
        ctx.db.execute(
            insert(role_permissions).values(role_id=role.id, permission_id=permission.id)
        )
    else:
        ctx.db.execute(
            delete(role_permissions).where(
                role_permissions.c.role_id == role.id,
                role_permissions.c.permission_id == permission.id,
            )
        )
    ctx.db.commit()
    ctx.db.expire(role)  # the `permissions` relationship is now stale

    updated = _permissions_of(ctx, role.id)
    verb = "now has" if grant else "no longer has"
    return ToolResult(
        summary=(
            f"The '{role.name}' role {verb} access to {tool.name} "
            f"('{permission.name}'). It takes effect on that role's next message."
        ),
        data={"role": role.name, "tool": tool.name, "permission": permission.name, "permissions": updated},
        row_count=1,
        audit_note=(
            f"{'granted' if grant else 'revoked'} '{permission.name}' ({tool.name}) "
            f"{'to' if grant else 'from'} '{role.name}'"
        ),
    )


def grant_tool_access(ctx: ToolContext, args: dict) -> ToolResult:
    return _change_access(ctx, args, grant=True)


def revoke_tool_access(ctx: ToolContext, args: dict) -> ToolResult:
    return _change_access(ctx, args, grant=False)


# --------------------------------------------------------------------------- #
# Tool definitions
# --------------------------------------------------------------------------- #

_ROLE_ENUM = ["supervisor", "analyst", "hr", "admin", "super_admin"]

AUDIT_TOOL = Tool(
    name="get_audit_logs",
    description=(
        "The audit trail of tool requests: who asked, which agent handled it, which tool was "
        "attempted, the permission it required, and whether RBAC allowed or denied it. Permission "
        "changes appear here too. Use for questions about access attempts, denied requests, "
        "security review or 'who accessed what'."
    ),
    required_permission=AUDIT_READ,
    domain="audit",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum entries to return, 1-200. Defaults to 25."},
            "decision": {
                "type": "string",
                "enum": ["ALLOWED", "DENIED"],
                "description": "Return only allowed or only denied requests.",
            },
            "tool": {"type": "string", "description": "Filter by tool name, e.g. get_payroll."},
            "user_email": {"type": "string", "description": "Filter by the requesting user's email."},
        },
    },
    handler=get_audit_logs,
)

ROLE_PERMISSIONS_TOOL = Tool(
    name="get_role_permissions",
    description=(
        "The current RBAC configuration: every role with its description and the permissions "
        "granted to it. Use for questions about roles, permissions, or the access-control setup. "
        "For a tool-by-tool view of who can run what, use get_tool_permissions instead."
    ),
    required_permission=PERMISSIONS_MANAGE,
    domain="rbac configuration",
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {
                "type": "string",
                "enum": _ROLE_ENUM,
                "description": "Limit the result to a single role.",
            },
        },
    },
    handler=get_role_permissions,
)

TOOL_PERMISSIONS_TOOL = Tool(
    name="get_tool_permissions",
    description=(
        "The tool access matrix: for every tool, the permission it requires and which roles "
        "currently hold that permission. Use for questions like 'which roles can see payroll', "
        "'what can the analyst role do', or before changing access with grant_tool_access."
    ),
    required_permission=PERMISSIONS_MANAGE,
    domain="rbac configuration",
    input_schema={
        "type": "object",
        "properties": {
            "tool_name": {"type": "string", "description": "Limit the result to a single tool."},
            "role_name": {
                "type": "string",
                "enum": _ROLE_ENUM,
                "description": "Show only tools this role can currently run.",
            },
        },
    },
    handler=get_tool_permissions,
)

GRANT_TOOL_ACCESS_TOOL = Tool(
    name="grant_tool_access",
    description=(
        "Give a role access to a tool, by granting that tool's required permission to the role. "
        "Use when asked to give, grant, allow or enable a role's access to some data or tool — "
        "for example 'let HR see payroll' or 'give the analyst role access to leave records'. "
        "The change is written to the database and applies from that role's next message."
    ),
    required_permission=PERMISSIONS_WRITE,
    domain="rbac configuration",
    mutates=True,
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {
                "type": "string",
                "enum": _ROLE_ENUM,
                "description": "The role to grant access to.",
            },
            "tool_name": {
                "type": "string",
                "description": "The tool to grant, e.g. get_payroll or get_leave.",
            },
        },
        "required": ["role_name", "tool_name"],
    },
    handler=grant_tool_access,
)

REVOKE_TOOL_ACCESS_TOOL = Tool(
    name="revoke_tool_access",
    description=(
        "Remove a role's access to a tool, by revoking that tool's required permission from the "
        "role. Use when asked to remove, revoke, block or disable a role's access — for example "
        "'stop the supervisor role seeing payroll'. The change is written to the database and "
        "applies from that role's next message."
    ),
    required_permission=PERMISSIONS_WRITE,
    domain="rbac configuration",
    mutates=True,
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {
                "type": "string",
                "enum": _ROLE_ENUM,
                "description": "The role to revoke access from.",
            },
            "tool_name": {
                "type": "string",
                "description": "The tool to revoke, e.g. get_payroll or get_leave.",
            },
        },
        "required": ["role_name", "tool_name"],
    },
    handler=revoke_tool_access,
)
