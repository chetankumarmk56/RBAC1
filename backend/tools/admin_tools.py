"""Administrative tools: the audit trail, and reading or changing the RBAC configuration.

The write tools here — tool access, model access, field access and row scope — are
the only tools in the POC that write. They are gated on `permissions:write`, which
only the super_admin role holds, and they are subject to the same enforcement path as
every read tool: the check in `execute_tool` runs before the handler, so a caller
without the permission never reaches this module.

The Access control page posts to these same tools, so the console and the chat share
one implementation, one RBAC check and one audit trail.
"""

from sqlalchemy import delete, insert, select, update

from models import (
    AuditLog,
    Permission,
    Role,
    role_data_scope,
    role_field_access,
    role_models,
    role_permissions,
)
from rbac.datasets import DATASET_CATALOGUE, DATA_SCOPES, SCOPE_KEYS, get_dataset, normalise_scope
from rbac.model_catalog import ALL_MODEL_KEYS, get_model, preference_order
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
        # Holding the dataset is not the same as seeing all of it. Reporting a bare
        # "nothing changed" here sends the super admin away believing the role has
        # everything, when the columns they actually asked about may be withheld — so
        # name them and point at the tool that changes them.
        withheld = _withheld_columns(ctx, role.id, tool)
        note = (
            f" It does not see {_join_labels(withheld)}, though — "
            f"{'that column is' if len(withheld) == 1 else 'those columns are'} withheld from "
            f"this role. Use set_field_access to change "
            f"{'it' if len(withheld) == 1 else 'them'}."
            if withheld
            else ""
        )
        return ToolResult(
            summary=(
                f"The '{role.name}' role already has access to {tool.name} "
                f"via '{permission.name}'. Nothing changed.{note}"
            ),
            caveat=(
                f"Nothing was granted. The '{role.name}' role already held "
                f"'{permission.name}', but it still does not see "
                f"{_join_labels(withheld)} — {'that column is' if len(withheld) == 1 else 'those columns are'} "
                f"withheld at the column level, which is a separate setting. To change that, ask "
                f"again naming the column (for example \"let the {role.name} see "
                f"{withheld[0].label.lower()}\"), or tick it on the Access control page."
                if withheld
                else None
            ),
            data={
                "role": role.name,
                "permissions": _permissions_of(ctx, role.id),
                "fields_withheld": [spec.key for spec in withheld],
            },
            audit_note=(
                f"grant no-op: '{role.name}' already had '{permission.name}'"
                + (f"; still withheld: {', '.join(spec.key for spec in withheld)}" if withheld else "")
            ),
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

    # One permission can gate several tools — `permissions:manage` gates four. The
    # write above moved all of them, so naming only the tool the caller happened to
    # mention would under-report what just changed.
    gated = _tools_gated_by(permission.name)
    others = [name for name in gated if name != tool.name]
    also = (
        f" That permission also gates {_join(others)}, so "
        f"{'it is' if len(others) == 1 else 'they are'} "
        f"{'now available to this role too' if grant else 'no longer available to it either'}."
        if others
        else ""
    )

    # Granting the dataset says nothing about its columns; flag any the role still
    # will not see, so this does not read as "the role now sees everything".
    withheld = _withheld_columns(ctx, role.id, tool) if grant else []
    column_note = (
        f" It still does not see {_join_labels(withheld)} — use set_field_access to "
        f"change {'that' if len(withheld) == 1 else 'those'}."
        if withheld
        else ""
    )

    return ToolResult(
        summary=(
            f"The '{role.name}' role {verb} access to {tool.name} "
            f"('{permission.name}').{also}{column_note} It takes effect on that role's next message."
        ),
        caveat=(
            f"The dataset is granted, but the '{role.name}' role still does not see "
            f"{_join_labels(withheld)} — {'that column is' if len(withheld) == 1 else 'those columns are'} "
            f"withheld at the column level, which is a separate setting. To change that, ask again "
            f"naming the column (for example \"let the {role.name} see {withheld[0].label.lower()}\"), "
            f"or tick it on the Access control page."
            if withheld
            else None
        ),
        data={
            "role": role.name,
            "tool": tool.name,
            "tools_affected": gated,
            "permission": permission.name,
            "permissions": updated,
            "fields_withheld": [spec.key for spec in withheld],
        },
        row_count=1,
        audit_note=(
            f"{'granted' if grant else 'revoked'} '{permission.name}' ({', '.join(gated)}) "
            f"{'to' if grant else 'from'} '{role.name}'"
        ),
    )


def grant_tool_access(ctx: ToolContext, args: dict) -> ToolResult:
    return _change_access(ctx, args, grant=True)


def revoke_tool_access(ctx: ToolContext, args: dict) -> ToolResult:
    return _change_access(ctx, args, grant=False)


# --------------------------------------------------------------------------- #
# Model access — which LLM a role may run
# --------------------------------------------------------------------------- #

def _editable_role(ctx: ToolContext, role_name: str, action: str) -> tuple[Role | None, ToolResult | None]:
    """Resolve a role for a write, or explain why it cannot be written to."""
    role = ctx.db.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        known = ", ".join(_role_names(ctx))
        return None, ToolResult(
            summary=f"There is no role named '{role_name}'. Known roles: {known}.",
            audit_note=f"{action} rejected: unknown role '{role_name}'",
        )

    if role.name in PROTECTED_ROLES:
        return None, ToolResult(
            summary=(
                f"The '{role.name}' role is protected and its configuration cannot be changed. "
                "This prevents a super admin from cutting off their own access."
            ),
            audit_note=f"{action} rejected: '{role.name}' is protected",
        )
    return role, None


def _models_of(ctx: ToolContext, role_id: int) -> list[str]:
    keys = set(ctx.db.scalars(select(role_models.c.model_key).where(role_models.c.role_id == role_id)))
    return [model.key for model in preference_order(keys)]


def get_model_access(ctx: ToolContext, args: dict) -> ToolResult:
    """Which models each role may run, most capable first."""
    role_filter = (args.get("role_name") or "").strip().lower()

    rows = []
    for role in ctx.db.scalars(select(Role).order_by(Role.name)):
        if role_filter and role.name != role_filter:
            continue
        allowed = _models_of(ctx, role.id)
        rows.append(
            {
                "role": role.name,
                "models_allowed": allowed,
                "models_denied": [key for key in ALL_MODEL_KEYS if key not in allowed],
                "default_model": allowed[0] if allowed else None,
            }
        )

    return ToolResult(
        summary=f"Model access for {len(rows)} role(s). A role runs the most capable model it holds.",
        data={"models": ALL_MODEL_KEYS, "roles": rows},
        row_count=len(rows),
    )


def set_model_access(ctx: ToolContext, args: dict) -> ToolResult:
    """Grant or revoke one role's access to one model."""
    grant = bool(args.get("granted", True))
    action = "grant model" if grant else "revoke model"
    role_name = (args.get("role_name") or "").strip().lower()
    model_key = (args.get("model_key") or "").strip()

    role, refusal = _editable_role(ctx, role_name, action)
    if refusal is not None:
        return refusal

    model = get_model(model_key)
    if model is None:
        return ToolResult(
            summary=f"There is no model named '{model_key}'. Available models: {', '.join(ALL_MODEL_KEYS)}.",
            audit_note=f"{action} rejected: unknown model '{model_key}'",
        )

    already = model.key in _models_of(ctx, role.id)
    if grant == already:
        verb = "already has" if already else "does not have"
        return ToolResult(
            summary=f"The '{role.name}' role {verb} access to {model.label}. Nothing changed.",
            data={"role": role.name, "models": _models_of(ctx, role.id)},
            audit_note=f"{action} no-op: '{role.name}' / '{model.key}'",
        )

    if grant:
        ctx.db.execute(insert(role_models).values(role_id=role.id, model_key=model.key))
    else:
        ctx.db.execute(
            delete(role_models).where(
                role_models.c.role_id == role.id, role_models.c.model_key == model.key
            )
        )
    ctx.db.commit()

    remaining = _models_of(ctx, role.id)
    verb = "can now use" if grant else "can no longer use"
    warning = (
        " That was its last model, so this role cannot use the assistant until one is granted."
        if not remaining
        else ""
    )
    return ToolResult(
        summary=(
            f"The '{role.name}' role {verb} {model.label}. It applies from that role's next "
            f"message.{warning}"
        ),
        data={"role": role.name, "model": model.key, "models": remaining},
        row_count=1,
        audit_note=(
            f"{'granted' if grant else 'revoked'} model '{model.key}' "
            f"{'to' if grant else 'from'} '{role.name}'"
        ),
    )


# --------------------------------------------------------------------------- #
# Data access — which fields and which rows
# --------------------------------------------------------------------------- #

def _fields_of(ctx: ToolContext, role_id: int, dataset_key: str) -> set[str]:
    return set(
        ctx.db.scalars(
            select(role_field_access.c.field_key).where(
                role_field_access.c.role_id == role_id,
                role_field_access.c.dataset_key == dataset_key,
            )
        )
    )


def _scope_of(ctx: ToolContext, role_id: int) -> str:
    return normalise_scope(
        ctx.db.scalar(select(role_data_scope.c.scope).where(role_data_scope.c.role_id == role_id))
    )


def get_data_access(ctx: ToolContext, args: dict) -> ToolResult:
    """Per role: the row scope, and per dataset which fields it may see."""
    role_filter = (args.get("role_name") or "").strip().lower()
    dataset_filter = (args.get("dataset") or "").strip().lower()

    rows = []
    for role in ctx.db.scalars(select(Role).order_by(Role.name)):
        if role_filter and role.name != role_filter:
            continue
        permissions = set(_permissions_of(ctx, role.id))
        datasets = []
        for dataset in DATASET_CATALOGUE:
            if dataset_filter and dataset.key != dataset_filter:
                continue
            granted = _fields_of(ctx, role.id, dataset.key)
            has_dataset = dataset.permission in permissions
            # What the role actually gets, not just what is ticked: a column that
            # reconstructs a withheld one is withheld with it, and no column at all
            # comes back when the dataset permission is missing. Reporting per-column
            # visibility for a dataset the role cannot open would read as access it
            # does not have — this is the tool the admin agent answers "what can this
            # role see" from.
            withheld = dataset.effective_withheld(granted) if has_dataset else dataset.field_keys
            datasets.append(
                {
                    "dataset": dataset.key,
                    "has_dataset_access": has_dataset,
                    "required_permission": dataset.permission,
                    "fields_visible": [
                        spec.key for spec in dataset.fields if spec.key not in withheld
                    ],
                    "fields_withheld": [
                        spec.key for spec in dataset.fields if spec.key in withheld
                    ],
                    "fields_granted": sorted(granted),
                }
            )
        rows.append({"role": role.name, "row_scope": _scope_of(ctx, role.id), "datasets": datasets})

    return ToolResult(
        summary=(
            f"Data access for {len(rows)} role(s): row scope plus the fields each role may see. "
            "Withheld fields are stripped from every tool result before the agent sees them."
        ),
        data={"scopes": DATA_SCOPES, "roles": rows},
        row_count=len(rows),
    )


def _join_labels(specs: list) -> str:
    return _join([item.label for item in specs])


def _column_vocabulary() -> str:
    """Every configurable column, grouped by dataset, for the tool descriptions.

    Generated rather than hand-listed: a written-out sample only steers the phrasings
    someone thought of, and any column left off the list falls through to the
    dataset-level tools, which revoke far more than the request asked for.
    """
    return "; ".join(
        f"{dataset.label} — " + ", ".join(f"{spec.label} ({spec.key})" for spec in dataset.configurable_fields)
        for dataset in DATASET_CATALOGUE
    )


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _tools_gated_by(permission_name: str) -> list[str]:
    """Every configurable tool this one permission controls, in catalogue order."""
    return [tool.name for tool in _catalogue() if tool.required_permission == permission_name]


def _withheld_columns(ctx: ToolContext, role_id: int, tool) -> list:
    """Columns of `tool`'s dataset the role does not actually see, in catalogue order.

    Used by the dataset-level grant/revoke tools: holding the dataset permission says
    nothing about the columns, and a super admin reading only "already has access"
    would never learn the difference.
    """
    dataset = get_dataset(tool.dataset) if tool.dataset else None
    if dataset is None:
        return []
    withheld = dataset.effective_withheld(_fields_of(ctx, role_id, dataset.key))
    return [spec for spec in dataset.fields if spec.key in withheld]


def _blocking_fields(dataset, granted: set[str], spec) -> list:
    """The withheld columns that `spec` helps reconstruct, and so is withheld with.

    Empty when `spec` comes back normally — either it is not part of any
    reconstruction, or the column it would rebuild is visible anyway.
    """
    withheld = dataset.effective_withheld(granted)
    if spec.key not in withheld:
        return []
    return [
        item
        for item in dataset.fields
        if item.key in withheld and item.key not in granted and spec.key in item.reconstructed_by
    ]


def set_field_access(ctx: ToolContext, args: dict) -> ToolResult:
    """Grant or revoke one role's access to one field of one dataset."""
    grant = bool(args.get("granted", True))
    action = "grant field" if grant else "revoke field"
    role_name = (args.get("role_name") or "").strip().lower()
    dataset_key = (args.get("dataset") or "").strip().lower()
    field_key = (args.get("field") or "").strip()

    role, refusal = _editable_role(ctx, role_name, action)
    if refusal is not None:
        return refusal

    dataset = get_dataset(dataset_key)
    if dataset is None:
        known = ", ".join(data.key for data in DATASET_CATALOGUE)
        return ToolResult(
            summary=f"There is no dataset named '{dataset_key}'. Known datasets: {known}.",
            audit_note=f"{action} rejected: unknown dataset '{dataset_key}'",
        )

    spec = next((candidate for candidate in dataset.fields if candidate.key == field_key), None)
    if spec is None:
        known = ", ".join(candidate.key for candidate in dataset.fields)
        return ToolResult(
            summary=f"The '{dataset.label}' dataset has no field '{field_key}'. Fields: {known}.",
            audit_note=f"{action} rejected: unknown field '{dataset_key}.{field_key}'",
        )

    if spec.locked:
        return ToolResult(
            summary=(
                f"'{spec.label}' identifies the row and is always returned, so it cannot be "
                "withheld. Remove the dataset from the role instead."
            ),
            audit_note=f"{action} rejected: '{dataset_key}.{field_key}' is locked",
        )

    current = _fields_of(ctx, role.id, dataset.key)
    was_withheld = dataset.effective_withheld(current)
    already = spec.key in current
    if grant == already:
        # "Already sees it" has to mean the role actually gets the column. A field that
        # is ticked but withheld by the reconstruction closure is not seen, and saying
        # otherwise here would send the super admin away believing a grant took effect.
        blocked_by = _blocking_fields(dataset, current, spec)
        if grant and blocked_by:
            return ToolResult(
                summary=(
                    f"The '{role.name}' role already holds {dataset.label} · {spec.label}, but "
                    f"still does not see it: with the other columns it reconstructs "
                    f"{_join_labels(blocked_by)}, which this role may not see. Grant "
                    f"{'that' if len(blocked_by) == 1 else 'those'} back for this one to count."
                ),
                data={"role": role.name, "dataset": dataset.key, "field": spec.key},
                caveat=(
                    f"Nothing changed, and the '{role.name}' role still does not see "
                    f"{dataset.label} · {spec.label}. It was already granted, but it is withheld "
                    f"because together with the other columns it reconstructs "
                    f"{_join_labels(blocked_by)}. Grant "
                    f"{_join_labels(blocked_by)} as well for this one to take effect."
                ),
                audit_note=(
                    f"{action} no-op: '{role.name}' / '{dataset_key}.{field_key}' is granted but "
                    f"withheld by {', '.join(item.key for item in blocked_by)}"
                ),
            )

        verb = "already sees" if already else "already does not see"
        return ToolResult(
            summary=f"The '{role.name}' role {verb} {dataset.label} · {spec.label}. Nothing changed.",
            audit_note=f"{action} no-op: '{role.name}' / '{dataset_key}.{field_key}'",
        )

    if grant:
        ctx.db.execute(
            insert(role_field_access).values(
                role_id=role.id, dataset_key=dataset.key, field_key=spec.key
            )
        )
    else:
        ctx.db.execute(
            delete(role_field_access).where(
                role_field_access.c.role_id == role.id,
                role_field_access.c.dataset_key == dataset.key,
                role_field_access.c.field_key == spec.key,
            )
        )
    ctx.db.commit()

    remaining = _fields_of(ctx, role.id, dataset.key)
    withheld = dataset.effective_withheld(remaining)

    # Columns this change pulled down with it. Diffed against the withheld set from
    # *before* the write: a sibling that was already withheld is not collateral of this
    # revoke, and reporting it as such would credit the change with something it did
    # not do. Saying only what actually moved is the difference between a super admin
    # believing the change took and knowing what it did.
    labels = {item.key: item.label for item in dataset.fields}
    order = [item.key for item in dataset.fields]

    # Describe what the write actually moved, not what the verb implies. The closure
    # runs in both directions: a GRANT can narrow access, because completing a
    # reconstruction set makes a still-ungranted column recoverable and withholds the
    # whole set with it. Granting Bonus to a role holding Base salary and Net pay
    # takes both of those away. Reporting that as "now sees Bonus" would be exactly
    # the kind of confident, wrong confirmation this tool exists to avoid.
    newly_withheld = [key for key in order if key in withheld and key not in was_withheld]
    newly_visible = [key for key in order if key not in withheld and key in was_withheld]

    # The ungranted columns driving the closure — the reason the others went with them.
    drivers = [key for key in order if key in withheld and key not in remaining]

    parts: list[str] = []
    if spec.key in withheld and grant:
        parts.append(
            f"The '{role.name}' role now holds {dataset.label} · {spec.label}, but still does "
            f"not see it."
        )
    else:
        verb = "now sees" if grant else "no longer sees"
        parts.append(f"The '{role.name}' role {verb} {dataset.label} · {spec.label}.")

    collateral = [key for key in newly_withheld if key != spec.key]
    if collateral:
        parts.append(
            f"{_join([labels[key] for key in collateral])} "
            f"{'is' if len(collateral) == 1 else 'are'} withheld too, because together these "
            f"columns reconstruct {_join([labels[key] for key in drivers])} exactly."
        )
    restored = [key for key in newly_visible if key != spec.key]
    if restored:
        parts.append(
            f"{_join([labels[key] for key in restored])} "
            f"{'comes' if len(restored) == 1 else 'come'} back with it."
        )
    if spec.key in withheld and grant and drivers:
        parts.append(
            f"Grant {_join([labels[key] for key in drivers])} as well for this one to count."
        )
    parts.append("It applies from that role's next message.")

    delta = ""
    if newly_withheld:
        delta += f"; now withheld: {', '.join(newly_withheld)}"
    if newly_visible:
        delta += f"; now visible: {', '.join(newly_visible)}"

    return ToolResult(
        summary=" ".join(parts),
        # A grant that narrows access, or one the closure still withholds, must not be
        # summarised as a plain widening.
        caveat=(
            (
                f"This did not widen access as asked: the '{role.name}' role now holds "
                f"{dataset.label} · {spec.label} but still does not see it"
                + (
                    f", and {_join([labels[key] for key in collateral])} "
                    f"{'is' if len(collateral) == 1 else 'are'} now withheld as well"
                    if collateral
                    else ""
                )
                + f". Grant {_join([labels[key] for key in drivers])} too."
            )
            if grant and spec.key in withheld
            else (
                f"This narrowed access as well as widening it: {_join([labels[key] for key in collateral])} "
                f"{'is' if len(collateral) == 1 else 'are'} now withheld from the '{role.name}' role, "
                f"because together these columns reconstruct {_join([labels[key] for key in drivers])}."
            )
            if collateral
            else None
        ),
        data={
            "role": role.name,
            "dataset": dataset.key,
            "field": spec.key,
            "fields": sorted(remaining),
            "fields_withheld": [key for key in order if key in withheld],
        },
        row_count=1,
        audit_note=(
            f"{'granted' if grant else 'revoked'} field '{dataset.key}.{spec.key}' "
            f"{'to' if grant else 'from'} '{role.name}'{delta}"
        ),
    )


def set_data_scope(ctx: ToolContext, args: dict) -> ToolResult:
    """Set how far a role's rows reach: all, department, team or self."""
    role_name = (args.get("role_name") or "").strip().lower()
    requested = (args.get("scope") or "").strip().lower()

    role, refusal = _editable_role(ctx, role_name, "set scope")
    if refusal is not None:
        return refusal

    if requested not in DATA_SCOPES:
        return ToolResult(
            summary=f"'{requested}' is not a row scope. Valid scopes: {', '.join(SCOPE_KEYS)}.",
            audit_note=f"set scope rejected: unknown scope '{requested}'",
        )

    current = _scope_of(ctx, role.id)
    if current == requested:
        return ToolResult(
            summary=f"The '{role.name}' role is already scoped to '{requested}'. Nothing changed.",
            audit_note=f"set scope no-op: '{role.name}' already '{requested}'",
        )

    updated = ctx.db.execute(
        update(role_data_scope)
        .where(role_data_scope.c.role_id == role.id)
        .values(scope=requested)
    )
    if updated.rowcount == 0:  # no row yet for this role
        ctx.db.execute(insert(role_data_scope).values(role_id=role.id, scope=requested))
    ctx.db.commit()

    return ToolResult(
        summary=(
            f"The '{role.name}' role is now scoped to '{requested}' — {DATA_SCOPES[requested]} "
            "It applies from that role's next message."
        ),
        data={"role": role.name, "row_scope": requested, "previous": current},
        row_count=1,
        audit_note=f"row scope for '{role.name}' changed from '{current}' to '{requested}'",
    )


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
        "Give a role access to a WHOLE dataset or tool, by granting that tool's required "
        "permission. Use when the request names a dataset — 'let HR see payroll', 'give the "
        "analyst role access to leave records'.\n"
        "Do NOT use this when the request names one column or figure; use set_field_access "
        "instead. These are the columns, by dataset: " + _column_vocabulary() + ".\n"
        "A role can hold a dataset and still be denied individual columns of it, so granting "
        "the dataset here does nothing for a column request.\n"
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

MODEL_ACCESS_TOOL = Tool(
    name="get_model_access",
    description=(
        "Which language models each role is allowed to run — Claude Opus, Claude Sonnet, Claude "
        "Haiku and Gemini — and which model a role gets by default. Use for questions like 'which "
        "model does the analyst use', 'who can use Opus', or before changing model access."
    ),
    required_permission=PERMISSIONS_MANAGE,
    domain="model access",
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
    handler=get_model_access,
)

SET_MODEL_ACCESS_TOOL = Tool(
    name="set_model_access",
    description=(
        "Allow or stop a role using one language model. Use when asked to give, grant, allow, "
        "remove, revoke or block a role's access to a model — for example 'let the analyst use "
        "Claude Opus' or 'stop HR using Gemini'. A role runs the most capable model it holds, and "
        "a request for a model it does not hold is refused before any model is called."
    ),
    required_permission=PERMISSIONS_WRITE,
    domain="model access",
    mutates=True,
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {"type": "string", "enum": _ROLE_ENUM, "description": "The role to change."},
            "model_key": {
                "type": "string",
                "enum": ALL_MODEL_KEYS,
                "description": "The model to grant or revoke.",
            },
            "granted": {
                "type": "boolean",
                "description": "True to allow the model, false to take it away.",
            },
        },
        "required": ["role_name", "model_key", "granted"],
    },
    handler=set_model_access,
)

