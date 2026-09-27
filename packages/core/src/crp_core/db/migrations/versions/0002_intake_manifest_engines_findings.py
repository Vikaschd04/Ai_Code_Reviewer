"""Intakes, snapshot manifests, structural symbols, engine runs, coverage, findings, scan events.

Adds manifest/inventory columns to snapshots and workflow/summary columns to scans.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 11:24:05.595384
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intakes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("archive_key", sa.String(length=512), nullable=True),
        sa.Column("archive_sha256", sa.String(length=64), nullable=True),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=True),
        sa.Column("client_manifest_key", sa.String(length=512), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "error_details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "archive_sha256 IS NULL OR archive_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_intakes_archive_sha256_format"),
        ),
        sa.CheckConstraint(
            "mode IN ('zip_upload', 'local_runner', 'browser_files', 'registered_mount')",
            name=op.f("ck_intakes_mode_valid"),
        ),
        sa.CheckConstraint(
            "state <> 'READY' OR snapshot_id IS NOT NULL",
            name=op.f("ck_intakes_ready_requires_snapshot"),
        ),
        sa.CheckConstraint(
            "state IN ('CREATED', 'UPLOADING', 'VALIDATING', 'READY', 'REJECTED', 'FAILED', 'CANCELED')",
            name=op.f("ck_intakes_state_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_intakes_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "source_id"],
            ["sources.workspace_id", "sources.project_id", "sources.id"],
            name=op.f("fk_intakes_workspace_id_project_id_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_intakes")),
        sa.UniqueConstraint(
            "workspace_id", "project_id", "id", name=op.f("uq_intakes_workspace_id_project_id_id")
        ),
    )
    op.create_index(op.f("ix_intakes_project_id"), "intakes", ["project_id"], unique=False)
    op.create_table(
        "file_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("disposition", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("blob_sha256", sa.String(length=64), nullable=True),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=True),
        sa.Column("parse_status", sa.String(length=16), nullable=True),
        sa.Column("parse_error_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "blob_sha256 IS NULL OR blob_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_file_entries_blob_sha256_format"),
        ),
        sa.CheckConstraint(
            "disposition <> 'ANALYZABLE' OR blob_sha256 IS NOT NULL",
            name=op.f("ck_file_entries_analyzable_has_blob"),
        ),
        sa.CheckConstraint(
            "disposition IN ('ANALYZABLE', 'BINARY', 'OVERSIZED', 'EXCLUDED')",
            name=op.f("ck_file_entries_disposition_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            name=op.f("fk_file_entries_workspace_id_project_id_snapshot_id_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_entries")),
        sa.UniqueConstraint("snapshot_id", "path", name=op.f("uq_file_entries_snapshot_id_path")),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "snapshot_id",
            "id",
            name=op.f("uq_file_entries_workspace_id_project_id_snapshot_id_id"),
        ),
    )
    op.create_table(
        "code_symbols",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("file_entry_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("container", sa.String(length=512), nullable=True),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("start_column", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("end_column", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "start_line >= 1 AND end_line >= start_line", name=op.f("ck_code_symbols_span_valid")
        ),
        sa.ForeignKeyConstraint(
            ["file_entry_id"],
            ["file_entries.id"],
            name=op.f("fk_code_symbols_file_entry_id_file_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_code_symbols")),
    )
    op.create_index(
        op.f("ix_code_symbols_file_entry_id"), "code_symbols", ["file_entry_id"], unique=False
    )
    op.create_index(
        op.f("ix_code_symbols_snapshot_id"), "code_symbols", ["snapshot_id"], unique=False
    )
    op.create_table(
        "engine_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=128), nullable=True),
        sa.Column("ruleset_id", sa.String(length=64), nullable=True),
        sa.Column("ruleset_sha256", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("files_eligible", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_attempted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_succeeded", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("findings_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("raw_artifact_key", sa.String(length=512), nullable=True),
        sa.Column("raw_artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "diagnostics",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'UNAVAILABLE', 'NOT_APPLICABLE')",
            name=op.f("ck_engine_runs_state_valid"),
        ),
        sa.CheckConstraint(
            "files_attempted <= files_eligible AND files_succeeded + files_failed <= files_attempted",
            name=op.f("ck_engine_runs_coverage_counts_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f("fk_engine_runs_scan_id_scans"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_engine_runs")),
        sa.UniqueConstraint("scan_id", "engine", name=op.f("uq_engine_runs_scan_id_engine")),
    )
    op.create_index(op.f("ix_engine_runs_scan_id"), "engine_runs", ["scan_id"], unique=False)
    op.create_table(
        "scan_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "payload",
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
            ["scan_id"], ["scans.id"], name=op.f("fk_scan_events_scan_id_scans"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scan_events")),
    )
    op.create_index(op.f("ix_scan_events_scan_id"), "scan_events", ["scan_id"], unique=False)
    op.create_table(
        "file_coverage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("engine_run_id", sa.Uuid(), nullable=False),
        sa.Column("file_entry_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('ANALYZED', 'FAILED', 'NOT_ATTEMPTED')",
            name=op.f("ck_file_coverage_outcome_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["engine_run_id"],
            ["engine_runs.id"],
            name=op.f("fk_file_coverage_engine_run_id_engine_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_entry_id"],
            ["file_entries.id"],
            name=op.f("fk_file_coverage_file_entry_id_file_entries"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_coverage")),
        sa.UniqueConstraint(
            "engine_run_id",
            "file_entry_id",
            name=op.f("uq_file_coverage_engine_run_id_file_entry_id"),
        ),
    )
    op.create_index(
        op.f("ix_file_coverage_engine_run_id"), "file_coverage", ["engine_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_file_coverage_file_entry_id"), "file_coverage", ["file_entry_id"], unique=False
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("scan_id", sa.Uuid(), nullable=False),
        sa.Column("engine_run_id", sa.Uuid(), nullable=False),
        sa.Column("file_entry_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=128), nullable=False),
        sa.Column("rule_id", sa.String(length=128), nullable=False),
        sa.Column("ruleset", sa.String(length=128), nullable=True),
        sa.Column("engine_severity", sa.String(length=32), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("start_column", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("end_column", sa.Integer(), nullable=True),
        sa.Column("rule_url", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("in_catalog", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category IN ('correctness', 'security', 'performance', 'maintainability', 'coding_standards', 'reliability')",
            name=op.f("ck_findings_category_valid"),
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_findings_fingerprint_format")
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name=op.f("ck_findings_severity_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('OPEN', 'TRIAGED', 'ACCEPTED_RISK', 'FALSE_POSITIVE', 'FIX_PROPOSED', 'RESOLVED')",
            name=op.f("ck_findings_status_valid"),
        ),
        sa.CheckConstraint(
            "start_line >= 1 AND end_line >= start_line", name=op.f("ck_findings_span_valid")
        ),
        sa.ForeignKeyConstraint(
            ["engine_run_id"],
            ["engine_runs.id"],
            name=op.f("fk_findings_engine_run_id_engine_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["file_entry_id"],
            ["file_entries.id"],
            name=op.f("fk_findings_file_entry_id_file_entries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"], ["scans.id"], name=op.f("fk_findings_scan_id_scans"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint("scan_id", "fingerprint", name=op.f("uq_findings_scan_id_fingerprint")),
    )
    op.create_index(op.f("ix_findings_file_entry_id"), "findings", ["file_entry_id"], unique=False)
    op.create_index(op.f("ix_findings_project_id"), "findings", ["project_id"], unique=False)
    op.create_index(op.f("ix_findings_scan_id"), "findings", ["scan_id"], unique=False)
    op.add_column("scans", sa.Column("workflow_id", sa.String(length=128), nullable=True))
    op.add_column(
        "scans", sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("scans", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.add_column("scans", sa.Column("error_message", sa.Text(), nullable=True))
    op.add_column(
        "scans",
        sa.Column(
            "summary",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column("snapshots", sa.Column("intake_id", sa.Uuid(), nullable=True))
    op.add_column("snapshots", sa.Column("manifest_key", sa.String(length=512), nullable=True))
    op.add_column("snapshots", sa.Column("policy_version", sa.String(length=64), nullable=True))
    op.add_column(
        "snapshots", sa.Column("analyzable_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "snapshots", sa.Column("excluded_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "snapshots",
        sa.Column(
            "inventory",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(op.f("uq_snapshots_intake_id"), "snapshots", ["intake_id"])


def downgrade() -> None:
    op.drop_constraint(op.f("uq_snapshots_intake_id"), "snapshots", type_="unique")
    op.drop_column("snapshots", "inventory")
    op.drop_column("snapshots", "excluded_count")
    op.drop_column("snapshots", "analyzable_count")
    op.drop_column("snapshots", "policy_version")
    op.drop_column("snapshots", "manifest_key")
    op.drop_column("snapshots", "intake_id")
    op.drop_column("scans", "summary")
    op.drop_column("scans", "error_message")
    op.drop_column("scans", "error_code")
    op.drop_column("scans", "cancel_requested_at")
    op.drop_column("scans", "workflow_id")
    op.drop_index(op.f("ix_findings_scan_id"), table_name="findings")
    op.drop_index(op.f("ix_findings_project_id"), table_name="findings")
    op.drop_index(op.f("ix_findings_file_entry_id"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(op.f("ix_file_coverage_file_entry_id"), table_name="file_coverage")
    op.drop_index(op.f("ix_file_coverage_engine_run_id"), table_name="file_coverage")
    op.drop_table("file_coverage")
    op.drop_index(op.f("ix_scan_events_scan_id"), table_name="scan_events")
    op.drop_table("scan_events")
    op.drop_index(op.f("ix_engine_runs_scan_id"), table_name="engine_runs")
    op.drop_table("engine_runs")
    op.drop_index(op.f("ix_code_symbols_snapshot_id"), table_name="code_symbols")
    op.drop_index(op.f("ix_code_symbols_file_entry_id"), table_name="code_symbols")
    op.drop_table("code_symbols")
    op.drop_table("file_entries")
    op.drop_index(op.f("ix_intakes_project_id"), table_name="intakes")
    op.drop_table("intakes")
