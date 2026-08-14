"""The authorization check.

Permissions are resolved from the database for the authenticated user. Nothing
here reads a role or permission supplied by the frontend, the planner, or the
LLM's tool arguments.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Permission, Role, User, role_permissions


class PermissionDenied(Exception):
    """Raised by a tool when the caller lacks the tool's required permission."""

    def __init__(self, required_permission: str, role: str) -> None:
        self.required_permission = required_permission
        self.role = role
        super().__init__(f"role '{role}' does not hold '{required_permission}'")


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, with permissions resolved from the database."""

    user_id: int
    email: str
    full_name: str
    role: str
    permissions: frozenset[str]
    employee_id: int | None

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def load_principal(db: Session, user: User) -> Principal:
    """Build a Principal by reading the user's role and permissions from the DB."""
    permission_names = db.scalars(
        select(Permission.name)
        .join(role_permissions, Permission.id == role_permissions.c.permission_id)
        .where(role_permissions.c.role_id == user.role_id)
    ).all()

    return Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.name,
        permissions=frozenset(permission_names),
        employee_id=user.employee_id,
    )


def check_permission(principal: Principal, required_permission: str) -> None:
    """Raise PermissionDenied unless the principal holds the permission."""
    if not principal.has(required_permission):
        raise PermissionDenied(required_permission, principal.role)


def role_permission_matrix(db: Session) -> list[dict]:
    """Every role with its permissions — backing data for the get_role_permissions tool."""
    roles = db.scalars(select(Role).order_by(Role.name)).all()
    return [
        {
            "role": role.name,
            "description": role.description,
            "permissions": sorted(permission.name for permission in role.permissions),
        }
        for role in roles
    ]
