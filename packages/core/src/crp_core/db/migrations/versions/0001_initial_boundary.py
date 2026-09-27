"""Initial workspace/project/source/snapshot/scan boundary.

Child tables reference parents through composite (workspace_id, project_id, ...) foreign keys so
cross-project references are impossible at the database level.

Revision ID: 0001
Revises: (none)
Create Date: 2026-09-26 02:12:18.596167
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("subject", name=op.f("uq_users_subject")),
    )
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'",
            name=op.f("ck_workspaces_slug_format"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        sa.UniqueConstraint("slug", name=op.f("uq_workspaces_slug")),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')", name=op.f("ck_memberships_role_valid")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_memberships_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_memberships_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint(
            "workspace_id", "user_id", name=op.f("uq_memberships_workspace_id_user_id")
        ),
    )
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_memberships_workspace_id"), "memberships", ["workspace_id"], unique=False
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin IN ('user', 'synthetic_fixture')", name=op.f("ck_projects_origin_valid")
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'", name=op.f("ck_projects_slug_format")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_projects_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_projects_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_projects_workspace_id_id")),
        sa.UniqueConstraint("workspace_id", "slug", name=op.f("uq_projects_workspace_id_slug")),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('zip_upload', 'local_runner', 'browser_files', 'registered_mount')",
            name=op.f("ck_sources_mode_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name=op.f("fk_sources_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name=op.f("uq_sources_workspace_id_project_id_id")
        ),
    )
    op.create_index(op.f("ix_sources_project_id"), "sources", ["project_id"], unique=False)
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("capture_status", sa.String(length=16), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("manifest_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("git_ref", sa.String(length=255), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "capture_status <> 'FROZEN' OR (manifest_sha256 IS NOT NULL AND frozen_at IS NOT NULL)",
            name=op.f("ck_snapshots_frozen_requires_manifest"),
        ),
        sa.CheckConstraint(
            "capture_status IN ('PENDING', 'CAPTURING', 'FROZEN', 'FAILED', 'INCONSISTENT')",
            name=op.f("ck_snapshots_capture_status_valid"),
        ),
        sa.CheckConstraint(
            "git_commit IS NULL OR git_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name=op.f("ck_snapshots_git_commit_format"),
        ),
        sa.CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_snapshots_manifest_sha256_format"),
        ),
        sa.CheckConstraint(
            "file_count >= 0 AND total_bytes >= 0", name=op.f("ck_snapshots_counts_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "source_id"],
            ["sources.workspace_id", "sources.project_id", "sources.id"],
            name=op.f("fk_snapshots_workspace_id_project_id_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshots")),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name=op.f("uq_snapshots_workspace_id_project_id_id")
        ),
    )
    op.create_index(
        op.f("ix_snapshots_manifest_sha256"), "snapshots", ["manifest_sha256"], unique=False
    )
    op.create_index(op.f("ix_snapshots_project_id"), "snapshots", ["project_id"], unique=False)
    op.create_table(
        "scans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'BLOCKED', 'BUDGET_EXHAUSTED')",
            name=op.f("ck_scans_state_valid"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 128", name=op.f("ck_scans_idempotency_key_len")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_scans_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            name=op.f("fk_scans_workspace_id_project_id_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scans")),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name=op.f("uq_scans_project_id_idempotency_key")
        ),
    )
    op.create_index(op.f("ix_scans_snapshot_id"), "scans", ["snapshot_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_scans_snapshot_id"), table_name="scans")
    op.drop_table("scans")
    op.drop_index(op.f("ix_snapshots_project_id"), table_name="snapshots")
    op.drop_index(op.f("ix_snapshots_manifest_sha256"), table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_index(op.f("ix_sources_project_id"), table_name="sources")
    op.drop_table("sources")
    op.drop_table("projects")
    op.drop_index(op.f("ix_memberships_workspace_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("workspaces")
    op.drop_table("users")
