"""AI review: per-project source-disclosure policy (audited), runs, usage and findings.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 13:50:14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_policy_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "changes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_ai_policy_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_ai_policy_events_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_policy_events")),
    )
    op.create_index(
        op.f("ix_ai_policy_events_project_id"), "ai_policy_events", ["project_id"], unique=False
    )
    op.create_table(
        "project_ai_policies",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("max_excerpt_lines", sa.Integer(), server_default="120", nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "max_excerpt_lines BETWEEN 10 AND 400",
            name=op.f("ck_project_ai_policies_max_excerpt_lines_range"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=op.f("fk_project_ai_policies_updated_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name=op.f("fk_project_ai_policies_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", name=op.f("pk_project_ai_policies")),
    )
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("finding_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column(
            "target_paths",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "limits",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "usage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "answer",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "steps",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "limitations",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind <> 'finding_review' OR finding_id IS NOT NULL OR state <> 'QUEUED'",
            name=op.f("ck_ai_runs_finding_review_requires_finding"),
        ),
        sa.CheckConstraint(
            "kind <> 'question' OR length(btrim(coalesce(question, ''))) > 0",
            name=op.f("ck_ai_runs_question_requires_text"),
        ),
        sa.CheckConstraint(
            "kind IN ('question', 'finding_review', 'file_review')",
            name=op.f("ck_ai_runs_kind_valid"),
        ),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'BUDGET_EXHAUSTED', 'FAILED', 'CANCELED')",
            name=op.f("ck_ai_runs_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"],
            ["findings.id"],
            name=op.f("fk_ai_runs_finding_id_findings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_ai_runs_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f("fk_ai_runs_scan_id_scans"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            name=op.f("fk_ai_runs_workspace_id_project_id_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_runs")),
    )
    op.create_index(
        "ix_ai_runs_project_id_created_at", "ai_runs", ["project_id", "created_at"], unique=False
    )
    op.create_table(
        "ai_calls",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("usage_reported", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('ok', 'error')", name=op.f("ck_ai_calls_status_valid")),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 AND cache_write_tokens >= 0",
            name=op.f("ck_ai_calls_tokens_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["ai_runs.id"], name=op.f("fk_ai_calls_run_id_ai_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_calls")),
    )
    op.create_index("ix_ai_calls_created_at", "ai_calls", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_calls_run_id"), "ai_calls", ["run_id"], unique=False)
    op.create_table(
        "ai_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("severity_rationale", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column("evidence_class", sa.String(length=24), nullable=False),
        sa.Column(
            "anchors",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("triggering_conditions", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column("validation_needed", sa.Text(), nullable=True),
        sa.Column("uncertainty", sa.Text(), nullable=True),
        sa.Column("related_finding_id", sa.Uuid(), nullable=True),
        sa.Column(
            "verification",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('correctness', 'security', 'performance', 'maintainability', 'coding_standards', 'reliability', 'dependencies')",
            name=op.f("ck_ai_findings_category_valid"),
        ),
        sa.CheckConstraint(
            "confidence IN ('low', 'medium', 'high')", name=op.f("ck_ai_findings_confidence_valid")
        ),
        sa.CheckConstraint(
            "evidence_class IN ('verified_anchor', 'hypothesis', 'rejected')",
            name=op.f("ck_ai_findings_evidence_class_valid"),
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name=op.f("ck_ai_findings_severity_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["related_finding_id"],
            ["findings.id"],
            name=op.f("fk_ai_findings_related_finding_id_findings"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ai_runs.id"],
            name=op.f("fk_ai_findings_run_id_ai_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_findings")),
    )
    op.create_index(op.f("ix_ai_findings_run_id"), "ai_findings", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_findings_run_id"), table_name="ai_findings")
    op.drop_table("ai_findings")
    op.drop_index(op.f("ix_ai_calls_run_id"), table_name="ai_calls")
    op.drop_index("ix_ai_calls_created_at", table_name="ai_calls")
    op.drop_table("ai_calls")
    op.drop_index("ix_ai_runs_project_id_created_at", table_name="ai_runs")
    op.drop_table("ai_runs")
    op.drop_table("project_ai_policies")
    op.drop_index(op.f("ix_ai_policy_events_project_id"), table_name="ai_policy_events")
    op.drop_table("ai_policy_events")
