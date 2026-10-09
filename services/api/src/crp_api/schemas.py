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
from crp_core.domain.states import (
    CheckFailThreshold,
    CodeReviewKind,
    MembershipRole,
    ProjectOrigin,
)
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
    git_ref: str | None = None
    git_provider: str | None = None
    git_repository: str | None = None
    git_tree_sha: str | None = None
    git_capture: dict[str, object] | None = Field(
        default=None, description="How a commit capture was checked against the commit's tree"
    )
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


class AiFixStepResponse(ApiModel):
    id: str
    label: str
    state: Literal["passed", "failed", "not_run"]
    detail: str


class AiFixCandidateResponse(ApiModel):
    index: int
    title: str
    explanation: str
    behaviour_note: str
    confidence: str
    label: str = Field(description="Always shown with the candidate (AI provenance, not verified)")
    patch: str | None = Field(description="Unified diff against the file as it was requested")
    changed_lines: int
    problems: list[str]
    steps: list[AiFixStepResponse] = Field(description="The P05 checks run on a copy")
    passed: bool
    summary: str
    applicable: bool = Field(description="Exact lines, policy, parse and original check passed")
    reason: str | None = Field(description="Why it cannot be applied (plain language)")
    applied_at: datetime | None


class AiFixResult(ApiModel):
    text: str
    abstained: bool
    uncertainty: str
    path: str
    base_sha256: str = Field(description="The file text the candidates were made for")
    candidates: list[AiFixCandidateResponse]


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
    change_set_id: UUID | None = Field(default=None, description="Fix runs: their workspace")
    fix: AiFixResult | None = Field(default=None, description="Fix runs: checked candidates")
    steps: list[AiStepResponse]
    limitations: list[str]
    findings: list[AiFindingResponse]


class AiRunPage(ApiModel):
    items: list[AiRunResponse]


# -- validated fixes (P05) --------------------------------------------------------------------


class FixOption(ApiModel):
    recipe_id: str
    title: str
    available: bool
    reason: str | None = Field(description="Why no fix can be prepared for this occurrence")


class FixOptions(ApiModel):
    finding_id: UUID
    options: list[FixOption]


class FixCreate(ApiModel):
    recipe_id: Annotated[str, Field(min_length=1, max_length=64)]


class FixEditUpdate(ApiModel):
    start_line: Annotated[int, Field(ge=1)]
    replacement: Annotated[list[Annotated[str, Field(max_length=2000)]], Field(max_length=200)]


class FixEditsUpdate(ApiModel):
    version: int
    edits: Annotated[list[FixEditUpdate], Field(min_length=1, max_length=20)]


