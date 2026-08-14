"""The access-control console — super admin only.

Every endpoint here is gated on `permissions:write`, so hiding the page in the
frontend is presentation, not security: a caller without the permission gets a 403
from the API regardless of what the UI shows them.

Changes are applied through the same `grant_tool_access` / `revoke_tool_access` tools
the chat agent uses, so the console and the chat share one code path, one RBAC check
and one audit trail. Console changes are tagged `admin_console` in `audit_logs` so
they can be told apart from chat-driven ones.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.dependencies import require_permission
from db.session import get_db
from models import Role
from rbac.audit import ALLOWED, DENIED, log_tool_request
from rbac.permissions import PERMISSIONS_WRITE, PROTECTED_ROLES
from rbac.service import PermissionDenied, Principal
from schemas import (
    AccessChangeRequest,
    AccessChangeResponse,
    AccessMatrix,
    RoleSummary,
    ToolSummary,
)
from tools.base import ToolContext, execute_tool
from tools.registry import ALL_TOOLS, get_tool

router = APIRouter(prefix="/api/admin", tags=["admin"])

CONSOLE_AGENT = "admin_console"


def _build_matrix(db: Session) -> AccessMatrix:
    roles = list(db.scalars(select(Role).order_by(Role.name)))
    granted = {role.name: {permission.name for permission in role.permissions} for role in roles}

    return AccessMatrix(
        roles=[
            RoleSummary(
                name=role.name,
                description=role.description,
                permissions=sorted(granted[role.name]),
                protected=role.name in PROTECTED_ROLES,
            )
            for role in roles
        ],
        tools=[
            ToolSummary(
                name=tool.name,
                domain=tool.domain,
                description=tool.description,
                required_permission=tool.required_permission,
                mutates=tool.mutates,
                configurable=tool.required_permission != PERMISSIONS_WRITE,
                roles_with_access=sorted(
                    name
                    for name, permissions in granted.items()
                    if tool.required_permission in permissions
                ),
            )
            for tool in ALL_TOOLS
        ],
    )


@router.get("/access-matrix", response_model=AccessMatrix)
def access_matrix(
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessMatrix:
    """Every role, every tool, and which roles can currently run which tool."""
    del principal  # gating only
    return _build_matrix(db)


@router.post("/access", response_model=AccessChangeResponse)
def set_access(
    payload: AccessChangeRequest,
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessChangeResponse:
    """Grant or revoke one role's access to one tool."""
    tool_name = "grant_tool_access" if payload.granted else "revoke_tool_access"
    tool = get_tool(tool_name)
    if tool is None:  # pragma: no cover — names are constants
        raise HTTPException(status_code=500, detail="RBAC management tool is missing")

    ctx = ToolContext(db=db, principal=principal)
    arguments = {"role_name": payload.role_name, "tool_name": payload.tool_name}

    # The dependency above already enforced `permissions:write`. Going through
    # execute_tool anyway keeps the check inside the tool authoritative, so the
    # endpoint cannot drift into being the only thing standing in the way.
    try:
        result = execute_tool(tool, ctx, arguments)
    except PermissionDenied as exc:
        log_tool_request(
            db,
            principal,
            agent=CONSOLE_AGENT,
            tool=tool.name,
            required_permission=tool.required_permission,
            decision=DENIED,
            reason=str(exc),
            request_summary=f"console: {tool_name} {payload.tool_name} for {payload.role_name}",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None

    log_tool_request(
        db,
        principal,
        agent=CONSOLE_AGENT,
        tool=tool.name,
        required_permission=tool.required_permission,
        decision=ALLOWED,
        reason=result.audit_note or result.summary,
        request_summary=f"console: {tool_name} {payload.tool_name} for {payload.role_name}",
    )

    db.expire_all()  # the tool committed; reload role→permission links for the response
    return AccessChangeResponse(
        changed=result.row_count == 1,
        message=result.summary,
        matrix=_build_matrix(db),
    )
