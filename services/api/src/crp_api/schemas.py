"""Public API request/response models. These Pydantic models are the authoritative contract;
``packages/contracts/openapi.json`` and the web client's TypeScript types are generated from them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from crp_core.config import DeploymentEnvironment
from crp_core.domain.states import MembershipRole, ProjectOrigin
from crp_core.workflows.contracts import DiagnosticWorkflowResult
from crp_core.workflows.gateway import WorkflowRunStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- health ----------------------------------------------------------------------------------


class LivenessResponse(ApiModel):
    status: str = Field(examples=["alive"])
    service: str
    version: str


class CheckStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class OverallReadiness(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class DependencyCheck(ApiModel):
    name: str
    status: CheckStatus
    latency_ms: float | None
    summary: str
    error_code: str | None = None
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class ReadinessReport(ApiModel):
    status: OverallReadiness
    checked_at: datetime
    environment: str
    checks: list[DependencyCheck]


# -- capabilities ------------------------------------------------------------------------------


class CapabilityState(StrEnum):
    AVAILABLE = "available"
    NOT_CONFIGURED = "not_configured"  # implemented, but this server has not set it up
    PLANNED = "planned"


class Capability(ApiModel):
    id: str
    label: str
    state: CapabilityState
    phase: str
    reason: str


class CapabilityList(ApiModel):
    capabilities: list[Capability]


# -- auth --------------------------------------------------------------------------------------


class SessionRequest(ApiModel):
    token: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class SessionResponse(ApiModel):
    subject: str
    expires_at: datetime


class WorkspaceGrantResponse(ApiModel):
    workspace_id: UUID
    slug: str
    name: str
    role: MembershipRole


class PrincipalResponse(ApiModel):
    user_id: UUID
    subject: str
    display_name: str
    auth_method: str
    is_operator: bool
    is_demo: bool = Field(
        description="Signed in with the shared demo account (demo workspace only)"
    )
    workspaces: list[WorkspaceGrantResponse]


class AuthOptions(ApiModel):
    """Public sign-in options for the web UI (no credentials needed)."""

    environment: DeploymentEnvironment
    demo_enabled: bool


# -- projects ----------------------------------------------------------------------------------

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")]


class ProjectCreate(ApiModel):
    workspace_id: UUID
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    slug: Slug | None = Field(
        default=None, description="Optional; derived from the name when omitted"
    )
    description: Annotated[str, StringConstraints(max_length=2000)] = ""


class ProjectResponse(ApiModel):
    id: UUID
    workspace_id: UUID
    slug: str
    name: str
    description: str
    origin: ProjectOrigin
    created_at: datetime
    updated_at: datetime
    version: int


class ProjectPage(ApiModel):
    items: list[ProjectResponse]
    next_cursor: str | None


class SampleProjectCreate(ApiModel):
    workspace_id: UUID


# -- diagnostics -------------------------------------------------------------------------------


class DiagnosticRunResponse(ApiModel):
    workflow_id: str
    status: WorkflowRunStatus
    started_at: datetime | None = None
    closed_at: datetime | None = None
    result: DiagnosticWorkflowResult | None = None
    failure_message: str | None = None


# -- intake ------------------------------------------------------------------------------------


class IntakeLimits(ApiModel):
    max_upload_bytes: int
    max_expanded_bytes: int
    max_entries: int
    max_text_file_bytes: int
    max_compression_ratio: int


class IntakePolicyResponse(ApiModel):
    version: str
    excluded_directories: dict[str, str]
    secret_patterns: list[str]
    secret_allowlist: list[str]
    generated_patterns: list[str]
    limits: IntakeLimits
    notes: list[str]


class IntakeCreate(ApiModel):
    mode: Literal["zip_upload", "local_runner"]
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]


class UploadTicketResponse(ApiModel):
    """Where and until when the archive may be uploaded without other credentials."""

    upload_url: str = Field(
        description="Relative in local mode; the API's own https URL in hosted mode"
    )
    expires_at: datetime
    max_bytes: int


class IntakeResponse(ApiModel):
    id: UUID
    project_id: UUID
    source_id: UUID
    mode: str
    state: str
    archive_sha256: str | None
    archive_bytes: int | None
    snapshot_id: UUID | None
    error_code: str | None
    error_message: str | None
    error_details: dict[str, object] | None
    created_at: datetime
    finalized_at: datetime | None
    expires_at: datetime
    limits: IntakeLimits


class SampleProjectResponse(ApiModel):
    """The new sample project and the intake that is freezing its snapshot."""

    project: ProjectResponse
    intake: IntakeResponse


class IntakePage(ApiModel):
    items: list[IntakeResponse]


# -- snapshots and files ----------------------------------------------------------------------


class SnapshotResponse(ApiModel):
    id: UUID
    project_id: UUID
    source_id: UUID
    source_mode: str
    source_name: str
    intake_id: UUID | None
    capture_status: str
    manifest_sha256: str | None
    manifest_version: int
    policy_version: str | None
    file_count: int
    analyzable_count: int
    excluded_count: int
    total_bytes: int
    git_commit: str | None
    frozen_at: datetime | None
    created_at: datetime
    inventory: dict[str, object] | None


class SnapshotPage(ApiModel):
    items: list[SnapshotResponse]


class FileEntryResponse(ApiModel):
    id: UUID
    path: str
    disposition: str
    reason: str | None
    size_bytes: int | None
    sha256: str | None
    language: str | None
    category: str | None
    line_count: int | None
    parse_status: str | None
    parse_error_count: int


class FilePage(ApiModel):
    items: list[FileEntryResponse]
    next_cursor: str | None
    total: int


class FileContentResponse(ApiModel):
    file_id: UUID
    path: str
    language: str | None
    total_lines: int
    start_line: int
    end_line: int
    lines: list[str]
    redactions: int
    truncated: bool


class SymbolResponse(ApiModel):
    kind: str
    name: str
    container: str | None
    start_line: int
    end_line: int


class SymbolList(ApiModel):
    items: list[SymbolResponse]
    truncated: bool


# -- scans, coverage, findings ------------------------------------------------------------------


class ScanCreate(ApiModel):
    snapshot_id: UUID
    cache_mode: Literal["use", "refresh"] = Field(
        default="use",
        description="use: reuse compatible per-file engine results; refresh: re-run everything "
        "and overwrite cached results (full rescan).",
    )


class EngineRunResponse(ApiModel):
    engine: str
    engine_version: str | None
    ruleset_id: str | None
    ruleset_sha256: str | None
    state: str
    files_eligible: int
    files_attempted: int
    files_succeeded: int
    files_failed: int
    findings_count: int
    exit_code: int | None
    duration_ms: int | None
    error_code: str | None
    error_message: str | None
    diagnostics: dict[str, object] | None
    enabled_rule_count: int | None = Field(
        default=None, description="None when the rule set is open-ended (vulnerability database)"
    )
    cache_hits: int = 0
    cache_misses: int = 0
    raw_artifact_sha256: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ScanResponse(ApiModel):
    id: UUID
    project_id: UUID
    snapshot_id: UUID
    cache_mode: str = "use"
    lifecycle_applied: bool = False
    manifest_sha256: str | None
    state: str
    mode: str
    policy_version: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    engines: list[EngineRunResponse]
    summary: dict[str, object] | None


class ScanPage(ApiModel):
    items: list[ScanResponse]


class CoverageRow(ApiModel):
    file_id: UUID
    path: str
    engine: str
    outcome: str
    reason: str | None
    cached: bool = False


class CoveragePage(ApiModel):
    items: list[CoverageRow]
    next_cursor: str | None


class IssueRef(ApiModel):
    id: UUID
    status: str
    recheck_state: str
    version: int


class CorrelatedFinding(ApiModel):
    """Another engine's observation of the same construct (same rule family and location)."""

    id: UUID
    engine: str
    rule_id: str
    severity: str


