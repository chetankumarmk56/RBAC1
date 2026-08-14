"""Audit logging for tool requests."""

from sqlalchemy.orm import Session

from models import AuditLog
from rbac.service import Principal

ALLOWED = "ALLOWED"
DENIED = "DENIED"


def log_tool_request(
    db: Session,
    principal: Principal,
    *,
    agent: str | None,
    tool: str | None,
    required_permission: str | None,
    decision: str,
    reason: str | None = None,
    request_summary: str | None = None,
) -> AuditLog:
    """Record one tool request. Called for allowed and denied requests alike."""
    entry = AuditLog(
        user_id=principal.user_id,
        user_email=principal.email,
        role=principal.role,
        agent=agent,
        tool=tool,
        required_permission=required_permission,
        decision=decision,
        reason=reason,
        request_summary=(request_summary or "")[:2000] or None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