DATA_ACCESS_TOOL = Tool(
    name="get_data_access",
    description=(
        "The data-access configuration: for every role, how far its rows reach (all employees, "
        "its own department, its own team, or only itself) and which fields of each dataset it "
        "may see. Use for questions like 'can the analyst see salaries', 'which columns does HR "
        "get', or 'what data can this role reach'."
    ),
    required_permission=PERMISSIONS_MANAGE,
    domain="data access",
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {
                "type": "string",
                "enum": _ROLE_ENUM,
                "description": "Limit the result to a single role.",
            },
            "dataset": {
                "type": "string",
                "enum": [dataset.key for dataset in DATASET_CATALOGUE],
                "description": "Limit the result to a single dataset.",
            },
        },
    },
    handler=get_data_access,
)

SET_FIELD_ACCESS_TOOL = Tool(
    name="set_field_access",
    description=(
        "Show or hide ONE column of one dataset for a role. This is the tool for any request "
        "that names a specific figure rather than a dataset — 'give the supervisor access to "
        "salary', 'hide bonus from HR', 'let the analyst see email addresses', 'stop managers "
        "seeing why someone took leave'.\n"
        "Map the words to a field key: salary / pay / compensation / base pay -> base_salary; "
        "bonus -> bonus; deductions -> deductions; take-home or net pay -> net_pay; email or "
        "contact details -> email; leave reason -> reason; review comments or feedback -> "
        "comments; reviewer -> reviewer.\n"
        "A hidden field is stripped from every result before the agent sees it, along with any "
        "column that would reconstruct it. Granting a dataset with grant_tool_access does NOT "
        "grant its columns, so use this even when the role already has the dataset. To remove a "
        "whole dataset instead, use revoke_tool_access."
    ),
    required_permission=PERMISSIONS_WRITE,
    domain="data access",
    mutates=True,
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {"type": "string", "enum": _ROLE_ENUM, "description": "The role to change."},
            "dataset": {
                "type": "string",
                "enum": [dataset.key for dataset in DATASET_CATALOGUE],
                "description": "The dataset the field belongs to.",
            },
            "field": {
                "type": "string",
                "enum": sorted(
                    {spec.key for dataset in DATASET_CATALOGUE for spec in dataset.configurable_fields}
                ),
                "description": (
                    "The column key. 'Salary', 'pay' and 'compensation' all mean base_salary."
                ),
            },
            "granted": {
                "type": "boolean",
                "description": "True to show the field, false to withhold it.",
            },
        },
        "required": ["role_name", "dataset", "field", "granted"],
    },
    handler=set_field_access,
)