class FindingResponse(ApiModel):
    id: UUID
    scan_id: UUID
    snapshot_id: UUID
    file_id: UUID
    path: str
    engine: str
    engine_version: str
    rule_id: str
    ruleset: str | None
    severity: str
    category: str
    confidence: str
    title: str
    message: str
    anchor_kind: Literal["source_span", "file", "dependency"]
    start_line: int | None
    start_column: int | None
    end_line: int | None
    end_column: int | None
    rule_url: str | None
    status: str = Field(description="Status at observation time; see issue for triage state")
    fingerprint: str
    correlation_key: str
    rule_family: str | None
    in_catalog: bool
    details: dict[str, object] | None = None
    issue: IssueRef | None = None
    also_reported_by: list[CorrelatedFinding] = Field(default_factory=list)


class FindingPage(ApiModel):
    items: list[FindingResponse]
    next_cursor: str | None
    total: int


class RuleGuidance(ApiModel):
    title: str
    explanation: str
    recommendation: str
    severity_rationale: str
    url: str | None
    in_catalog: bool


class FindingDetailResponse(ApiModel):
    project_id: UUID
    finding: FindingResponse
    rule: RuleGuidance
    source: FileContentResponse | None
    related: list[FindingResponse] = Field(
        default_factory=list, description="Correlated observations from other engines"
    )
    manifest_sha256: str | None
    evidence_note: str


class ProjectOverview(ApiModel):
    project: ProjectResponse
    latest_snapshot: SnapshotResponse | None
    latest_scan: ScanResponse | None
    snapshot_count: int
    scan_count: int


