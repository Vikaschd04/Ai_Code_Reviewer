"""Issues and audit events, snapshot graph builds, per-file engine cache, finding anchors.

Findings gain nullable lines (dependency/file anchors), a correlation key (backfilled from the
fingerprint for existing rows), rule family, details, guidance and an issue link. Engine runs gain
enabled rules, configuration fingerprint and cache counters; scans gain cache mode and the
lifecycle-applied flag. CHECK constraints are replaced explicitly (autogenerate cannot see them).

Downgrade removes findings that cannot satisfy the 0002 constraints (no line, or category
``dependencies``) before restoring them.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 13:03:15.634223
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "engine_cache",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=128), nullable=False),
        sa.Column("ruleset_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("hit_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cache_key ~ '^[0-9a-f]{64}$'", name=op.f("ck_engine_cache_cache_key_format")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name=op.f("fk_engine_cache_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engine_cache")),
        sa.UniqueConstraint(
            "project_id", "cache_key", name=op.f("uq_engine_cache_project_id_cache_key")
        ),
    )
    op.create_table(
        "graph_builds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("extractor", sa.String(length=256), nullable=False),
        sa.Column("node_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("edge_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unresolved_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_parsed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "diagnostics",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('SUCCEEDED', 'PARTIAL', 'FAILED')", name=op.f("ck_graph_builds_state_valid")
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name=op.f("fk_graph_builds_scan_id_scans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            name=op.f("fk_graph_builds_workspace_id_project_id_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_builds")),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "snapshot_id",
            "id",
            name=op.f("uq_graph_builds_workspace_id_project_id_snapshot_id_id"),
        ),
    )
    op.create_index(
        op.f("ix_graph_builds_snapshot_id"), "graph_builds", ["snapshot_id"], unique=False
    )
    op.create_index(
        "uq_graph_builds_current_snapshot",
        "graph_builds",
        ["snapshot_id"],
        unique=True,
        postgresql_where="is_current",
    )
    op.create_table(
        "issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("recheck_state", sa.String(length=24), nullable=False),
        sa.Column("recheck_reason", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("exception_reason", sa.Text(), nullable=True),
        sa.Column("exception_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_scan_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_scan_id", sa.Uuid(), nullable=True),
        sa.Column("last_evaluated_scan_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_engine_version", sa.String(length=128), nullable=True),
        sa.Column("last_seen_ruleset_sha256", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
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
            "fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_issues_fingerprint_format")
        ),
        sa.CheckConstraint(
            "recheck_state IN ('VERIFIED_PRESENT', 'VERIFIED_ABSENT', 'NOT_RECHECKED', 'UNKNOWN', 'RULE_OBSOLETE')",
            name=op.f("ck_issues_recheck_state_valid"),
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name=op.f("ck_issues_severity_valid"),
        ),
        sa.CheckConstraint(
            "status <> 'ACCEPTED_RISK' OR exception_expires_at IS NOT NULL",
            name=op.f("ck_issues_accepted_risk_requires_expiry"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'TRIAGED', 'ACCEPTED_RISK', 'FALSE_POSITIVE', 'FIX_PROPOSED', 'RESOLVED')",
            name=op.f("ck_issues_status_valid"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('ACCEPTED_RISK', 'FALSE_POSITIVE') OR length(btrim(coalesce(exception_reason, ''))) > 0",
            name=op.f("ck_issues_exception_requires_reason"),
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_scan_id"],
            ["scans.id"],
            name=op.f("fk_issues_first_seen_scan_id_scans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_evaluated_scan_id"],
            ["scans.id"],
            name=op.f("fk_issues_last_evaluated_scan_id_scans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_scan_id"],
            ["scans.id"],
            name=op.f("fk_issues_last_seen_scan_id_scans"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name=op.f("fk_issues_workspace_id_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issues")),
        sa.UniqueConstraint(
            "project_id", "fingerprint", name=op.f("uq_issues_project_id_fingerprint")
        ),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name=op.f("uq_issues_workspace_id_project_id_id")
        ),
    )
    op.create_index("ix_issues_project_id_status", "issues", ["project_id", "status"], unique=False)
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("build_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=1024), nullable=False),
        sa.Column("label", sa.String(length=512), nullable=False),
        sa.Column("module_key", sa.String(length=1024), nullable=True),
        sa.Column("file_entry_id", sa.Uuid(), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column(
            "attributes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "kind IN ('module', 'file', 'package', 'type', 'function', 'external')",
            name=op.f("ck_graph_nodes_kind_valid"),
        ),
        sa.CheckConstraint(
            "start_line IS NULL OR (end_line IS NOT NULL AND start_line >= 1 AND end_line >= start_line)",
            name=op.f("ck_graph_nodes_span_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id", "build_id"],
            [
                "graph_builds.workspace_id",
                "graph_builds.project_id",
                "graph_builds.snapshot_id",
                "graph_builds.id",
            ],
            name=op.f("fk_graph_nodes_workspace_id_project_id_snapshot_id_build_id_graph_builds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id", "file_entry_id"],
            [
                "file_entries.workspace_id",
                "file_entries.project_id",
                "file_entries.snapshot_id",
                "file_entries.id",
            ],
            name=op.f(
                "fk_graph_nodes_workspace_id_project_id_snapshot_id_file_entry_id_file_entries"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_nodes")),
        sa.UniqueConstraint("build_id", "id", name=op.f("uq_graph_nodes_build_id_id")),
        sa.UniqueConstraint("build_id", "key", name=op.f("uq_graph_nodes_build_id_key")),
    )
    op.create_index(
        "ix_graph_nodes_build_id_kind", "graph_nodes", ["build_id", "kind"], unique=False
    )
    op.create_index(
        "ix_graph_nodes_build_id_file_entry_id",
        "graph_nodes",
        ["build_id", "file_entry_id"],
        unique=False,
    )
    op.create_table(
        "issue_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=True),
        sa.Column("actor_kind", sa.String(length=8), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "changes",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'system')", name=op.f("ck_issue_events_actor_kind_valid")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_issue_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issues.id"],
            name=op.f("fk_issue_events_issue_id_issues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name=op.f("fk_issue_events_scan_id_scans"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issue_events")),
    )
    op.create_index(op.f("ix_issue_events_issue_id"), "issue_events", ["issue_id"], unique=False)
    op.create_table(
        "graph_edges",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("build_id", sa.Uuid(), nullable=False),
        sa.Column("source_node_id", sa.BigInteger(), nullable=False),
        sa.Column("target_node_id", sa.BigInteger(), nullable=True),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("target_ref", sa.String(length=1024), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_file_entry_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_start_line", sa.Integer(), nullable=True),
        sa.Column("evidence_end_line", sa.Integer(), nullable=True),
        sa.Column("evidence_text", sa.String(length=500), nullable=True),
        sa.Column("extractor", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "classification <> 'resolved' OR target_node_id IS NOT NULL",
            name=op.f("ck_graph_edges_resolved_has_target"),
        ),
        sa.CheckConstraint(
            "classification IN ('declared', 'resolved', 'inferred', 'unresolved')",
            name=op.f("ck_graph_edges_classification_valid"),
        ),
        sa.CheckConstraint(
            "evidence_start_line IS NULL OR (evidence_end_line IS NOT NULL "
            "AND evidence_start_line >= 1 AND evidence_end_line >= evidence_start_line)",
            name=op.f("ck_graph_edges_evidence_span_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["build_id", "source_node_id"],
            ["graph_nodes.build_id", "graph_nodes.id"],
            name=op.f("fk_graph_edges_build_id_source_node_id_graph_nodes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["build_id", "target_node_id"],
            ["graph_nodes.build_id", "graph_nodes.id"],
            name=op.f("fk_graph_edges_build_id_target_node_id_graph_nodes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id", "evidence_file_entry_id"],
            [
                "file_entries.workspace_id",
                "file_entries.project_id",
                "file_entries.snapshot_id",
                "file_entries.id",
            ],
            name=op.f(
                "fk_graph_edges_workspace_id_project_id_snapshot_id_evidence_file_entry_id_file_entries"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_graph_edges")),
    )
    op.create_index(
        "ix_graph_edges_build_id_source_node_id",
        "graph_edges",
        ["build_id", "source_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_graph_edges_build_id_target_node_id",
        "graph_edges",
        ["build_id", "target_node_id"],
        unique=False,
    )
    op.add_column(
        "engine_runs",
        sa.Column(
            "enabled_rules",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "engine_runs", sa.Column("config_fingerprint", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "engine_runs", sa.Column("cache_hits", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "engine_runs", sa.Column("cache_misses", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "file_coverage", sa.Column("cached", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column(
        "findings",
        sa.Column(
            "anchor_kind", sa.String(length=16), server_default="source_span", nullable=False
        ),
    )
    op.add_column("findings", sa.Column("correlation_key", sa.String(length=64), nullable=True))
    op.execute("UPDATE findings SET correlation_key = fingerprint WHERE correlation_key IS NULL")
    op.alter_column(
        "findings", "correlation_key", existing_type=sa.String(length=64), nullable=False
    )
    op.add_column("findings", sa.Column("rule_family", sa.String(length=128), nullable=True))
    op.add_column(
        "findings",
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "findings",
        sa.Column(
            "guidance",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column("findings", sa.Column("issue_id", sa.Uuid(), nullable=True))
    op.alter_column("findings", "start_line", existing_type=sa.INTEGER(), nullable=True)
    op.alter_column("findings", "end_line", existing_type=sa.INTEGER(), nullable=True)
    op.create_index(op.f("ix_findings_issue_id"), "findings", ["issue_id"], unique=False)
    op.create_index(
        "ix_findings_scan_id_correlation_key",
        "findings",
        ["scan_id", "correlation_key"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_findings_issue_id_issues"),
        "findings",
        "issues",
        ["issue_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "scans", sa.Column("cache_mode", sa.String(length=16), server_default="use", nullable=False)
    )
    op.add_column(
        "scans",
        sa.Column("lifecycle_applied", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_scans_cache_mode_valid"), "scans", "cache_mode IN ('use', 'refresh')"
    )
    op.drop_constraint(op.f("ck_findings_category_valid"), "findings", type_="check")
    op.create_check_constraint(
        op.f("ck_findings_category_valid"),
        "findings",
        "category IN ('correctness', 'security', 'performance', 'maintainability', "
        "'coding_standards', 'reliability', 'dependencies')",
    )
    op.drop_constraint(op.f("ck_findings_span_valid"), "findings", type_="check")
    op.create_check_constraint(
        op.f("ck_findings_span_valid"),
        "findings",
        "(start_line IS NULL AND end_line IS NULL AND anchor_kind <> 'source_span') "
        "OR (start_line IS NOT NULL AND end_line IS NOT NULL "
        "AND start_line >= 1 AND end_line >= start_line)",
    )
    op.create_check_constraint(
        op.f("ck_findings_anchor_kind_valid"),
        "findings",
        "anchor_kind IN ('source_span', 'file', 'dependency')",
    )
    op.create_check_constraint(
        op.f("ck_findings_correlation_key_format"),
        "findings",
        "correlation_key ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.execute("DELETE FROM findings WHERE start_line IS NULL OR category = 'dependencies'")
    op.drop_constraint(op.f("ck_findings_correlation_key_format"), "findings", type_="check")
    op.drop_constraint(op.f("ck_findings_anchor_kind_valid"), "findings", type_="check")
    op.drop_constraint(op.f("ck_findings_span_valid"), "findings", type_="check")
    op.create_check_constraint(
        op.f("ck_findings_span_valid"), "findings", "start_line >= 1 AND end_line >= start_line"
    )
    op.drop_constraint(op.f("ck_findings_category_valid"), "findings", type_="check")
    op.create_check_constraint(
        op.f("ck_findings_category_valid"),
        "findings",
        "category IN ('correctness', 'security', 'performance', 'maintainability', "
        "'coding_standards', 'reliability')",
    )
    op.drop_constraint(op.f("ck_scans_cache_mode_valid"), "scans", type_="check")
    op.drop_column("scans", "lifecycle_applied")
    op.drop_column("scans", "cache_mode")
    op.drop_constraint(op.f("fk_findings_issue_id_issues"), "findings", type_="foreignkey")
    op.drop_index("ix_findings_scan_id_correlation_key", table_name="findings")
    op.drop_index(op.f("ix_findings_issue_id"), table_name="findings")
    op.alter_column("findings", "end_line", existing_type=sa.INTEGER(), nullable=False)
    op.alter_column("findings", "start_line", existing_type=sa.INTEGER(), nullable=False)
    op.drop_column("findings", "issue_id")
    op.drop_column("findings", "guidance")
    op.drop_column("findings", "details")
    op.drop_column("findings", "rule_family")
    op.drop_column("findings", "correlation_key")
    op.drop_column("findings", "anchor_kind")
    op.drop_column("file_coverage", "cached")
    op.drop_column("engine_runs", "cache_misses")
    op.drop_column("engine_runs", "cache_hits")
    op.drop_column("engine_runs", "config_fingerprint")
    op.drop_column("engine_runs", "enabled_rules")
    op.drop_index("ix_graph_edges_build_id_target_node_id", table_name="graph_edges")
    op.drop_index("ix_graph_edges_build_id_source_node_id", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index(op.f("ix_issue_events_issue_id"), table_name="issue_events")
    op.drop_table("issue_events")
    op.drop_index("ix_graph_nodes_build_id_file_entry_id", table_name="graph_nodes")
    op.drop_index("ix_graph_nodes_build_id_kind", table_name="graph_nodes")
    op.drop_table("graph_nodes")
    op.drop_index("ix_issues_project_id_status", table_name="issues")
    op.drop_table("issues")
    op.drop_index(
        "uq_graph_builds_current_snapshot", table_name="graph_builds", postgresql_where="is_current"
    )
    op.drop_index(op.f("ix_graph_builds_snapshot_id"), table_name="graph_builds")
    op.drop_table("graph_builds")
    op.drop_table("engine_cache")