class FixReject(ApiModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class FixRebase(ApiModel):
    snapshot_id: UUID


class FixEditResponse(ApiModel):
    path: str
    start_line: int
    end_line: int
    original: list[str]
    replacement: list[str]


class FixStepResponse(ApiModel):
    id: str
    label: str
    state: Literal["passed", "failed", "not_run"]
    detail: str


class FixValidationResponse(ApiModel):
    id: UUID
    proposal_id: UUID
    state: str
    patch_sha256: str
    result_sha256: str
    current: bool = Field(description="Whether it validated the proposal's current patch")
    steps: list[FixStepResponse]
    summary: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested_at: datetime | None


class FixFindingSummary(ApiModel):
    id: UUID
    title: str
    engine: str
    rule_id: str
    severity: str
    start_line: int | None


class FixPullRequestResponse(ApiModel):
    number: int
    url: str
    repository: str
    branch: str
    base_ref: str
    base_sha: str
    commit_sha: str
    created_at: datetime


class FixProposalResponse(ApiModel):
    id: UUID
    project_id: UUID
    snapshot_id: UUID
    scan_id: UUID
    finding_id: UUID
    kind: str
    recipe_id: str
    title: str
    explanation: str
    behaviour_note: str | None
    state: str
    path: str
    base_sha256: str
    result_sha256: str
    patch_sha256: str
    patch: str
    edits: list[FixEditResponse]
    changed_lines: int
    edited: bool
    validations_used: int
    max_validations: int
    rejected_reason: str | None
    created_at: datetime
    updated_at: datetime
    version: int
    finding: FixFindingSummary | None
    latest_validation: FixValidationResponse | None
    labels: list[str] = Field(description="Plain statements of what was and was not verified")
    pull_request: FixPullRequestResponse | None = None
    pull_request_available: bool = False
    pull_request_reason: str | None = Field(
        default=None, description="Why a pull request cannot be opened (plain language)"
    )


class FixProposalPage(ApiModel):
    items: list[FixProposalResponse]


# -- GitHub (P06) ------------------------------------------------------------------------------


class GitHubStatus(ApiModel):
    available: bool = Field(description="Reviews and publication can authenticate as the app")
    linking_available: bool
    webhooks_available: bool
    reason: str | None
    admin_hint: str | None = Field(description="Setup steps; only for workspace admins")
    install_url: str | None


class GitHubLinkStart(ApiModel):
    authorize_url: str = Field(description="GitHub page that asks the admin to confirm access")


class GitHubLinkComplete(ApiModel):
    code: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    state: Annotated[str, StringConstraints(min_length=20, max_length=200)]


class GitRepositoryResponse(ApiModel):
    id: UUID
    full_name: str
    default_branch: str
    private: bool
    archived: bool
    removed: bool
    project_id: UUID | None
    project_name: str | None


class GitInstallationResponse(ApiModel):
    id: UUID
    account: str
    account_type: str
    repository_selection: str | None
    suspended: bool
    revoked: bool
    linked_at: datetime
    synced_at: datetime | None
    repositories: list[GitRepositoryResponse]


class GitLinkSkip(ApiModel):
    account: str
    reason: str


class GitHubLinkResult(ApiModel):
    linked: list[GitInstallationResponse]
    skipped: list[GitLinkSkip]


class GitInstallationList(ApiModel):
    items: list[GitInstallationResponse]


class GitConnectionCreate(ApiModel):
    repository_id: UUID


class GitConnectionUpdate(ApiModel):
    version: int = Field(ge=1)
    review_pushes: bool | None = None
    review_pull_requests: bool | None = None
    review_forks: bool | None = None
    publish_checks: bool | None = None
    publish_pull_requests: bool | None = None
    check_fail_threshold: CheckFailThreshold | None = None
    reconcile_days: int | None = Field(default=None, ge=1, le=90)


class GitConnectionResponse(ApiModel):
    connected: bool
    can_edit: bool
    id: UUID | None = None
    status: (
        Literal["active", "access_removed", "installation_revoked", "installation_suspended"] | None
    ) = None
    status_reason: str | None = None
    repository: GitRepositoryResponse | None = None
    installation_account: str | None = None
    review_pushes: bool = True
    review_pull_requests: bool = True
    review_forks: bool = False
    publish_checks: bool = False
    publish_pull_requests: bool = False
    check_fail_threshold: CheckFailThreshold = CheckFailThreshold.NEVER
    reconcile_days: int = 7
    last_full_review_at: datetime | None = None
    version: int | None = None
    created_at: datetime | None = None


class CodeReviewCreate(ApiModel):
    kind: CodeReviewKind = CodeReviewKind.BRANCH
    pull_request: int | None = Field(default=None, ge=1, le=10_000_000)
    full: bool = Field(default=False, description="Re-run every check instead of reusing results")


class CodeReviewResponse(ApiModel):
    id: UUID
    project_id: UUID
    repository: str | None
    kind: str
    trigger: str
    state: str
    ref: str | None
    head_sha: str | None
    pr_number: int | None
    pr_title: str | None
    pr_author: str | None
    pr_url: str | None
    fork: bool
    base_ref: str | None
    base_sha: str | None
    merge_base_sha: str | None
    full: bool
    head_snapshot_id: UUID | None
    base_snapshot_id: UUID | None
    head_scan_id: UUID | None
    base_scan_id: UUID | None
    changes: dict[str, object] | None
    result: dict[str, object] | None
    publish_state: str | None
    publish_error: str | None
    published_at: datetime | None
    superseded_by: UUID | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class CodeReviewPage(ApiModel):
    items: list[CodeReviewResponse]


# -- fix workspaces (P08) ----------------------------------------------------------------------


class ChangeSetCreate(ApiModel):
    snapshot_id: UUID | None = Field(default=None, description="Default: the latest upload")
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None


class ChangeSetFileSummary(ApiModel):
    path: str
    action: str
    language: str | None
    size_bytes: int | None
    line_count: int | None
    flags: list[str]
    sources: list[str]
    updated_at: datetime


class ChangeSetEventResponse(ApiModel):
    source: str
    path: str | None
    action: str | None
    finding_ids: list[str]
    recipe_id: str | None
    summary: str
    flags: list[str]
    created_at: datetime


class ChangeSetCheckResponse(ApiModel):
    id: UUID
    change_set_id: UUID
    state: str
    content_sha256: str
    current: bool = Field(description="The workspace has not changed since this check")
    snapshot_id: UUID | None
    scan_id: UUID | None
    base_scan_id: UUID | None
    result: dict[str, object] | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ChangeSetResponse(ApiModel):
    state: Literal["draft", "checking", "ready", "exported"] = Field(
        description="ready: checked as it is now; exported: downloaded as it is now"
    )
    id: UUID
    project_id: UUID
    title: str
    base_snapshot_id: UUID
    base_name: str
    base_git_commit: str | None
    base_scan_id: UUID | None
    content_sha256: str
    version: int
    can_edit: bool
    ai: WorkspaceAiStatus
    pull_request: WorkspacePullRequestState
    files: list[ChangeSetFileSummary]
    latest_check: ChangeSetCheckResponse | None
    events: list[ChangeSetEventResponse]
    created_at: datetime
    updated_at: datetime


class WorkspaceAiStatus(ApiModel):
    available: bool = Field(description="AI fix suggestions can be requested in this workspace")
    reason: str | None = Field(description="Why not (plain language)")


class WorkspacePullRequest(ApiModel):
    number: int
    url: str
    repository: str
    branch: str
    base_ref: str
    base_sha: str
    commit_sha: str
    content_sha256: str
    current: bool = Field(description="Opened for the workspace content as it is now")
    created_at: datetime


class WorkspacePullRequestState(ApiModel):
    applies: bool = Field(description="The upload came from a connected GitHub repository")
    available: bool = Field(description="A pull request can be opened for the current content")
    reason: str | None = Field(description="Why not (plain language)")
    opened: list[WorkspacePullRequest]


class WorkspaceAiFixRequest(ApiModel):
    finding_id: UUID


class WorkspaceAiFixApply(ApiModel):
    version: int = Field(ge=1)
    candidate: int = Field(ge=0, le=4)


class ChangeSetListItem(ApiModel):
    id: UUID
    project_id: UUID
    title: str
    base_snapshot_id: UUID
    base_name: str
    files_changed: int
    latest_check_state: str | None
    updated_at: datetime


class ChangeSetList(ApiModel):
    items: list[ChangeSetListItem]


class WorkspaceFileContent(ApiModel):
    path: str
    action: str | None = Field(description="None when the file is unchanged in the workspace")
    editable: bool
    reason: str | None = Field(description="Why the file cannot be edited (plain language)")
    language: str | None
    base_sha256: str | None
    base_content: str | None
    sha256: str | None
    content: str | None
    line_ending: Literal["lf", "crlf"]
    flags: list[str]


class WorkspaceFileSave(ApiModel):
    version: int = Field(ge=1)
    path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    content: str
    finding_ids: list[UUID] = Field(default_factory=list, max_length=200)


class WorkspaceFilePath(ApiModel):
    version: int = Field(ge=1)
    path: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class WorkspaceSaveResult(ApiModel):
    change_set: ChangeSetResponse
    flags: list[str] = Field(description="Policy flags of this save (information, not refusal)")


class WorkspaceFixRequest(ApiModel):
    version: int = Field(ge=1)
    finding_ids: list[UUID] = Field(default_factory=list, max_length=500)
    engine: Annotated[str, StringConstraints(max_length=32)] | None = None
    rule_id: Annotated[str, StringConstraints(max_length=128)] | None = Field(
        default=None, description="With engine: every occurrence of this rule in the upload"
    )


class WorkspaceFixApplied(ApiModel):
    finding_id: UUID
    path: str
    recipe_id: str
    title: str


class WorkspaceFixSkipped(ApiModel):
    finding_id: UUID
    path: str | None
    reason: str


class WorkspaceFixResult(ApiModel):
    applied: list[WorkspaceFixApplied]
    skipped: list[WorkspaceFixSkipped]
    change_set: ChangeSetResponse


class WorkspaceIssue(ApiModel):
    finding_id: UUID
    title: str
    severity: str
    category: str
    engine: str
    rule_id: str
    path: str
    line: int | None
    recipe_available: bool
    changed: bool = Field(description="The finding's file was changed in the workspace")
    outcome: str | None = Field(description="From the latest check (see check `current`)")


class WorkspaceIssuePage(ApiModel):
    items: list[WorkspaceIssue]
    total: int
    next_cursor: str | None


class SnapshotChange(ApiModel):
    path: str
    status: Literal["added", "modified", "removed", "renamed"]
    previous_path: str | None


class SnapshotComparisonResponse(ApiModel):
    base_snapshot_id: UUID
    snapshot_id: UUID
    counts: dict[str, int]
    changes: list[SnapshotChange]
    truncated: bool


class FileComparison(ApiModel):
    path: str
    previous_path: str | None
    before: str | None
    after: str | None
    before_sha256: str | None
    after_sha256: str | None
    note: str | None = Field(description="Why a side is missing (binary, not stored, absent)")


class ArchitectureComponent(ApiModel):
    key: str = Field(description="Java package or folder")
    kind: Literal["package", "folder"]
    files: int
    lines: int
    types: int
    abstract_types: int
    afferent: int = Field(description="Ca: files outside that depend on this component")
    efferent: int = Field(description="Ce: files inside that depend on other components")
    fan_in: int
    fan_out: int
    instability: float | None = Field(description="Ce / (Ca + Ce); null without dependencies")
    abstractness: float | None = Field(description="Abstract types / types; null without types")
    distance: float | None = Field(description="|A + I - 1|; null when A or I is undefined")
    zone: Literal["pain", "uselessness"] | None
    in_cycle: bool


class ArchitectureEdge(ApiModel):
    source: str
    target: str
    weight: int = Field(description="Distinct file-level dependencies")


class ArchitectureCycle(ApiModel):
    components: list[str] = Field(description="Up to 50 members (see component_count)")
    component_count: int
    edges: list[ArchitectureEdge] = Field(description="Up to 50, heaviest first")
    edge_count: int
    cut: list[ArchitectureEdge] = Field(
        description="Dependencies to remove; up to 50, lightest first"
    )
    cut_count: int
    cut_weight: int = Field(description="File-level dependencies to change for the whole cut")
    exact: bool = Field(description="False when the cut is a heuristic (large cycles)")


class ArchitectureSummary(ApiModel):
    components: int
    dependencies: int = Field(description="Distinct file-level dependencies between components")
    component_edges: int
    cycles: int
    components_in_cycles: int
    zone_of_pain: int
    zone_of_uselessness: int
    test_files: int = Field(description="Test files left out of the model")
    generated_files: int = Field(
        default=0, description="Generated files (gensrc, generated-sources) left out of the model"
    )
    not_counted: int = Field(description="Graph edges not counted (unresolved, external)")
    average_distance: float | None


class ArchitectureMetricsResponse(ApiModel):
    build_id: UUID
    extractor: str
    algorithm: str
    summary: ArchitectureSummary
    components: list[ArchitectureComponent]
    edges: list[ArchitectureEdge]
    edges_truncated: bool
    cycles: list[ArchitectureCycle]
    notes: list[str]


# -- architecture rules (P10 slice 2; ADR 0019) -------------------------------------------------

_RuleSeverity = Literal["critical", "high", "medium", "low"]


class ArchitectureLayerDocument(ApiModel):
    name: str = Field(max_length=40)
    match: list[Annotated[str, StringConstraints(max_length=200)]] = Field(
        max_length=20,
        description="Patterns over parts (Java packages or folders): * within one name part, "
        "** any number of parts",
    )
    description: str | None = Field(default=None, max_length=300)


class ArchitectureForbidDocument(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str | None = Field(
        default=None, max_length=48, description="Stable rule key (derived when omitted)"
    )
    source: str = Field(alias="from", max_length=300, description="A layer name or a pattern")
    target: str = Field(alias="to", max_length=300, description="A layer name or a pattern")
    reason: str | None = Field(default=None, max_length=300)
    severity: _RuleSeverity = "medium"


class ArchitectureAllowDocument(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source: str = Field(alias="from", max_length=300)
    target: str = Field(alias="to", max_length=300)
    reason: str = Field(max_length=300)
    until: str | None = Field(
        default=None, description="Expiry date (YYYY-MM-DD); expired exceptions stop applying"
    )


class ArchitectureRulesDocument(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    rules_schema: Literal["crp-architecture-rules-v1"] = Field(
        default="crp-architecture-rules-v1", alias="schema"
    )
    layers: list[ArchitectureLayerDocument] = Field(
        default_factory=list, max_length=30, description="Top to bottom"
    )
    layering: Literal["lower", "next", "none"] = Field(
        default="lower",
        description="lower: a layer may use any layer below it; next: only the one directly "
        "below; none: layers only name groups",
    )
    severity: _RuleSeverity = Field(default="medium", description="Severity of layering breaches")
    forbid: list[ArchitectureForbidDocument] = Field(default_factory=list, max_length=100)
    allow: list[ArchitectureAllowDocument] = Field(
        default_factory=list, max_length=100, description="Exceptions to layering"
    )


class ArchitectureRuleVersionSummary(ApiModel):
    version: int
    sha256: str
    source: Literal["editor", "yaml"]
    note: str | None
    created_at: datetime
    created_by: str | None = Field(description="Display name of the author")
    layers: int
    forbid: int
    allow: int


class ArchitectureRulesResponse(ApiModel):
    project_id: UUID
    version: int = Field(description="0 when no rules were saved")
    current_version: int = Field(description="The version the next review applies")
    sha256: str | None
    document: ArchitectureRulesDocument | None
    rule_ids: list[str]
    note: str | None
    created_at: datetime | None
    created_by: str | None
    can_edit: bool
    history: list[ArchitectureRuleVersionSummary] = Field(description="Newest first, up to 50")


class ArchitectureRulesUpdate(ApiModel):
    document: ArchitectureRulesDocument | None = Field(
        default=None, description="The rules as JSON (give this or yaml)"
    )
    yaml: str | None = Field(
        default=None, max_length=65536, description="The rules as YAML (give this or document)"
    )
    note: str | None = Field(default=None, max_length=500, description="Why the rules changed")
    base_version: int = Field(
        ge=0, description="The version you edited (0 when none); a newer one is a conflict"
    )


class ArchitectureRulesCheckRequest(ApiModel):
    document: ArchitectureRulesDocument | None = None
    yaml: str | None = Field(default=None, max_length=65536)


class ArchitectureLayerParts(ApiModel):
    name: str
    parts: list[str] = Field(description="Up to 50")
    part_count: int


class ArchitectureViolationResponse(ApiModel):
    rule_id: str
    title: str
    severity: _RuleSeverity
    path: str
    line: int | None
    target: str
    source_component: str
    target_component: str
    source_layer: str | None
    target_layer: str | None
    message: str


class ArchitectureRulesCheckResponse(ApiModel):
    build_id: UUID
    rules_sha256: str
    rules_version: int | None = Field(description="Null when unsaved rules were checked")
    layers: list[ArchitectureLayerParts]
    unassigned: list[str] = Field(description="Parts in no layer (up to 50)")
    unassigned_count: int
    overlaps: dict[str, list[str]] = Field(description="Parts matching more than one layer")
    violations: list[ArchitectureViolationResponse] = Field(description="Up to 200")
    violation_count: int
    by_rule: dict[str, int]
    allowed_by_exception: int
    expired_exceptions: list[str]
    dependencies_checked: int
    notes: list[str]
