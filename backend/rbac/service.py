"""The authorization check.

Everything a role may do is resolved from the database for the authenticated user:
the permissions it holds, the models it may run, the fields it may see and how far
its rows reach. Nothing here reads a role, a permission, a model or a field supplied
by the frontend, the planner, or the LLM's tool arguments.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import (
    Permission,
    Role,
    User,
    role_data_scope,
    role_field_access,
    role_models,
    role_permissions,
)
from rbac.datasets import DATASETS_BY_KEY, normalise_scope
from rbac.model_catalog import MODELS_BY_KEY, preference_order


class PermissionDenied(Exception):
    """Raised by a tool when the caller lacks the tool's required permission."""

    def __init__(self, required_permission: str, role: str) -> None:
        self.required_permission = required_permission
        self.role = role
        super().__init__(f"role '{role}' does not hold '{required_permission}'")


class ModelAccessDenied(Exception):
    """Raised when the caller asks for a model their role is not allowed to run."""

    def __init__(self, model_key: str, role: str, allowed: list[str]) -> None:
        self.model_key = model_key
        self.role = role
        self.allowed = allowed
        super().__init__(f"role '{role}' is not allowed to use model '{model_key}'")


@dataclass(frozen=True)
class FieldPolicy:
    """Column-level data access: the (dataset, field) pairs a role may see.

    `granted` is what the console ticked. What a caller actually gets is narrower: a
    column the role holds is withheld anyway when it is part of a set that
    reconstructs a column the role does *not* hold. Every read goes through
    `allows`, so that closure applies to redacted payloads and to the figures
    handlers write into their prose summaries alike.
    """

    granted: frozenset[tuple[str, str]]

    def allows(self, dataset_key: str, field_key: str) -> bool:
        dataset = DATASETS_BY_KEY.get(dataset_key)
        if dataset is None:  # not a catalogued dataset — nothing to redact against
            return (dataset_key, field_key) in self.granted
        return field_key not in dataset.effective_withheld(self.allowed_fields(dataset_key))

    def allowed_fields(self, dataset_key: str) -> set[str]:
        """The raw grants for this dataset, before the reconstruction closure."""
        return {field for dataset, field in self.granted if dataset == dataset_key}

    def withheld_fields(self, dataset_key: str) -> list[str]:
        """Catalogue fields of this dataset the role does not get, in catalogue order."""
        dataset = DATASETS_BY_KEY.get(dataset_key)
        if dataset is None:
            return []
        withheld = dataset.effective_withheld(self.allowed_fields(dataset_key))
        return [spec.key for spec in dataset.fields if spec.key in withheld]


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, with everything RBAC needs resolved from the DB."""

    user_id: int
    email: str
    full_name: str
    role: str
    permissions: frozenset[str]
    employee_id: int | None
    # LLMs this role may run, most capable first.
    models: tuple[str, ...] = ()
    # Row reach: all | department | team | self.
    row_scope: str = "all"
    # Column reach, per dataset.
    field_access: FieldPolicy = FieldPolicy(granted=frozenset())

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def may_use_model(self, model_key: str) -> bool:
        return model_key in self.models


def load_principal(db: Session, user: User) -> Principal:
    """Build a Principal by reading the user's role configuration from the DB."""
    permission_names = db.scalars(
        select(Permission.name)
        .join(role_permissions, Permission.id == role_permissions.c.permission_id)
        .where(role_permissions.c.role_id == user.role_id)
    ).all()

    granted_models = set(
        db.scalars(select(role_models.c.model_key).where(role_models.c.role_id == user.role_id))
    )
    scope = db.scalar(select(role_data_scope.c.scope).where(role_data_scope.c.role_id == user.role_id))

    fields = db.execute(
        select(role_field_access.c.dataset_key, role_field_access.c.field_key).where(
            role_field_access.c.role_id == user.role_id
        )
    ).all()

    return Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.name,
        permissions=frozenset(permission_names),
        employee_id=user.employee_id,
        # Unknown keys are dropped: a model removed from the catalogue cannot be
        # resurrected by a stale row.
        models=tuple(model.key for model in preference_order(granted_models & set(MODELS_BY_KEY))),
        row_scope=normalise_scope(scope),
        field_access=FieldPolicy(granted=frozenset((dataset, field) for dataset, field in fields)),
    )


def check_permission(principal: Principal, required_permission: str) -> None:
    """Raise PermissionDenied unless the principal holds the permission."""
    if not principal.has(required_permission):
        raise PermissionDenied(required_permission, principal.role)


def check_model_access(principal: Principal, model_key: str) -> None:
    """Raise ModelAccessDenied unless the principal's role may run that model."""
    if not principal.may_use_model(model_key):
        raise ModelAccessDenied(model_key, principal.role, list(principal.models))


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


def role_model_matrix(db: Session) -> dict[str, list[str]]:
    """role name -> allowed model keys, most capable first."""
    rows = db.execute(
        select(Role.name, role_models.c.model_key).join(
            role_models, Role.id == role_models.c.role_id, isouter=True
        )
    ).all()

    granted: dict[str, set[str]] = {}
    for name, model_key in rows:
        granted.setdefault(name, set())
        if model_key:
            granted[name].add(model_key)

    return {
        name: [model.key for model in preference_order(keys)] for name, keys in sorted(granted.items())
    }


def role_field_matrix(db: Session) -> dict[str, set[tuple[str, str]]]:
    """role name -> the (dataset, field) pairs it may see."""
    rows = db.execute(
        select(Role.name, role_field_access.c.dataset_key, role_field_access.c.field_key).join(
            role_field_access, Role.id == role_field_access.c.role_id, isouter=True
        )
    ).all()

    granted: dict[str, set[tuple[str, str]]] = {}
    for name, dataset_key, field_key in rows:
        granted.setdefault(name, set())
        if dataset_key:
            granted[name].add((dataset_key, field_key))
    return granted


def role_scope_map(db: Session) -> dict[str, str]:
    """role name -> row scope, defaulting to `all` when no row exists."""
    rows = db.execute(
        select(Role.name, role_data_scope.c.scope).join(
            role_data_scope, Role.id == role_data_scope.c.role_id, isouter=True
        )
    ).all()
    return {name: normalise_scope(scope) for name, scope in rows}
