"""Projects' own AI models and keys (ADR 0026): a project admin chooses a provider and model and
adds their own API key, tested before it is saved and stored encrypted (never the plain key).
Project AI policies and AI runs accept the connection ``project``.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-14 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = "connection IN ('primary', 'alt')"
_AFTER = "connection IN ('primary', 'alt', 'project')"


def _connections(check: str) -> None:
    for table in ("project_ai_policies", "ai_runs"):
        op.drop_constraint(op.f(f"ck_{table}_connection_valid"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_connection_valid"), table, check)


def upgrade() -> None:
    op.create_table(
        "project_ai_models",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("key_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=8), nullable=False),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'unorouter')",
            name=op.f("ck_project_ai_models_provider_valid"),
        ),
        sa.CheckConstraint(
            "key_ciphertext LIKE 'v1.%'", name=op.f("ck_project_ai_models_key_encrypted")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_project_ai_models_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name=op.f("fk_project_ai_models_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_ai_models")),
    )
    _connections(_AFTER)


def downgrade() -> None:
    op.execute("UPDATE project_ai_policies SET connection = 'primary' WHERE connection = 'project'")
    op.execute("UPDATE ai_runs SET connection = 'primary' WHERE connection = 'project'")
    _connections(_BEFORE)
    op.drop_table("project_ai_models")