# -- issues -------------------------------------------------------------------------------------


class IssueResponse(ApiModel):
    id: UUID
    project_id: UUID
    fingerprint: str
    engine: str
    rule_id: str
    path: str
    title: str
    severity: str
    category: str
    status: str
    recheck_state: str
    recheck_reason: str | None
    owner: str | None
    exception_reason: str | None
    exception_expires_at: datetime | None
    exception_expired: bool
    first_seen_scan_id: UUID | None
    last_seen_scan_id: UUID | None
    last_evaluated_scan_id: UUID | None
    last_seen_engine_version: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class IssuePage(ApiModel):
    items: list[IssueResponse]
    next_cursor: str | None
    total: int
    by_status: dict[str, int]
    by_recheck: dict[str, int]


class IssueEventResponse(ApiModel):
    id: int
    kind: str
    actor_kind: str
    actor_user_id: UUID | None
    scan_id: UUID | None
    changes: dict[str, object]
    reason: str | None
    created_at: datetime


class IssueDetailResponse(ApiModel):
    issue: IssueResponse
    events: list[IssueEventResponse]
    latest_finding_id: UUID | None


class IssueTriage(ApiModel):
    """Triage update with optimistic concurrency (``version`` must match the current issue)."""

    version: int = Field(ge=1)
    status: Literal["OPEN", "TRIAGED", "ACCEPTED_RISK", "FALSE_POSITIVE"] | None = None
    owner: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None = None
    expires_at: datetime | None = None


# -- comparison ---------------------------------------------------------------------------------


class ComparisonItem(ApiModel):
    fingerprint: str
    engine: str
    rule_id: str
    path: str
    title: str
    severity: str
    finding_id: UUID | None = Field(description="Finding in the target scan, when present")
    base_finding_id: UUID | None
    reason: str | None = None


class ComparisonGroup(ApiModel):
    count: int
    items: list[ComparisonItem]
    truncated: bool


class EngineCompatibility(ApiModel):
    engine: str
    base_state: str | None
    target_state: str | None
    base_version: str | None
    target_version: str | None
    same_rules: bool
    compatible: bool
    note: str


class ScanComparison(ApiModel):
    base_scan_id: UUID
    target_scan_id: UUID
    base_snapshot_id: UUID
    target_snapshot_id: UUID
    new: ComparisonGroup
    unchanged: ComparisonGroup
    verified_absent: ComparisonGroup
    not_rechecked: ComparisonGroup
    unknown: ComparisonGroup
    rule_obsolete: ComparisonGroup
    engines: list[EngineCompatibility]
    notes: list[str]


# -- graph --------------------------------------------------------------------------------------


class GraphBuildResponse(ApiModel):
    id: UUID
    snapshot_id: UUID
    scan_id: UUID | None
    state: str
    extractor: str
    node_count: int
    edge_count: int
    unresolved_count: int
    files_parsed: int
    files_failed: int
    diagnostics: dict[str, object] | None
    created_at: datetime


class GraphNodeResponse(ApiModel):
    id: int
    kind: str
    key: str
    label: str
    module_key: str | None
    file_id: UUID | None
    path: str | None
    start_line: int | None
    end_line: int | None
    attributes: dict[str, object] | None


class GraphEdgeResponse(ApiModel):
    id: int
    source_id: int
    target_id: int | None
    relation: str
    classification: str
    target_ref: str
    reason: str | None
    evidence_file_id: UUID | None
    evidence_path: str | None
    evidence_start_line: int | None
    evidence_end_line: int | None
    evidence_text: str | None
    extractor: str


class ModuleSummary(ApiModel):
    node: GraphNodeResponse
    files: int
    types: int


class ModuleDependency(ApiModel):
    source_key: str
    target_key: str
    relation: str
    edges: int
    classification: str


class FrameworkCapability(ApiModel):
    id: str
    label: str
    state: Literal["available", "partial", "unavailable"]
    detail: str


class FrameworkPack(ApiModel):
    """What a framework pack (SAP Commerce, Salesforce) detected and covers for this upload."""

    id: str
    name: str
    adapter: str
    status: str = Field(description="experimental or sme_reviewed")
    version: str | None
    version_status: Literal["supported", "unsupported_version", "unknown_version"]
    version_evidence: str | None = Field(description="path:line the version was read from")
    supported_versions: str
    capabilities: list[FrameworkCapability]
    relations: dict[str, int]
    components: dict[str, int]
    rules: list[str]
    notes: list[str]


