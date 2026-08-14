"""Model access, field-level data access and row scope per role.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14

The three tables are back-filled from the seeded baseline in `rbac/model_catalog.py`
and `rbac/datasets.py`, so an existing database comes out of the upgrade configured
rather than locked out. Row scope is back-filled to exactly what the code did before
this revision: `team` for supervisor, `all` for everyone else.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from rbac.datasets import ROLE_SCOPES, SCOPE_ALL, seeded_fields_for
from rbac.model_catalog import ALL_MODEL_KEYS, ROLE_MODELS

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    role_models = op.create_table(
        "role_models",
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("model_key", sa.String(length=40), primary_key=True),
    )

    role_field_access = op.create_table(
        "role_field_access",
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("dataset_key", sa.String(length=40), primary_key=True),
        sa.Column("field_key", sa.String(length=60), primary_key=True),
    )

    role_data_scope = op.create_table(
        "role_data_scope",
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("scope", sa.String(length=20), nullable=False, server_default=SCOPE_ALL),
    )

    bind = op.get_bind()
    roles = list(bind.execute(sa.text("SELECT id, name FROM roles")))
    if not roles:  # a fresh database — seed.py fills these in
        return

    model_rows, field_rows, scope_rows = [], [], []
    for role_id, name in roles:
        # A role this build doesn't know about keeps everything — `seeded_fields_for`
        # withholds nothing for an unlisted role — rather than silently losing access
        # during an upgrade.
        for model_key in ROLE_MODELS.get(name, ALL_MODEL_KEYS):
            model_rows.append({"role_id": role_id, "model_key": model_key})
        for dataset_key, field_key in seeded_fields_for(name):
            field_rows.append(
                {"role_id": role_id, "dataset_key": dataset_key, "field_key": field_key}
            )
        scope_rows.append({"role_id": role_id, "scope": ROLE_SCOPES.get(name, SCOPE_ALL)})

    op.bulk_insert(role_models, model_rows)
    op.bulk_insert(role_field_access, field_rows)
    op.bulk_insert(role_data_scope, scope_rows)


def downgrade() -> None:
    op.drop_table("role_data_scope")
    op.drop_table("role_field_access")
    op.drop_table("role_models")
