"""Registry of product capabilities and their real availability.

The UI renders navigation from this list so future features appear disabled with a reason
instead of as active buttons without a backend. Update an entry only when the capability's
implementation and validation evidence exist.
"""

from __future__ import annotations

from crp_api.schemas import Capability, CapabilityState

_A = CapabilityState.AVAILABLE
_P = CapabilityState.PLANNED

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="readiness",
        label="Service readiness",
        state=_A,
        phase="P00",
        reason="Live checks of PostgreSQL, Temporal, worker and artifact storage",
    ),
    Capability(
        id="workflow_diagnostic",
        label="Workflow diagnostic",
        state=_A,
        phase="P00",
        reason="Runs a real diagnostic workflow; performs no code analysis",
    ),
    Capability(
        id="projects",
        label="Projects",
        state=_A,
        phase="P00",
        reason="Create and list workspace-scoped projects",
    ),
    Capability(
        id="zip_intake",
        label="ZIP upload",
        state=_A,
        phase="P01",
        reason="Streamed, bounded ZIP upload with safe extraction and a frozen manifest",
    ),
    Capability(
        id="local_runner_capture",
        label="Local folder capture",
        state=_A,
        phase="P01",
        reason="crp-runner capture uploads an explicitly selected folder after local exclusions",
    ),
    Capability(
        id="baseline_analysis",
        label="Scans and findings",
        state=_A,
        phase="P01",
        reason="PMD, ESLint, Opengrep (owned rules) and Trivy (offline dependencies/secrets) "
        "with per-file coverage, exact evidence and cross-engine correlation",
    ),
    Capability(
        id="issue_lifecycle",
        label="Issues",
        state=_A,
        phase="P02",
        reason="Durable issues with triage, exceptions with expiry and strict recheck states",
    ),
    Capability(
        id="scan_comparison",
        label="Compare scans",
        state=_A,
        phase="P02",
        reason="New / unchanged / verified-absent / not-rechecked / unknown / rule-obsolete",
    ),
    Capability(
        id="exports",
        label="JSON and SARIF export",
        state=_A,
        phase="P02",
        reason="Schema-validated JSON export and SARIF 2.1.0",
    ),
    Capability(
        id="architecture_graph",
        label="Architecture",
        state=_A,
        phase="P02",
        reason="Snapshot graph of modules, files, types and relations with evidence and "
        "resolved/declared/inferred/unresolved classification; syntax-level only",
    ),
    Capability(
        id="ai_investigation",
        label="AI investigation",
        state=_P,
        phase="P03",
        reason="Requires Phase 3 and an approved provider and data-egress policy",
    ),
    Capability(
        id="fix_workbench",
        label="Fix workbench",
        state=_P,
        phase="P05",
        reason="Validated patch proposals arrive in Phase 5",
    ),
    Capability(
        id="git_integration",
        label="Git integration",
        state=_P,
        phase="P06",
        reason="Git connector and pull-request analysis arrive in Phase 6",
    ),
)
