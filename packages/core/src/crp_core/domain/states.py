"""Domain enumerations and lifecycle state machines.

Database columns store these values as text guarded by CHECK constraints generated from the same
enums, so code and schema share one definition.
"""

from __future__ import annotations

from enum import StrEnum


class MembershipRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]

    def at_least(self, required: MembershipRole) -> bool:
        return self.rank >= required.rank


_ROLE_RANK = {
    MembershipRole.VIEWER: 0,
    MembershipRole.MEMBER: 1,
    MembershipRole.ADMIN: 2,
    MembershipRole.OWNER: 3,
}


class ProjectOrigin(StrEnum):
    """Whether a project was created by a user or seeded as a clearly labelled synthetic fixture."""

    USER = "user"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class SourceMode(StrEnum):
    ZIP_UPLOAD = "zip_upload"
    LOCAL_RUNNER = "local_runner"
    BROWSER_FILES = "browser_files"
    REGISTERED_MOUNT = "registered_mount"


class CaptureStatus(StrEnum):
    PENDING = "PENDING"
    CAPTURING = "CAPTURING"
    FROZEN = "FROZEN"
    FAILED = "FAILED"
    INCONSISTENT = "INCONSISTENT"


class ScanState(StrEnum):
    """Scan lifecycle. SUCCEEDED means the selected scope completed, not that no defects exist."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    BLOCKED = "BLOCKED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"

    @property
    def is_terminal(self) -> bool:
        return not SCAN_TRANSITIONS[self]


SCAN_TRANSITIONS: dict[ScanState, frozenset[ScanState]] = {
    ScanState.QUEUED: frozenset({ScanState.RUNNING, ScanState.CANCELED, ScanState.BLOCKED}),
    ScanState.RUNNING: frozenset(
        {
            ScanState.SUCCEEDED,
            ScanState.PARTIAL,
            ScanState.FAILED,
            ScanState.CANCELED,
            ScanState.BUDGET_EXHAUSTED,
        }
    ),
    ScanState.BLOCKED: frozenset({ScanState.QUEUED, ScanState.CANCELED}),
    ScanState.SUCCEEDED: frozenset(),
    ScanState.PARTIAL: frozenset(),
    ScanState.FAILED: frozenset(),
    ScanState.CANCELED: frozenset(),
    ScanState.BUDGET_EXHAUSTED: frozenset(),
}


class InvalidTransitionError(ValueError):
    pass


def require_scan_transition(current: ScanState, target: ScanState) -> ScanState:
    """Validate a scan state change, raising InvalidTransitionError when it is not allowed."""
    if target not in SCAN_TRANSITIONS[current]:
        raise InvalidTransitionError(f"scan cannot move from {current} to {target}")
    return target


class IntakeState(StrEnum):
    """Intake lifecycle (SOURCE_INTAKE.md). Only READY intakes own a frozen snapshot."""

    CREATED = "CREATED"
    UPLOADING = "UPLOADING"
    VALIDATING = "VALIDATING"
    READY = "READY"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"

    @property
    def is_terminal(self) -> bool:
        return not INTAKE_TRANSITIONS[self]


INTAKE_TRANSITIONS: dict[IntakeState, frozenset[IntakeState]] = {
    IntakeState.CREATED: frozenset({IntakeState.UPLOADING, IntakeState.CANCELED}),
    IntakeState.UPLOADING: frozenset(
        {IntakeState.UPLOADING, IntakeState.VALIDATING, IntakeState.REJECTED, IntakeState.CANCELED}
    ),
    IntakeState.VALIDATING: frozenset(
        {IntakeState.READY, IntakeState.REJECTED, IntakeState.FAILED, IntakeState.CANCELED}
    ),
    IntakeState.READY: frozenset(),
    IntakeState.REJECTED: frozenset(),
    IntakeState.FAILED: frozenset(),
    IntakeState.CANCELED: frozenset(),
}


def require_intake_transition(current: IntakeState, target: IntakeState) -> IntakeState:
    if target not in INTAKE_TRANSITIONS[current]:
        raise InvalidTransitionError(f"intake cannot move from {current} to {target}")
    return target


class FileDisposition(StrEnum):
    """Per-entry manifest disposition. Only ANALYZABLE files are stored and eligible for engines."""

    ANALYZABLE = "ANALYZABLE"
    BINARY = "BINARY"
    OVERSIZED = "OVERSIZED"
    EXCLUDED = "EXCLUDED"


class EngineState(StrEnum):
    """Engine-run outcome. SUCCEEDED means the eligible scope completed, not "no defects"."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CoverageOutcome(StrEnum):
    ANALYZED = "ANALYZED"
    FAILED = "FAILED"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(StrEnum):
    CORRECTNESS = "correctness"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    CODING_STANDARDS = "coding_standards"
    RELIABILITY = "reliability"
    DEPENDENCIES = "dependencies"