class GraphSummary(ApiModel):
    build: GraphBuildResponse | None
    status: Literal["current", "failed", "none"]
    message: str
    nodes_by_kind: dict[str, int]
    edges_by_classification: dict[str, int]
    edges_by_relation: dict[str, int]
    modules: list[ModuleSummary]
    module_dependencies: list[ModuleDependency]
    unresolved_reasons: dict[str, int]
    frameworks: list[FrameworkPack] = Field(default_factory=list)


class GraphNodePage(ApiModel):
    items: list[GraphNodeResponse]
    next_cursor: str | None
    total: int


class GraphNeighborhood(ApiModel):
    build_id: UUID
    center: GraphNodeResponse
    depth: int
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    truncated: bool


class ImpactItem(ApiModel):
    node: GraphNodeResponse
    depth: int
    via: str


class GraphImpact(ApiModel):
    build_id: UUID
    target: GraphNodeResponse
    max_depth: int
    dependents: list[ImpactItem]
    truncated: bool
    unresolved_edges_in_build: int
    files_with_parse_problems: int
    caveats: list[str]


# -- AI review (P03) -----------------------------------------------------------------------------


class AiLimits(ApiModel):
    max_model_calls: int
    max_tool_calls: int
    max_tokens: int
    timeout_seconds: int
    max_cost_usd: float | None


class AiMonthUsage(ApiModel):
    month_start: datetime
    calls: int
    tokens: int
    token_limit: int = Field(description="0 means no monthly token limit")
    cost_usd: float | None = Field(description="Null when costs are unknown (no prices set)")
    cost_limit_usd: float | None


class AiStatus(ApiModel):
    available: bool
    provider: str
    model: str | None
    reason: str | None = Field(description="Why AI review is unavailable, for every user")
    admin_hint: str | None = Field(description="What to configure; only shown to administrators")
    prices_configured: bool
    limits: AiLimits
    month: AiMonthUsage


class ProjectAiPolicyResponse(ApiModel):
    project_id: UUID
    enabled: bool
    max_excerpt_lines: int
    updated_at: datetime | None
    version: int
    can_edit: bool = Field(description="Only workspace admins and owners may change the policy")


class ProjectAiPolicyUpdate(ApiModel):
    enabled: bool
    max_excerpt_lines: Annotated[int, Field(ge=10, le=400)] = 120
    version: int | None = Field(
        default=None, description="Current version for optimistic concurrency (omit on first save)"
    )


class AiRunCreate(ApiModel):
    kind: Literal["question", "finding_review", "file_review"]
    question: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
        | None
    ) = None
    finding_id: UUID | None = None
    paths: (
        Annotated[
            list[Annotated[str, StringConstraints(min_length=1, max_length=512)]],
            Field(min_length=1, max_length=5),
        ]
        | None
    ) = None
    snapshot_id: UUID | None = Field(
        default=None, description="Defaults to the project's latest upload (frozen snapshot)"
    )


class AiAnchorResponse(ApiModel):
    path: str
    start_line: int
    end_line: int
    quote: str
    status: str = Field(description="verified, unquoted, quote_mismatch, bad_range, unknown_path")
    sha256: str | None = Field(
        default=None, description="Content hash of the cited file (null when the path is unknown)"
    )


class AiAssessmentResponse(ApiModel):
    verdict: str
    explanation: str
    evidence_class: str
    anchors: list[AiAnchorResponse]


class AiAnswerResponse(ApiModel):
    type: Literal["answer", "review"]
    text: str
    abstained: bool = False
    uncertainty: str = ""
    inferred_intent: str = ""
    evidence_class: str | None = None
    citations: list[AiAnchorResponse] = []
    reviewed_paths: list[str] = []
    assessment: AiAssessmentResponse | None = None


class AiFindingResponse(ApiModel):
    id: UUID
    title: str
    category: str
    severity: str
    severity_rationale: str
    confidence: str
    evidence_class: str
    anchors: list[AiAnchorResponse]
    triggering_conditions: str
    impact: str
    recommendation: str
    validation_needed: str | None
    uncertainty: str | None
    related_finding_id: UUID | None


class AiUsageResponse(ApiModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    usage_reported: bool = True
    budget_tokens: int = 0
    cost_usd: float | None = None
    excerpts: int = 0


class AiStepResponse(ApiModel):
    n: int
    action: str
    detail: str
    outcome: str
    at: str


class AiRunResponse(ApiModel):
    id: UUID
    project_id: UUID
    snapshot_id: UUID
    scan_id: UUID | None
    finding_id: UUID | None
    kind: str
    state: str
    question: str | None
    target_paths: list[str] | None
    provider: str
    model: str
    prompt_version: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    usage: AiUsageResponse | None
    answer: AiAnswerResponse | None
    steps: list[AiStepResponse]
    limitations: list[str]
    findings: list[AiFindingResponse]


class AiRunPage(ApiModel):
    items: list[AiRunResponse]
