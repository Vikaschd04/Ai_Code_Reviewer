"""SQLAlchemy models for the initial workspace/project/source/snapshot/scan boundary.

Scope compatibility is enforced by the database: child rows reference their parent through
composite foreign keys that include ``workspace_id`` and ``project_id``, so a scan can never
point at a snapshot from another project even if application code passes a wrong identifier.
Alembic migrations own the schema; ``alembic check`` in the test suite keeps them in sync.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from crp_core.domain.states import (
    AiConfidence,
    AiEvidenceClass,
    AiRunKind,
    AiRunState,
    AnchorKind,
    CacheMode,
    CaptureStatus,
    ChangeAction,
    ChangeSetCheckState,
    ChangeSource,
    CheckFailThreshold,
    CodeReviewKind,
    CodeReviewState,
    CodeReviewTrigger,
    CoverageOutcome,
    EdgeClassification,
    EngineState,
    FileDisposition,
    FindingCategory,
    FindingStatus,
    FixKind,
    FixProposalState,
    FixValidationState,
    GitProvider,
    GraphBuildState,
    GraphNodeKind,
    IntakeState,
    MembershipRole,
    ProjectOrigin,
    PublishState,
    RecheckState,
    ScanState,
    Severity,
    SourceMode,
)

JsonDocument = JSON().with_variant(JSONB(), "postgresql")

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SLUG_PATTERN = "^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


def enum_check(column: str, enum_type: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum_type)
    return f"{column} IN ({values})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"
    __table_args__ = (CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint(enum_check("role", MembershipRole), name="role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug"),
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint(enum_check("origin", ProjectOrigin), name="origin_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class Source(TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "project_id", "id"),
        CheckConstraint(enum_check("mode", SourceMode), name="mode_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)


class Snapshot(TimestampMixin, Base):
    """Immutable, content-identified source snapshot. Git metadata is optional."""

    __tablename__ = "snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "source_id"],
            ["sources.workspace_id", "sources.project_id", "sources.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "project_id", "id"),
        CheckConstraint(enum_check("capture_status", CaptureStatus), name="capture_status_valid"),
        CheckConstraint(
            "manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="manifest_sha256_format",
        ),
        CheckConstraint(
            "capture_status <> 'FROZEN' OR (manifest_sha256 IS NOT NULL AND frozen_at IS NOT NULL)",
            name="frozen_requires_manifest",
        ),
        CheckConstraint(
            "git_commit IS NULL OR git_commit ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name="git_commit_format",
        ),
        CheckConstraint(
            "git_tree_sha IS NULL OR git_tree_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'",
            name="git_tree_sha_format",
        ),
        CheckConstraint("file_count >= 0 AND total_bytes >= 0", name="counts_non_negative"),
        Index("ix_snapshots_project_id_git_commit", "project_id", "git_commit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    capture_status: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    manifest_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    git_commit: Mapped[str | None] = mapped_column(String(64))
    git_ref: Mapped[str | None] = mapped_column(String(255))
    # Commit captures (P06): provider, repository name at capture time, the commit's tree and a
    # summary of how the capture was checked against that tree (files fetched individually,
    # symlinks, submodules, Git LFS pointers, file modes that differ from 100644).
    git_provider: Mapped[str | None] = mapped_column(String(16))
    git_repository: Mapped[str | None] = mapped_column(String(255))
    git_tree_sha: Mapped[str | None] = mapped_column(String(64))
    git_capture: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    # Fix workspaces (P08): a snapshot derived from ``derived_from`` by a change set's edits. Such
    # snapshots are not uploads: lists, overviews and issue lifecycle ignore them.
    derived_from: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    change_set_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    intake_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, unique=True)
    manifest_key: Mapped[str | None] = mapped_column(String(512))
    policy_version: Mapped[str | None] = mapped_column(String(64))
    analyzable_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    inventory: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)


class Scan(TimestampMixin, Base):
    __tablename__ = "scans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint(enum_check("state", ScanState), name="state_valid"),
        CheckConstraint("length(idempotency_key) BETWEEN 8 AND 128", name="idempotency_key_len"),
        CheckConstraint(enum_check("cache_mode", CacheMode), name="cache_mode_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="use")
    # True once this scan's results were applied to the project's issue lifecycle (only scans of
    # the newest snapshot that completed at least one engine apply it).
    lifecycle_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class Intake(TimestampMixin, Base):
    """One upload/capture attempt. Raw archive bytes are transient; the snapshot is the product."""

    __tablename__ = "intakes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "source_id"],
            ["sources.workspace_id", "sources.project_id", "sources.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "project_id", "id"),
        CheckConstraint(enum_check("state", IntakeState), name="state_valid"),
        CheckConstraint(enum_check("mode", SourceMode), name="mode_valid"),
        CheckConstraint(
            "archive_sha256 IS NULL OR archive_sha256 ~ '^[0-9a-f]{64}$'",
            name="archive_sha256_format",
        ),
        CheckConstraint(
            "state <> 'READY' OR snapshot_id IS NOT NULL", name="ready_requires_snapshot"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    archive_key: Mapped[str | None] = mapped_column(String(512))
    archive_sha256: Mapped[str | None] = mapped_column(String(64))
    archive_bytes: Mapped[int | None] = mapped_column(BigInteger)
    client_manifest_key: Mapped[str | None] = mapped_column(String(512))
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FileEntry(Base):
    """One manifest entry of a frozen snapshot: safe relative path, hash, size and disposition."""

    __tablename__ = "file_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("snapshot_id", "path"),
        UniqueConstraint("workspace_id", "project_id", "snapshot_id", "id"),
        CheckConstraint(enum_check("disposition", FileDisposition), name="disposition_valid"),
        CheckConstraint(
            "blob_sha256 IS NULL OR blob_sha256 ~ '^[0-9a-f]{64}$'", name="blob_sha256_format"
        ),
        CheckConstraint(
            "disposition <> 'ANALYZABLE' OR blob_sha256 IS NOT NULL", name="analyzable_has_blob"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    blob_sha256: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(32))
    line_count: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[str | None] = mapped_column(String(16))
    parse_error_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class CodeSymbol(Base):
    """Structural (syntax-level) symbol extracted by Tree-sitter; not semantic resolution."""

    __tablename__ = "code_symbols"
    __table_args__ = (
        ForeignKeyConstraint(["file_entry_id"], ["file_entries.id"], ondelete="CASCADE"),
        CheckConstraint("start_line >= 1 AND end_line >= start_line", name="span_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    file_entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    container: Mapped[str | None] = mapped_column(String(512))
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    start_column: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_column: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)


class EngineRun(Base):
    __tablename__ = "engine_runs"
    __table_args__ = (
        ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
        UniqueConstraint("scan_id", "engine"),
        CheckConstraint(enum_check("state", EngineState), name="state_valid"),
        CheckConstraint(
            "files_attempted <= files_eligible "
            "AND files_succeeded + files_failed <= files_attempted",
            name="coverage_counts_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(128))
    ruleset_id: Mapped[str | None] = mapped_column(String(64))
    ruleset_sha256: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    files_eligible: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_attempted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    findings_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    raw_artifact_key: Mapped[str | None] = mapped_column(String(512))
    raw_artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    diagnostics: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    # Rule identifiers enabled for this run; NULL when the rule set is open-ended (e.g. a
    # vulnerability database), in which case absence cannot be attributed to a removed rule.
    enabled_rules: Mapped[list[str] | None] = mapped_column(JsonDocument)
    config_fingerprint: Mapped[str | None] = mapped_column(String(64))
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_misses: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FileCoverage(Base):
    __tablename__ = "file_coverage"
    __table_args__ = (
        ForeignKeyConstraint(["engine_run_id"], ["engine_runs.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["file_entry_id"], ["file_entries.id"], ondelete="CASCADE"),
        UniqueConstraint("engine_run_id", "file_entry_id"),
        CheckConstraint(enum_check("outcome", CoverageOutcome), name="outcome_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    engine_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    file_entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class Finding(TimestampMixin, Base):
    """One engine observation in one scan, anchored to a snapshot source span, file or dependency.

    Dependency findings may have no line (``anchor_kind = 'dependency'``); no line is invented.
    """

    __tablename__ = "findings"
    __table_args__ = (
        ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["engine_run_id"], ["engine_runs.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["file_entry_id"], ["file_entries.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="SET NULL"),
        UniqueConstraint("scan_id", "fingerprint"),
        Index("ix_findings_scan_id_correlation_key", "scan_id", "correlation_key"),
        CheckConstraint(enum_check("severity", Severity), name="severity_valid"),
        CheckConstraint(enum_check("category", FindingCategory), name="category_valid"),
        CheckConstraint(enum_check("status", FindingStatus), name="status_valid"),
        CheckConstraint(enum_check("anchor_kind", AnchorKind), name="anchor_kind_valid"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint("correlation_key ~ '^[0-9a-f]{64}$'", name="correlation_key_format"),
        CheckConstraint(
            "(start_line IS NULL AND end_line IS NULL AND anchor_kind <> 'source_span') "
            "OR (start_line IS NOT NULL AND end_line IS NOT NULL "
            "AND start_line >= 1 AND end_line >= start_line)",
            name="span_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    engine_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    file_entry_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ruleset: Mapped[str | None] = mapped_column(String(128))
    engine_severity: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=AnchorKind.SOURCE_SPAN.value
    )
    start_line: Mapped[int | None] = mapped_column(Integer)
    start_column: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    end_column: Mapped[int | None] = mapped_column(Integer)
    rule_url: Mapped[str | None] = mapped_column(String(512))
    # Status at observation time; the durable, triaged status lives on the linked issue.
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    in_catalog: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    correlation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_family: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    guidance: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)


class ScanEvent(Base):
    """Append-only progress events; ``id`` is the monotonic SSE event ID used for resume."""

    __tablename__ = "scan_events"
    __table_args__ = (ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="CASCADE"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Issue(TimestampMixin, Base):
    """Durable identity of an engine observation across scans of a project (by fingerprint).

    ``status`` is the triage/lifecycle decision; ``recheck_state`` records what the newest
    compatible scan could prove. RESOLVED is only set after a verified absence.
    """

    __tablename__ = "issues"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["first_seen_scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["last_seen_scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["last_evaluated_scan_id"], ["scans.id"], ondelete="SET NULL"),
        UniqueConstraint("project_id", "fingerprint"),
        UniqueConstraint("workspace_id", "project_id", "id"),
        CheckConstraint(enum_check("status", FindingStatus), name="status_valid"),
        CheckConstraint(enum_check("recheck_state", RecheckState), name="recheck_state_valid"),
        CheckConstraint(enum_check("severity", Severity), name="severity_valid"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint(
            "status NOT IN ('ACCEPTED_RISK', 'FALSE_POSITIVE') "
            "OR length(btrim(coalesce(exception_reason, ''))) > 0",
            name="exception_requires_reason",
        ),
        CheckConstraint(
            "status <> 'ACCEPTED_RISK' OR exception_expires_at IS NOT NULL",
            name="accepted_risk_requires_expiry",
        ),
        Index("ix_issues_project_id_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    recheck_state: Mapped[str] = mapped_column(String(24), nullable=False)
    recheck_reason: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(200))
    exception_reason: Mapped[str | None] = mapped_column(Text)
    exception_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_seen_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_evaluated_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    last_seen_engine_version: Mapped[str | None] = mapped_column(String(128))
    last_seen_ruleset_sha256: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class IssueEvent(Base):
    """Append-only audit trail of issue status, recheck, owner and exception changes."""

    __tablename__ = "issue_events"
    __table_args__ = (
        ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint("actor_kind IN ('user', 'system')", name="actor_kind_valid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    issue_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    actor_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    changes: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GraphBuild(TimestampMixin, Base):
    """One structural-graph extraction for a snapshot. Only the ``is_current`` build is served.

    A newer build (including a failed one) always replaces the previous current build, so links
    from an earlier extraction are never presented as current facts.
    """

    __tablename__ = "graph_builds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="SET NULL"),
        UniqueConstraint("workspace_id", "project_id", "snapshot_id", "id"),
        CheckConstraint(enum_check("state", GraphBuildState), name="state_valid"),
        Index(
            "uq_graph_builds_current_snapshot",
            "snapshot_id",
            unique=True,
            postgresql_where="is_current",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    extractor: Mapped[str] = mapped_column(String(256), nullable=False)
    node_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    edge_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unresolved_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_parsed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    files_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    diagnostics: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GraphNode(Base):
    """Snapshot-scoped graph node; file anchors must belong to the build's snapshot (by FK)."""

    __tablename__ = "graph_nodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id", "build_id"],
            [
                "graph_builds.workspace_id",
                "graph_builds.project_id",
                "graph_builds.snapshot_id",
                "graph_builds.id",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id", "file_entry_id"],
            [
                "file_entries.workspace_id",
                "file_entries.project_id",
                "file_entries.snapshot_id",
                "file_entries.id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("build_id", "key"),
        UniqueConstraint("build_id", "id"),
        CheckConstraint(enum_check("kind", GraphNodeKind), name="kind_valid"),
        CheckConstraint(
            "start_line IS NULL "
            "OR (end_line IS NOT NULL AND start_line >= 1 AND end_line >= start_line)",
            name="span_valid",
        ),
        Index("ix_graph_nodes_build_id_kind", "build_id", "kind"),
        Index("ix_graph_nodes_build_id_file_entry_id", "build_id", "file_entry_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    build_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str] = mapped_column(String(1024), nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    module_key: Mapped[str | None] = mapped_column(String(1024))
    file_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)


class GraphEdge(Base):
    """A relation with source evidence, extractor identity and a resolution classification."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["build_id", "source_node_id"],
            ["graph_nodes.build_id", "graph_nodes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["build_id", "target_node_id"],
            ["graph_nodes.build_id", "graph_nodes.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id", "evidence_file_entry_id"],
            [
                "file_entries.workspace_id",
                "file_entries.project_id",
                "file_entries.snapshot_id",
                "file_entries.id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            enum_check("classification", EdgeClassification), name="classification_valid"
        ),
        CheckConstraint(
            "classification <> 'resolved' OR target_node_id IS NOT NULL",
            name="resolved_has_target",
        ),
        CheckConstraint(
            "evidence_start_line IS NULL "
            "OR (evidence_end_line IS NOT NULL AND evidence_start_line >= 1 "
            "AND evidence_end_line >= evidence_start_line)",
            name="evidence_span_valid",
        ),
        Index("ix_graph_edges_build_id_source_node_id", "build_id", "source_node_id"),
        Index("ix_graph_edges_build_id_target_node_id", "build_id", "target_node_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    build_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_node_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_node_id: Mapped[int | None] = mapped_column(BigInteger)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(1024), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_file_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    evidence_start_line: Mapped[int | None] = mapped_column(Integer)
    evidence_end_line: Mapped[int | None] = mapped_column(Integer)
    evidence_text: Mapped[str | None] = mapped_column(String(500))
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)


class EngineCacheEntry(Base):
    """Per-file engine result reusable only for identical content, engine, rules and config.

    Scoped to one project (deleted with it). The key covers workspace, project, engine, engine
    version, rule-set hash, configuration fingerprint, normalization versions, path and blob hash.
    """

    __tablename__ = "engine_cache"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "cache_key"),
        CheckConstraint("cache_key ~ '^[0-9a-f]{64}$'", name="cache_key_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(128), nullable=False)
    ruleset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ArtifactObject(Base):
    """Artifact bytes kept in PostgreSQL (``CRP_ARTIFACT_BACKEND=postgres``, lite deployments).

    Keys are validated ``ArtifactKey`` strings; objects are size-bounded by the store.
    """

    __tablename__ = "artifact_objects"
    __table_args__ = (
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        CheckConstraint("size_bytes = octet_length(data)", name="size_matches_data"),
    )

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectAiPolicy(Base):
    """Per-project source-disclosure policy for AI review (default: off). ADR 0012.

    Only when ``enabled`` may bounded, secret-masked excerpts of this project's snapshots be sent
    to the configured model provider. Changes are audited in ``ai_policy_events``.
    """

    __tablename__ = "project_ai_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("max_excerpt_lines BETWEEN 10 AND 400", name="max_excerpt_lines_range"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    max_excerpt_lines: Mapped[int] = mapped_column(Integer, nullable=False, server_default="120")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class AiPolicyEvent(Base):
    """Append-only audit trail of AI policy changes (who allowed or stopped source disclosure)."""

    __tablename__ = "ai_policy_events"
    __table_args__ = (
        ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    changes: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiRun(TimestampMixin, Base):
    """One bounded AI investigation of a snapshot: a question, a finding review or a file review.

    ``steps`` is a concise action log (tool, arguments summary, outcome), never private
    reasoning. ``usage`` holds provider-reported tokens and, only when the owner configured
    prices, an estimated cost.
    """

    __tablename__ = "ai_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint(enum_check("kind", AiRunKind), name="kind_valid"),
        CheckConstraint(enum_check("state", AiRunState), name="state_valid"),
        CheckConstraint(
            "kind <> 'question' OR length(btrim(coalesce(question, ''))) > 0",
            name="question_requires_text",
        ),
        CheckConstraint(
            "kind <> 'finding_review' OR finding_id IS NOT NULL OR state <> 'QUEUED'",
            name="finding_review_requires_finding",
        ),
        Index("ix_ai_runs_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    finding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    question: Mapped[str | None] = mapped_column(Text)
    target_paths: Mapped[list[str] | None] = mapped_column(JsonDocument)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    limits: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    usage: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    answer: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    steps: Mapped[list[dict[str, object]] | None] = mapped_column(JsonDocument)
    limitations: Mapped[list[str] | None] = mapped_column(JsonDocument)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class AiCall(Base):
    """Usage accounting per provider call (also the source of the monthly spend cap)."""

    __tablename__ = "ai_calls"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["ai_runs.id"], ondelete="CASCADE"),
        CheckConstraint("status IN ('ok', 'error')", name="status_valid"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cache_read_tokens >= 0 "
            "AND cache_write_tokens >= 0",
            name="tokens_non_negative",
        ),
        Index("ix_ai_calls_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    usage_reported: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiFinding(Base):
    """A structured AI claim with its evidence class; severity, evidence and confidence differ."""

    __tablename__ = "ai_findings"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["ai_runs.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["related_finding_id"], ["findings.id"], ondelete="SET NULL"),
        CheckConstraint(enum_check("severity", Severity), name="severity_valid"),
        CheckConstraint(enum_check("category", FindingCategory), name="category_valid"),
        CheckConstraint(enum_check("confidence", AiConfidence), name="confidence_valid"),
        CheckConstraint(enum_check("evidence_class", AiEvidenceClass), name="evidence_class_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    severity_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    evidence_class: Mapped[str] = mapped_column(String(24), nullable=False)
    anchors: Mapped[list[dict[str, object]]] = mapped_column(JsonDocument, nullable=False)
    triggering_conditions: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    validation_needed: Mapped[str | None] = mapped_column(Text)
    uncertainty: Mapped[str | None] = mapped_column(Text)
    related_finding_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    verification: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FixProposal(TimestampMixin, Base):
    """A proposed repair for one finding, bound to its snapshot, file base hash and patch hash.

    The patch is applied only to copies; the upload is never modified. ``allowed_paths`` is the
    scope the patch may touch (v1: the finding's file). Editing the patch resets validation.
    """

    __tablename__ = "fix_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint(enum_check("kind", FixKind), name="kind_valid"),
        CheckConstraint(enum_check("state", FixProposalState), name="state_valid"),
        CheckConstraint("base_sha256 ~ '^[0-9a-f]{64}$'", name="base_sha256_format"),
        CheckConstraint("result_sha256 ~ '^[0-9a-f]{64}$'", name="result_sha256_format"),
        CheckConstraint("patch_sha256 ~ '^[0-9a-f]{64}$'", name="patch_sha256_format"),
        CheckConstraint(
            "validations_used >= 0 AND validations_used <= max_validations",
            name="validation_budget",
        ),
        Index("ix_fix_proposals_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    recipe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    behaviour_note: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    target_line: Mapped[int | None] = mapped_column(Integer)  # the finding's line in this file
    rebased_from: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    allowed_paths: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    base_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    patch: Mapped[str] = mapped_column(Text, nullable=False)
    edits: Mapped[list[dict[str, object]]] = mapped_column(JsonDocument, nullable=False)
    changed_lines: Mapped[int] = mapped_column(Integer, nullable=False)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    validations_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_validations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    rejected_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class FixValidation(Base):
    """One validation-ladder run for an exact patch; results bind to the patch and result hashes."""

    __tablename__ = "fix_validations"
    __table_args__ = (
        ForeignKeyConstraint(["proposal_id"], ["fix_proposals.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint(enum_check("state", FixValidationState), name="state_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    patch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    steps: Mapped[list[dict[str, object]] | None] = mapped_column(JsonDocument)
    summary: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


_SHA = "'^([0-9a-f]{40}|[0-9a-f]{64})$'"


class GitInstallation(TimestampMixin, Base):
    """A Git provider app installation, linked to exactly one workspace after the installing user
    proved access to it (GitHub user authorization); unlinked installations are never used."""

    __tablename__ = "git_installations"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        UniqueConstraint("workspace_id", "id"),
        CheckConstraint(enum_check("provider", GitProvider), name="provider_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    repository_selection: Mapped[str | None] = mapped_column(String(16))
    permissions: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class GitRepository(TimestampMixin, Base):
    """A repository an installation can read. ``removed_at`` is set when access is withdrawn."""

    __tablename__ = "git_repositories"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "installation_id"],
            ["git_installations.workspace_id", "git_installations.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("installation_id", "external_id"),
        UniqueConstraint("workspace_id", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    installation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    external_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    private: Mapped[bool] = mapped_column(Boolean, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class GitConnection(TimestampMixin, Base):
    """A project's connected repository and its review/publication policy (admin-managed).

    Publication (checks, comments, fix pull requests) is off until an admin switches it on;
    pull requests from forks are not reviewed automatically unless allowed.
    """

    __tablename__ = "git_connections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "repository_id"],
            ["git_repositories.workspace_id", "git_repositories.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "source_id"],
            ["sources.workspace_id", "sources.project_id", "sources.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        UniqueConstraint("project_id"),
        UniqueConstraint("repository_id"),
        CheckConstraint(
            enum_check("check_fail_threshold", CheckFailThreshold), name="threshold_valid"
        ),
        CheckConstraint("reconcile_days BETWEEN 1 AND 90", name="reconcile_days_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    repository_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    review_pushes: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    review_pull_requests: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    review_forks: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    publish_checks: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    publish_pull_requests: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    check_fail_threshold: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="never"
    )
    reconcile_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    last_full_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class GitConnectionEvent(Base):
    """Append-only audit of connection and policy changes."""

    __tablename__ = "git_connection_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    changes: Mapped[dict[str, object]] = mapped_column(JsonDocument, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GitLinkRequest(Base):
    """One-time state for linking installations through GitHub user authorization.

    Only the SHA-256 of the state is stored; it is bound to the user and workspace, expires after
    ten minutes and can be used once.
    """

    __tablename__ = "git_link_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GitDelivery(Base):
    """A received webhook delivery (signature already verified); the id makes redelivery a no-op.

    Payloads are not stored, only what routing needs.
    """

    __tablename__ = "git_deliveries"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id"),
        CheckConstraint(enum_check("provider", GitProvider), name="provider_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str | None] = mapped_column(String(64))
    installation_external_id: Mapped[int | None] = mapped_column(BigInteger)
    repository_external_id: Mapped[int | None] = mapped_column(BigInteger)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    review_ids: Mapped[list[str] | None] = mapped_column(JsonDocument)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class CodeReview(TimestampMixin, Base):
    """A review of a branch's latest commit or of a pull request, bound to exact commits.

    The reviewed head is resolved when the review runs, so late or duplicate events converge on
    the newest commit; older reviews of the same branch or pull request end SUPERSEDED.
    """

    __tablename__ = "code_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["connection_id"], ["git_connections.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["head_snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["base_snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["head_scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["base_scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint(enum_check("kind", CodeReviewKind), name="kind_valid"),
        CheckConstraint(enum_check("trigger", CodeReviewTrigger), name="trigger_valid"),
        CheckConstraint(enum_check("state", CodeReviewState), name="state_valid"),
        CheckConstraint(
            f"publish_state IS NULL OR {enum_check('publish_state', PublishState)}",
            name="publish_state_valid",
        ),
        CheckConstraint(f"head_sha IS NULL OR head_sha ~ {_SHA}", name="head_sha_format"),
        CheckConstraint(f"base_sha IS NULL OR base_sha ~ {_SHA}", name="base_sha_format"),
        CheckConstraint(
            f"merge_base_sha IS NULL OR merge_base_sha ~ {_SHA}", name="merge_base_sha_format"
        ),
        CheckConstraint(
            "kind <> 'pull_request' OR pr_number IS NOT NULL", name="pull_request_number"
        ),
        Index("ix_code_reviews_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    ref: Mapped[str | None] = mapped_column(String(255))  # branch name (pull request: head)
    head_sha: Mapped[str | None] = mapped_column(String(64))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_title: Mapped[str | None] = mapped_column(String(300))  # untrusted display text
    pr_author: Mapped[str | None] = mapped_column(String(100))
    pr_url: Mapped[str | None] = mapped_column(String(500))
    fork: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    base_ref: Mapped[str | None] = mapped_column(String(255))
    base_sha: Mapped[str | None] = mapped_column(String(64))
    merge_base_sha: Mapped[str | None] = mapped_column(String(64))
    full: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    head_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    base_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    head_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    base_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    changes: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    result: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    publish_state: Mapped[str | None] = mapped_column(String(16))
    publish_error: Mapped[str | None] = mapped_column(Text)
    check_run_id: Mapped[int | None] = mapped_column(BigInteger)
    comment_id: Mapped[int | None] = mapped_column(BigInteger)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    delivery_id: Mapped[str | None] = mapped_column(String(64))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FixPullRequest(Base):
    """A pull request opened for a validated fix after a person asked for it (never merged)."""

    __tablename__ = "fix_pull_requests"
    __table_args__ = (
        ForeignKeyConstraint(["proposal_id"], ["fix_proposals.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        UniqueConstraint("proposal_id"),
        CheckConstraint(f"base_sha ~ {_SHA}", name="base_sha_format"),
        CheckConstraint(f"commit_sha ~ {_SHA}", name="commit_sha_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChangeSet(TimestampMixin, Base):
    """A fix workspace: edits to many files of one upload, never applied to the upload itself.

    ``content_sha256`` identifies the current set of file revisions; checks and exports record the
    value they were made from.
    """

    __tablename__ = "change_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id", "base_snapshot_id"],
            ["snapshots.workspace_id", "snapshots.project_id", "snapshots.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["base_scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="content_sha256_format"),
        Index("ix_change_sets_project_id_created_at", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    base_snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    base_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy declarative API


class ChangeSetFile(Base):
    """The current revision of one file in a workspace (content in the artifact store)."""

    __tablename__ = "change_set_files"
    __table_args__ = (
        ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["updated_by"], ["users.id"], ondelete="SET NULL"),
        UniqueConstraint("change_set_id", "path"),
        CheckConstraint(enum_check("action", ChangeAction), name="action_valid"),
        CheckConstraint("(action = 'delete') = (sha256 IS NULL)", name="delete_has_no_content"),
        CheckConstraint("(action = 'add') = (base_sha256 IS NULL)", name="add_has_no_base"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    base_sha256: Mapped[str | None] = mapped_column(String(64))
    sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    line_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(32))
    flags: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    sources: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChangeSetEvent(Base):
    """Append-only provenance: who changed which file how, for which findings (and exports)."""

    __tablename__ = "change_set_events"
    __table_args__ = (
        ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint(enum_check("source", ChangeSource), name="source_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str | None] = mapped_column(String(8))
    finding_ids: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    recipe_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    flags: Mapped[list[str]] = mapped_column(JsonDocument, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChangeSetCheck(Base):
    """A re-check of a workspace: its edits applied to a derived snapshot, scanned, compared."""

    __tablename__ = "change_set_checks"
    __table_args__ = (
        ForeignKeyConstraint(["change_set_id"], ["change_sets.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["snapshot_id"], ["snapshots.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["base_scan_id"], ["scans.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        CheckConstraint(enum_check("state", ChangeSetCheckState), name="state_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    files: Mapped[list[dict[str, object]]] = mapped_column(JsonDocument, nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    base_scan_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    result: Mapped[dict[str, object] | None] = mapped_column(JsonDocument)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    workflow_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