class AnchorKind(StrEnum):
    """What a finding's evidence points at. Dependency findings may have no source line."""

    SOURCE_SPAN = "source_span"
    FILE = "file"
    DEPENDENCY = "dependency"


class RecheckState(StrEnum):
    """Whether the latest compatible scan re-examined an issue (separate from its status)."""

    VERIFIED_PRESENT = "VERIFIED_PRESENT"
    VERIFIED_ABSENT = "VERIFIED_ABSENT"
    NOT_RECHECKED = "NOT_RECHECKED"
    UNKNOWN = "UNKNOWN"
    RULE_OBSOLETE = "RULE_OBSOLETE"


class GraphNodeKind(StrEnum):
    MODULE = "module"
    FILE = "file"
    PACKAGE = "package"
    TYPE = "type"
    FUNCTION = "function"
    EXTERNAL = "external"
    COMPONENT = "component"  # framework component (Spring bean, item type, SObject, LWC, ...)


class EdgeClassification(StrEnum):
    """How a graph edge's target was determined.

    Declared: stated in syntax, target not located. Resolved: target located deterministically in
    the snapshot. Inferred: heuristic match. Unresolved: target unknown or outside the snapshot.
    """

    DECLARED = "declared"
    RESOLVED = "resolved"
    INFERRED = "inferred"
    UNRESOLVED = "unresolved"


class FindingStatus(StrEnum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    ACCEPTED_RISK = "ACCEPTED_RISK"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    FIX_PROPOSED = "FIX_PROPOSED"
    RESOLVED = "RESOLVED"


# Statuses a person may set through triage. RESOLVED is only set by a compatible successful
# recheck (verified absence) and FIX_PROPOSED only by the fix workflow.
TRIAGE_STATUSES = frozenset(
    {
        FindingStatus.OPEN,
        FindingStatus.TRIAGED,
        FindingStatus.ACCEPTED_RISK,
        FindingStatus.FALSE_POSITIVE,
    }
)


class CacheMode(StrEnum):
    """``use`` reuses compatible per-file engine results; ``refresh`` re-runs and overwrites."""

    USE = "use"
    REFRESH = "refresh"


class GraphBuildState(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class AiRunKind(StrEnum):
    """What an AI run investigates (P03; ADR 0012)."""

    QUESTION = "question"  # a repository question answered with citations
    FINDING_REVIEW = "finding_review"  # explain and check one deterministic finding
    FILE_REVIEW = "file_review"  # review selected files for additional problems


class AiRunState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"  # useful results, but a limit or failure left scope unreviewed
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"

    @property
    def is_terminal(self) -> bool:
        return self not in {AiRunState.QUEUED, AiRunState.RUNNING}


class AiEvidenceClass(StrEnum):
    """How far an AI claim was checked against the snapshot (never the model's own word)."""

    VERIFIED_ANCHOR = "verified_anchor"  # every cited location exists and matches the quote
    HYPOTHESIS = "hypothesis"  # plausible but not anchored in verifiable code
    REJECTED = "rejected"  # cited code does not exist or does not match; not shown as a finding


class AiConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
