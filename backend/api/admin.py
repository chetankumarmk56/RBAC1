"""The access-control console — super admin only.

Every endpoint here is gated on `permissions:write`, so hiding the page in the
frontend is presentation, not security: a caller without the permission gets a 403
from the API regardless of what the UI shows them.

Changes are applied through the same tools the chat agent uses — `grant_tool_access`
/ `revoke_tool_access` for datasets and tools, `set_model_access` for models,
`set_field_access` and `set_data_scope` for column and row reach — so the console and
the chat share one code path, one RBAC check and one audit trail. Console changes are
tagged `admin_console` in `audit_logs` so they can be told apart from chat-driven ones.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.llm import model_is_available
from auth.dependencies import require_permission
from db.session import get_db
from models import Role
from rbac.audit import ALLOWED, DENIED, log_tool_request
from rbac.datasets import DATASET_CATALOGUE, DATA_SCOPES
from rbac.model_catalog import MODEL_CATALOGUE
from rbac.permissions import PERMISSIONS_WRITE, PROTECTED_ROLES
from rbac.service import (
    PermissionDenied,
    Principal,
    role_field_matrix,
    role_model_matrix,
    role_scope_map,
)
from schemas import (
    AccessChangeRequest,
    AccessChangeResponse,
    AccessMatrix,
    DatasetSummary,
    DataScopeRequest,
    FieldAccessRequest,
    FieldSummary,
    ModelAccessRequest,
    ModelSummary,
    RoleSummary,
    ScopeOption,
    ToolSummary,
)
from tools.base import Tool, ToolContext, execute_tool
from tools.registry import ALL_TOOLS, get_tool

router = APIRouter(prefix="/api/admin", tags=["admin"])

CONSOLE_AGENT = "admin_console"


def _granted_fields(
    matrix: dict[str, set[tuple[str, str]]], role_name: str, dataset_key: str
) -> set[str]:
    return {field for dataset, field in matrix.get(role_name, set()) if dataset == dataset_key}


def _build_matrix(db: Session) -> AccessMatrix:
    roles = list(db.scalars(select(Role).order_by(Role.name)))
    granted = {role.name: {permission.name for permission in role.permissions} for role in roles}
    models = role_model_matrix(db)
    fields = role_field_matrix(db)
    scopes = role_scope_map(db)

    return AccessMatrix(
        roles=[
            RoleSummary(
                name=role.name,
                description=role.description,
                permissions=sorted(granted[role.name]),
                protected=role.name in PROTECTED_ROLES,
                models=models.get(role.name, []),
                row_scope=scopes.get(role.name, "all"),
                fields={
                    dataset.key: sorted(_granted_fields(fields, role.name, dataset.key))
                    for dataset in DATASET_CATALOGUE
                },
                fields_withheld={
                    dataset.key: [
                        spec.key
                        for spec in dataset.fields
                        if spec.key
                        in dataset.effective_withheld(
                            _granted_fields(fields, role.name, dataset.key)
                        )
                    ]
                    for dataset in DATASET_CATALOGUE
                },
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
        models=[
            ModelSummary(
                key=model.key,
                label=model.label,
                provider=model.provider,
                blurb=model.blurb,
                available=model_is_available(model),
                roles_with_access=sorted(
                    name for name, keys in models.items() if model.key in keys
                ),
            )
            for model in MODEL_CATALOGUE
        ],
        datasets=[
            DatasetSummary(
                key=dataset.key,
                label=dataset.label,
                blurb=dataset.blurb,
                required_permission=dataset.permission,
                tool=dataset.tool,
                fields=[
                    FieldSummary(key=spec.key, label=spec.label, locked=spec.locked)
                    for spec in dataset.fields
                ],
                roles_with_access=sorted(
                    name for name, permissions in granted.items() if dataset.permission in permissions
                ),
            )
            for dataset in DATASET_CATALOGUE
        ],
        scopes=[ScopeOption(key=key, description=description) for key, description in DATA_SCOPES.items()],
    )


def _apply(
    db: Session,
    principal: Principal,
    tool: Tool | None,
    arguments: dict,
    summary: str,
) -> AccessChangeResponse:
    """Run one console change through the tool layer and audit it.

    The dependency on each endpoint already enforced `permissions:write`. Going
    through `execute_tool` anyway keeps the check inside the tool authoritative, so
    the endpoint cannot drift into being the only thing standing in the way.
    """
    if tool is None:  # pragma: no cover — names are constants
        raise HTTPException(status_code=500, detail="RBAC management tool is missing")

    ctx = ToolContext(db=db, principal=principal)
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
            request_summary=f"console: {summary}",
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
        request_summary=f"console: {summary}",
    )

    db.expire_all()  # the tool committed; reload the role configuration for the response
    return AccessChangeResponse(
        changed=result.row_count == 1,
        message=result.summary,
        matrix=_build_matrix(db),
    )


@router.get("/access-matrix", response_model=AccessMatrix)
def access_matrix(
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessMatrix:
    """Every role with the tools, models, datasets, fields and row scope it holds."""
    del principal  # gating only
    return _build_matrix(db)


@router.post("/access", response_model=AccessChangeResponse)
def set_access(
    payload: AccessChangeRequest,
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessChangeResponse:
    """Grant or revoke one role's access to one tool — and so to its dataset."""
    tool_name = "grant_tool_access" if payload.granted else "revoke_tool_access"
    return _apply(
        db,
        principal,
        get_tool(tool_name),
        {"role_name": payload.role_name, "tool_name": payload.tool_name},
        f"{tool_name} {payload.tool_name} for {payload.role_name}",
    )


@router.post("/model-access", response_model=AccessChangeResponse)
def set_model_access(
    payload: ModelAccessRequest,
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessChangeResponse:
    """Allow or stop a role running one language model."""
    return _apply(
        db,
        principal,
        get_tool("set_model_access"),
        {
            "role_name": payload.role_name,
            "model_key": payload.model_key,
            "granted": payload.granted,
        },
        f"set_model_access {payload.model_key}={payload.granted} for {payload.role_name}",
    )


@router.post("/field-access", response_model=AccessChangeResponse)
def set_field_access(
    payload: FieldAccessRequest,
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessChangeResponse:
    """Show or withhold one field of one dataset for a role."""
    return _apply(
        db,
        principal,
        get_tool("set_field_access"),
        {
            "role_name": payload.role_name,
            "dataset": payload.dataset,
            "field": payload.field,
            "granted": payload.granted,
        },
        f"set_field_access {payload.dataset}.{payload.field}={payload.granted} for {payload.role_name}",
    )


@router.post("/data-scope", response_model=AccessChangeResponse)
def set_data_scope(
    payload: DataScopeRequest,
    principal: Principal = Depends(require_permission(PERMISSIONS_WRITE)),
    db: Session = Depends(get_db),
) -> AccessChangeResponse:
    """Set how far a role's rows reach."""
    return _apply(
        db,
        principal,
        get_tool("set_data_scope"),
        {"role_name": payload.role_name, "scope": payload.scope},
        f"set_data_scope {payload.scope} for {payload.role_name}",
    )
