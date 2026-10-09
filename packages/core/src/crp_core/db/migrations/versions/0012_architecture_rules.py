"""Architecture rules (P10 slice 2): append-only versions of a project's intended architecture,
evaluated on every review (engine ``architecture``).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "architecture_rule_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "document",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_architecture_rule_versions_version_positive")
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_architecture_rule_versions_sha256_format")
        ),
        sa.CheckConstraint(
            "source IN ('editor', 'yaml')",
            name=op.f("ck_architecture_rule_versions_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_architecture_rule_versions_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name=op.f("fk_architecture_rule_versions_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_architecture_rule_versions")),
        sa.UniqueConstraint(
            "project_id",
            "version",
            name=op.f("uq_architecture_rule_versions_project_id_version"),
        ),
    )
    op.create_index(
        op.f("ix_architecture_rule_versions_project_id"),
        "architecture_rule_versions",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_architecture_rule_versions_project_id"), table_name="architecture_rule_versions"
    )
    op.drop_table("architecture_rule_versions")
