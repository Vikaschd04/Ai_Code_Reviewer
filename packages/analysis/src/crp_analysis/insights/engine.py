"""The insight engine (tools, always on): evidence in, recommendations out.

Inputs are the NFR assessment (questions with signals, gaps and team statements), the project's
tracked issues and the evidence signals of the newest reviewed upload. A curated guideline catalog
turns them into recommendations, each with an area, a priority, what was found, why it matters,
guided steps, and the evidence behind it. Nothing is inferred beyond the evidence: a missing
mechanism is reported as "not found in the upload", never as "absent from the system".

Areas group the NFR questionnaire's questions:
- security: security.*;
- reliability: reliability.consistency, availability.*, recoverability.*;
- performance: performance.*, scalability.*;
- architecture: operations.remediation (maintainability, structure);
- operations: operations.monitoring, reliability.glitches (observability);
- experience: usability.*, portability.*.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from crp_analysis.nfr.assessment import OPEN, Assessment, TrackedIssue
from crp_analysis.nfr.signals import Evidence

ENGINE_VERSION = "crp-insights-v1"


@dataclass(frozen=True, slots=True)
class Area:
    id: str
    name: str


AREAS: tuple[Area, ...] = (
    Area("security", "Security"),
    Area("reliability", "Reliability"),
    Area("performance", "Performance and scalability"),
    Area("architecture", "Architecture and maintainability"),
    Area("operations", "Operations and monitoring"),
    Area("experience", "Experience and portability"),
)
_AREA_OF_QUESTION = {
    "operations.remediation": "architecture",
    "operations.monitoring": "operations",
    "reliability.glitches": "operations",
}
_AREA_OF_ASPECT = {
    "security": "security",
    "reliability": "reliability",
    "availability": "reliability",
    "recoverability": "reliability",
    "performance": "performance",
    "scalability": "performance",
    "usability": "experience",
    "portability": "experience",
    "operations": "architecture",
}


def area_of(question_id: str) -> str:
    return _AREA_OF_QUESTION.get(question_id) or _AREA_OF_ASPECT[question_id.split(".", 1)[0]]


# -- guideline catalog --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssueGuideline:
    """Recommendation for open issues matching any of the selectors (first guideline wins)."""

    key: str
    area: str
    title: str
    why: str
    steps: tuple[str, ...]
    families: tuple[str, ...] = ()
    rule_prefixes: tuple[str, ...] = ()  # "engine:rule-prefix"
    categories: tuple[str, ...] = ()
    engines: tuple[str, ...] = ()

    def matches(self, issue: TrackedIssue) -> bool:
        return (
            (issue.family is not None and issue.family in self.families)
            or any(f"{issue.engine}:{issue.rule_id}".startswith(p) for p in self.rule_prefixes)
            or issue.category in self.categories
            or issue.engine in self.engines
        )


ISSUE_GUIDELINES: tuple[IssueGuideline, ...] = (
    IssueGuideline(
        "security.secrets",
        "security",
        "Secrets and keys are written in the code",
        "Anyone who can read the code, its history or a copy of it can use these credentials.",
        (
            "Move each secret to the platform's secret store or environment configuration.",
            "Rotate every secret that was committed: treat it as leaked.",
            "Keep a secret scan in the pipeline so new ones are caught before merge.",
        ),
        families=(
            "secrets.credentials",
            "secrets.hardcoded-token-secret",
            "crypto.hardcoded-key",
        ),
        rule_prefixes=("trivy:secret:",),
    ),
    IssueGuideline(
        "security.injection",
        "security",
        "Untrusted input can reach dangerous calls",
        "Injection (SQL, commands, code, HTML) lets an attacker read or change data or run code.",
        (
            "Use parameterised queries or the framework's query builder instead of building "
            "queries from strings.",
            "Never pass user input to eval, shell commands or HTML sinks; encode output for its "
            "context.",
            "Validate input at the boundary and add a test for each fixed case.",
        ),
        families=(
            "injection.sql",
            "command-injection.os",
            "code-injection.eval",
            "xss.dom-sink",
        ),
    ),
    IssueGuideline(
        "security.dependencies",
        "security",
        "Third-party libraries have known vulnerabilities",
        "Known vulnerabilities in dependencies are the easiest way in, because exploits are "
        "public.",
        (
            "Upgrade each affected library to a version that fixes the vulnerability.",
            "Remove libraries that are no longer used.",
            "Add automated dependency updates and a vulnerability check to the pipeline.",
        ),
        categories=("dependencies",),
    ),
    IssueGuideline(
        "security.access",
        "security",
        "Access checks or secure transport are missing",
        "Missing permission checks or plain-text connections expose data to the wrong people.",
        (
            "Enforce object and field permissions (or sharing rules) on every data access.",
            "Use encrypted transport and named credentials for outgoing calls.",
            "Keep sensitive data out of logs.",
        ),
        families=("transport.insecure", "secrets.logging"),
        rule_prefixes=("pmd-apex:ApexCRUDViolation", "pmd-apex:ApexSharingViolations"),
    ),
    IssueGuideline(
        "security.other",
        "security",
        "Other security weaknesses",
        "Weak cryptography and unsafe constructs make attacks easier.",
        (
            "Replace weak algorithms (MD5, SHA-1, ECB mode) with current ones.",
            "Fix the most severe findings first; each finding explains the safe alternative.",
        ),
        categories=("security",),
    ),
    IssueGuideline(
        "performance.loops",
        "performance",
        "Database work happens inside loops",
        "One query or save per item multiplies load and latency with data volume, and hits "
        "platform limits (Salesforce governor limits, SAP Commerce persistence).",
        (
            "Load what the loop needs in one query before it, and save in one batch after it.",
            "Keep queries and saves out of triggers' per-record logic (bulkify).",
        ),
        families=("performance.persistence-in-loop",),
    ),
    IssueGuideline(
        "performance.unbounded",
        "performance",
        "Queries can return unbounded results",
        "Queries without limits or paging slow down and run out of memory as data grows.",
        (
            "Add paging or an explicit limit to every list query.",
            "Return only the fields the caller needs.",
        ),
        families=("performance.unbounded-query",),
    ),
    IssueGuideline(
        "performance.code",
        "performance",
        "Costly operations in hot code",
        "Expensive operations repeated in loops waste CPU and memory.",
        ("Move costly work out of loops and reuse results.",),
        categories=("performance",),
    ),
    IssueGuideline(
        "architecture.cycles",
        "architecture",
        "Parts of the code depend on each other in cycles",
        "Parts in a cycle can only change, be tested and be released together.",
        (
            "Start with the cheapest cut named in the recommendation's issues.",
            "Move shared code into a part both may use, or invert one dependency with an "
            "interface owned by the part that should stay independent.",
        ),
        families=("maintainability.dependency-cycle", "reliability.dependency-cycle"),
    ),
    IssueGuideline(
        "architecture.hubs",
        "architecture",
        "Some parts are hubs that everything depends on",
        "Changes reach a hub from all sides and spread from it to all sides.",
        (
            "Split the hub by responsibility; begin with its busiest files.",
            "Keep new code out of the hub; give it a clear, small interface.",
        ),
        families=("maintainability.hub",),
    ),
    IssueGuideline(
        "architecture.layers",
        "architecture",
        "The code breaks your architecture rules",
        "Uses that skip or reverse your layers tie parts together that should stay independent.",
        (
            "Move the dependency downwards or behind an interface owned by the lower layer.",
            "If the use is intended for now, add an exception with a reason and an expiry date.",
        ),
        engines=("architecture",),
    ),
    IssueGuideline(
        "architecture.unstable",
        "architecture",
        "Stable parts depend on parts that change often",
        "Changes in the less stable parts ripple into everything that relies on the stable one.",
        ("Depend in the direction of stability: put the contract in the stable part.",),
        families=("maintainability.unstable-dependency",),
    ),
    IssueGuideline(
        "reliability.platform",
        "reliability",
        "Platform versions or jobs need attention",
        "Retired platform API versions and jobs that cannot stop cause failures on upgrades and "
        "incidents.",
        (
            "Raise retired API versions to a supported one and test the affected components.",
            "Make long-running jobs check for abort requests.",
        ),
        families=(
            "compatibility.api-version",
            "reliability.job-abort",
            "reliability.interceptor-side-effect",
        ),
    ),
    IssueGuideline(
        "reliability.defects",
        "reliability",
        "Defects that make behaviour unpredictable",
        "Swallowed errors and wrong comparisons hide failures and give wrong results.",
        (
            "Handle or rethrow errors; never leave a catch block empty.",
            "Fix the most severe defects first and add a test for each.",
        ),
        categories=("reliability", "correctness"),
    ),
    IssueGuideline(
        "architecture.code",
        "architecture",
        "Code that is harder to read and change",
        "Unused code, deprecated APIs and inconsistent style slow every change.",
        ("Clean up as you touch the code; fix the issues in files you change often first.",),
        categories=("maintainability", "coding_standards"),
    ),
)


@dataclass(frozen=True, slots=True)
class MissingGuideline:
    """Recommendation when the upload shows no supporting mechanism for a question."""

    key: str
    area: str
    question: str
    priority: str
    title: str
    why: str
    steps: tuple[str, ...]
    signal: str | None = None  # a specific signal must be absent (else: the question's evidence)


MISSING_GUIDELINES: tuple[MissingGuideline, ...] = (
    MissingGuideline(
        "operations.monitoring",
        "operations",
        "operations.monitoring",
        "medium",
        "No monitoring or metrics found in the upload",
        "Without metrics and alerts, problems are found by users first.",
        (
            "Add a metrics library (Micrometer, OpenTelemetry or prom-client) and expose health "
            "and readiness endpoints.",
            "Keep alert rules and dashboards with the code.",
            "If monitoring is configured outside this code, record it in the NFR questionnaire.",
        ),
    ),
    MissingGuideline(
        "operations.diagnostics",
        "operations",
        "reliability.glitches",
        "low",
        "No structured logging, tracing or error tracking found in the upload",
        "Without them, finding the cause of an incident takes much longer.",
        (
            "Log in a structured format with a request or correlation id.",
            "Add tracing (OpenTelemetry) and an error-tracking service.",
        ),
    ),
    MissingGuideline(
        "reliability.tests",
        "reliability",
        "reliability.consistency",
        "medium",
        "No automated tests found in the upload",
        "Without tests, every change can break behaviour unnoticed, and fixes cannot be verified.",
        (
            "Start with tests for the code you change most and for every fixed defect.",
            "Run the tests in a pipeline on every change.",
        ),
        signal="automated-tests",
    ),
    MissingGuideline(
        "reliability.recovery",
        "reliability",
        "recoverability.recovery-time",
        "low",
        "No health endpoints or automated pipeline found in the upload",
        "Health checks let the platform restart failed instances; a pipeline makes redeploying "
        "fast and repeatable.",
        (
            "Expose health and readiness endpoints (for example Spring Boot Actuator).",
            "Build, test and deploy through a pipeline kept with the code.",
        ),
    ),
    MissingGuideline(
        "reliability.fault-tolerance",
        "reliability",
        "availability.fault-tolerance",
        "low",
        "No fault handling for remote calls found in the upload",
        "If this system calls other services, one slow or failing service can take it down.",
        (
            "Wrap remote calls with timeouts, retries with backoff and circuit breakers "
            "(for example Resilience4j).",
            "Use queues for work that does not need an immediate answer.",
        ),
    ),
)


# -- insights ----------------------------------------------------------------------------------

_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_PRIORITY = {"high": 0, "medium": 1, "low": 2}
TOP_ISSUES = 5


@dataclass(slots=True)
class Insight:
    id: str
    area: str
    kind: str  # issues | missing | targets
    priority: str  # high | medium | low
    title: str
    summary: str
    why: str
    steps: tuple[str, ...]
    questions: tuple[str, ...]
    issues: list[TrackedIssue] = field(default_factory=list)  # every open issue it covers
    targets: tuple[str, ...] = ()  # missing team targets (kind targets)


@dataclass(slots=True)
class AreaHealth:
    area: Area
    state: str  # attention | improve | no_problems | unknown
    insights: dict[str, int]  # priority -> count
    questions: dict[str, int]  # NFR status -> count


@dataclass(slots=True)
class InsightReport:
    insights: list[Insight]
    areas: list[AreaHealth]
    reviewed: bool


def _priority(issues: list[TrackedIssue]) -> str:
    worst = min((_SEVERITY.get(i.severity, 3) for i in issues), default=3)
    return "high" if worst <= 1 else "medium" if worst == 2 else "low"


def _places(issues: list[TrackedIssue]) -> str:
    files = len({i.path for i in issues})
    count = len(issues)
    issue_text = f"{count} open issue{'s' if count != 1 else ''}"
    return f"{issue_text} in {files} file{'s' if files != 1 else ''}"


_TARGET_LABELS = {
    "availability_percent": "availability",
    "latency_p95_ms": "response time",
    "page_load_seconds": "page load time",
    "typical_users": "typical users",
    "peak_concurrent_users": "peak users",
    "rto_minutes": "recovery time (RTO)",
    "rpo_minutes": "acceptable data loss (RPO)",
    "growth": "expected growth",
    "downtime_cost": "cost of downtime",
    "accessibility": "accessibility level",
    "regulations": "regulations",
    "platforms": "platforms",
}


def build(
    assessment: Assessment, issues: list[TrackedIssue], evidence: list[Evidence]
) -> InsightReport:
    insights: list[Insight] = []
    open_issues = [i for i in issues if i.status in OPEN]
    grouped: dict[str, list[TrackedIssue]] = {}
    for issue in open_issues:
        guideline = next((g for g in ISSUE_GUIDELINES if g.matches(issue)), None)
        if guideline is not None:
            grouped.setdefault(guideline.key, []).append(issue)
    for guideline in ISSUE_GUIDELINES:
        matched = grouped.get(guideline.key)
        if not matched:
            continue
        severities = Counter(i.severity for i in matched)
        mix = ", ".join(
            f"{severities[s]} {s}" for s in ("critical", "high", "medium", "low") if severities[s]
        )
        insights.append(
            Insight(
                id=guideline.key,
                area=guideline.area,
                kind="issues",
                priority=_priority(matched),
                title=guideline.title,
                summary=f"{_places(matched)} ({mix}).",
                why=guideline.why,
                steps=guideline.steps,
                questions=(),
                issues=sorted(
                    matched, key=lambda i: (_SEVERITY.get(i.severity, 9), i.path, i.title)
                ),
            )
        )
    if assessment.reviewed:
        status = {r.question.id: r for a in assessment.aspects for r in a.questions}
        present = {e.signal for e in evidence if e.kind == "supports"}
        for missing in MISSING_GUIDELINES:
            result = status.get(missing.question)
            if result is None or result.status == "not_applicable":
                continue
            absent = missing.signal not in present if missing.signal else not result.evidence
            if not absent or (result.answer and result.answer.text):
                continue  # evidence found, or the team explained how it is handled
            insights.append(
                Insight(
                    id=missing.key,
                    area=missing.area,
                    kind="missing",
                    priority=missing.priority,
                    title=missing.title,
                    summary="Nothing in the uploaded code or configuration shows it.",
                    why=missing.why,
                    steps=missing.steps,
                    questions=(missing.question,),
                )
            )
    # Targets only the team can give, per area.
    needed: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for aspect in assessment.aspects:
        for result in aspect.questions:
            if result.status == "needs_input":
                needed.setdefault(area_of(result.question.id), []).append(
                    (result.question.id, result.question.profile_fields)
                )
    for area_id, entries in needed.items():
        fields = tuple(dict.fromkeys(f for _, fs in entries for f in fs))
        labels = [_TARGET_LABELS.get(f, f) for f in fields] or ["your answers"]
        insights.append(
            Insight(
                id=f"targets.{area_id}",
                area=area_id,
                kind="targets",
                priority="medium",
                title="Tell us your targets",
                summary=f"Needed to judge this area: {', '.join(labels)}.",
                why="Evidence in code shows intent; only your targets say whether it is enough.",
                steps=("Enter the targets in the NFR questionnaire (Insights).",),
                questions=tuple(q for q, _ in entries),
                targets=fields,
            )
        )
    insights.sort(key=lambda i: (_PRIORITY[i.priority], [a.id for a in AREAS].index(i.area), i.id))
    areas: list[AreaHealth] = []
    for area in AREAS:
        mine = [i for i in insights if i.area == area.id]
        counts = Counter(i.priority for i in mine)
        questions = Counter(
            r.status
            for a in assessment.aspects
            for r in a.questions
            if area_of(r.question.id) == area.id
        )
        if counts["high"]:
            state = "attention"
        elif counts["medium"] or counts["low"]:
            state = "improve"
        elif questions["evidence"] or questions["answered"]:
            state = "no_problems"
        else:
            state = "unknown"
        areas.append(
            AreaHealth(
                area,
                state,
                {p: counts.get(p, 0) for p in ("high", "medium", "low")},
                dict(questions),
            )
        )
    return InsightReport(insights, areas, assessment.reviewed)


# -- the advisor's fact sheet ---------------------------------------------------------------------


def facts(report: InsightReport, evidence: list[Evidence]) -> list[dict[str, object]]:
    """Numbered facts the advisor agent may cite: recommendations, their most severe issues, the
    mechanisms found, and missing targets. Everything is copied from the tools' output."""
    sheet: list[dict[str, object]] = []

    def add(text: str, **refs: object) -> None:
        sheet.append({"id": f"F{len(sheet) + 1}", "text": text[:600], **refs})

    for insight in report.insights:
        add(
            f"Recommendation {insight.id} ({insight.area}, priority {insight.priority}): "
            f"{insight.title}. {insight.summary}",
            insight=insight.id,
        )
        for issue in insight.issues[:TOP_ISSUES]:
            add(
                f"Issue in {insight.id}: {issue.title} ({issue.severity}) at {issue.path}",
                insight=insight.id,
                issue=issue.id,
                path=issue.path,
            )
    for item in evidence:
        if item.kind != "supports":
            continue
        where = ", ".join(f"{p}{f':{n}' if n else ''}" for p, n, _ in item.locations[:2])
        add(f"Found in the upload: {item.label} ({where})", signal=item.signal)
    return sheet