SET_DATA_SCOPE_TOOL = Tool(
    name="set_data_scope",
    description=(
        "Set how far a role's rows reach: 'all' for the whole company, 'department' for its own "
        "department, 'team' for the caller plus their direct reports, or 'self' for the caller's "
        "own record. Use when asked to widen or narrow which employees a role can see — for "
        "example 'limit the analyst to their own department'."
    ),
    required_permission=PERMISSIONS_WRITE,
    domain="data access",
    mutates=True,
    input_schema={
        "type": "object",
        "properties": {
            "role_name": {"type": "string", "enum": _ROLE_ENUM, "description": "The role to change."},
            "scope": {
                "type": "string",
                "enum": SCOPE_KEYS,
                "description": "The row scope to apply.",
            },
        },
        "required": ["role_name", "scope"],
    },
    handler=set_data_scope,
)

REVOKE_TOOL_ACCESS_TOOL = Tool(
    name="revoke_tool_access",
    description=(
        "Remove a role's access to a WHOLE dataset or tool, by revoking that tool's required "
        "permission. Use when the request names a dataset — 'stop the supervisor role seeing "
        "payroll', 'take leave records away from the analyst'.\n"
        "Do NOT use this when the request names one column or figure; use set_field_access with "
        "granted=false instead, which leaves the rest of the dataset readable. These are the "
        "columns, by dataset: " + _column_vocabulary() + ".\n"
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
