"""Change-set pull requests (P08 slice 5): one GitHub pull request per exact workspace content,
opened on request with exact-head freshness (never merged).

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09 00:28:19.946548
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_set_pull_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("change_set_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("repository", sa.String(length=255), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("base_ref", sa.String(length=255), nullable=False),
        sa.Column("base_sha", sa.String(length=64), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "base_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name=op.f("ck_change_set_pull_requests_base_sha_format"),
        ),
        sa.CheckConstraint(
            "commit_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name=op.f("ck_change_set_pull_requests_commit_sha_format"),
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_change_set_pull_requests_content_sha256_format"),
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"],
            ["change_sets.id"],
            name=op.f("fk_change_set_pull_requests_change_set_id_change_sets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_change_set_pull_requests_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_set_pull_requests")),
        sa.UniqueConstraint(
            "change_set_id",
            "content_sha256",
            name=op.f("uq_change_set_pull_requests_change_set_id_content_sha256"),
        ),
    )
    op.create_index(
        op.f("ix_change_set_pull_requests_change_set_id"),
        "change_set_pull_requests",
        ["change_set_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_change_set_pull_requests_change_set_id"), table_name="change_set_pull_requests"
    )
    op.drop_table("change_set_pull_requests")
