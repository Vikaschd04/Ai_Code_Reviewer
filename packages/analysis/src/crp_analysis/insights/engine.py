"""NFR checkpoints (tools, always on): what the uploaded project shows for each non-functional
requirement, and how to resolve what needs work (ADR 0024; replaces the NFR questionnaire).

A checkpoint is one concrete, code-checkable requirement in an area (security, reliability,
performance and scalability, operations, architecture, experience). Its status comes only from
evidence in the newest reviewed upload:

- ``attention``: open tracked issues violate it (priority from the most severe issue);
- ``missing``: the mechanism it needs is not found in the upload (catalog priority);
- ``handled``: not found in the code, and the team recorded that it is handled elsewhere;
- ``in_place``: the mechanism is found (file and line) and no issue is open;
- ``no_issues``: the checks that look for it ran and report nothing open;
- ``not_checked``: the checks that look for it did not run, or there is no review yet;
- ``not_applicable``: the upload has nothing it applies to (for example no Kubernetes).

"Not found in the upload" never means "absent from the system", and "no issues" never means
"verified at run time"; the screens say so.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from crp_analysis.nfr.signals import Evidence, LibraryUse

ENGINE_VERSION = "crp-insights-v3"
OPEN = frozenset({"OPEN", "TRIAGED", "FIX_PROPOSED"})
COMPLETED = frozenset({"SUCCEEDED", "PARTIAL", "NOT_APPLICABLE"})
# Trivy's Kubernetes checks for CPU and memory requests and limits.
CAPACITY_CHECKS = frozenset(
    {"misconfig:KSV-0011", "misconfig:KSV-0015", "misconfig:KSV-0016", "misconfig:KSV-0018"}
)
FAILING = frozenset({"attention", "missing"})
PASSED = frozenset({"in_place", "handled", "no_issues"})
STATUSES = (
    "attention",
    "missing",
    "not_checked",
    "in_place",
    "handled",
    "no_issues",
    "not_applicable",
)
TOP_ISSUES = 5


@dataclass(frozen=True, slots=True)
class TrackedIssue:
    id: str
    engine: str
    rule_id: str
    category: str
    family: str | None
    severity: str
    title: str
    path: str
    status: str


@dataclass(frozen=True, slots=True)
class Area:
    id: str
    name: str


AREAS: tuple[Area, ...] = (
    Area("security", "Security"),
    Area("reliability", "Reliability and availability"),
    Area("performance", "Performance and scalability"),
    Area("operations", "Operations and monitoring"),
    Area("architecture", "Architecture and maintainability"),
    Area("experience", "Experience and portability"),
)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    id: str
    area: str
    title: str  # the requirement, phrased as the goal
    why: str
    steps: tuple[str, ...]  # how to resolve it
    families: tuple[str, ...] = ()
    rule_prefixes: tuple[str, ...] = ()  # "engine:rule-prefix"
    categories: tuple[str, ...] = ()
    engines: tuple[str, ...] = ()
    checked_by: tuple[str, ...] = ()  # engines whose completed run means the code was checked
    signals: tuple[str, ...] = ()  # evidence that the mechanism is in place
    missing: str | None = None  # priority when nothing is found; None: absence is no problem
    requires: str | None = None  # kubernetes | configuration | platform | rules | web-ui | http-api
    stack_steps: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (stack, steps) for that stack

    def matches(self, issue: TrackedIssue) -> bool:
        return (
            (issue.family is not None and issue.family in self.families)
            or any(f"{issue.engine}:{issue.rule_id}".startswith(p) for p in self.rule_prefixes)
            or issue.category in self.categories
            or issue.engine in self.engines
        )


_CODE = ("pmd", "eslint", "opengrep", "pmd-apex")

# An issue belongs to the first checkpoint that matches it by family, rule or engine; checkpoints
# that match whole categories are catch-alls and are tried last (``checkpoint_for``).
CHECKPOINTS: tuple[Checkpoint, ...] = (
    Checkpoint(
        "security.secrets",
        "security",
        "No secrets in the code",
        "Anyone who can read the code, its history or a copy of it can use these credentials.",
        (
            "Move each secret to the platform's secret store or environment configuration.",
            "Rotate every secret that was committed: treat it as leaked.",
            "Keep a secret scan in the pipeline so new ones are caught before merge.",
        ),
        families=("secrets.credentials", "secrets.hardcoded-token-secret", "crypto.hardcoded-key"),
        rule_prefixes=("trivy:secret:",),
        checked_by=("trivy", "opengrep"),
    ),
    Checkpoint(
        "security.injection",
        "security",
        "Untrusted input cannot reach dangerous calls",
        "Injection (SQL, commands, code, HTML) lets an attacker read or change data or run code.",
        (
            "Use parameterised queries or the framework's query builder instead of building "
            "queries from strings.",
            "Never pass user input to eval, shell commands or HTML sinks; encode output for its "
            "context.",
            "Validate input at the boundary and add a test for each fixed case.",
        ),
        families=("injection.sql", "command-injection.os", "code-injection.eval", "xss.dom-sink"),
        checked_by=_CODE,
    ),
    Checkpoint(
        "security.dependencies",
        "security",
        "Libraries have no known vulnerabilities",
        "Known vulnerabilities in dependencies are the easiest way in, because exploits are "
        "public.",
        (
            "Upgrade each affected library to a version that fixes the vulnerability.",
            "Remove libraries that are no longer used.",
            "Add automated dependency updates and a vulnerability check to the pipeline.",
        ),
        categories=("dependencies",),
        checked_by=("trivy",),
    ),
    Checkpoint(
        "security.access",
        "security",
        "Data access is checked and connections are encrypted",
        "Missing permission checks or plain-text connections expose data to the wrong people.",
        (
            "Enforce object and field permissions (or sharing rules) on every data access.",
            "Use encrypted transport and named credentials for outgoing calls.",
            "Keep sensitive data out of logs.",
        ),
        families=("transport.insecure", "secrets.logging"),
        rule_prefixes=("pmd-apex:ApexCRUDViolation", "pmd-apex:ApexSharingViolations"),
        checked_by=("opengrep", "pmd-apex"),
        signals=("authentication",),
    ),
    Checkpoint(
        "performance.capacity",
        "performance",
        "Containers declare CPU and memory requests and limits",
        "Without requests the scheduler cannot place instances reliably and CPU-based "
        "autoscaling has no baseline; without memory limits one instance can starve the others.",
        (
            "Set CPU and memory requests from measured usage, and a memory limit.",
            "Use the requests as the baseline for autoscaling on CPU utilisation.",
            "Revisit the values after load tests.",
        ),
        rule_prefixes=tuple(sorted(f"trivy:{check}" for check in CAPACITY_CHECKS)),
        checked_by=("trivy",),
        requires="kubernetes",
    ),
    Checkpoint(
        "security.configuration",
        "security",
        "Containers and settings are configured securely",
        "Root or privileged containers, writable file systems and exposed management endpoints "
        "turn one weakness into control of the host or the data.",
        (
            "Run containers as a non-root user with a read-only root file system, no privilege "
            "escalation and all capabilities dropped.",
            "Expose only the health and info Actuator endpoints; require authentication for the "
            "others.",
            "Each issue names the setting to change.",
        ),
        families=("security.actuator-exposure",),
        rule_prefixes=("trivy:misconfig:",),
        checked_by=("trivy", "nfr"),
        requires="configuration",
    ),
    Checkpoint(
        "security.other",
        "security",
        "No weak cryptography or unsafe constructs",
        "Weak cryptography and unsafe constructs make attacks easier.",
        (
            "Replace weak algorithms (MD5, SHA-1, ECB mode) with current ones.",
            "Fix the most severe findings first; each finding explains the safe alternative.",
        ),
        categories=("security",),
        checked_by=_CODE,
    ),
    Checkpoint(
        "reliability.instances",
        "reliability",
        "More than one instance runs",
        "With one instance, every crash, node failure or release is an outage.",
        (
            "Run at least two replicas, or an autoscaler with a minimum of two.",
            "Spread the instances across nodes or zones.",
            "If another repository sets the replica count, mark it as handled there.",
        ),
        families=("availability.single-instance",),
        checked_by=("nfr",),
        signals=("multiple-instances", "multi-zone"),
        requires="kubernetes",
    ),
    Checkpoint(
        "reliability.health",
        "reliability",
        "Health checks restart and gate instances",
        "Health checks let the platform stop sending traffic to instances that are not ready and "
        "restart those that hang.",
        (
            "Expose health and readiness endpoints.",
            "Give every container that serves traffic a readiness probe.",
        ),
        families=("availability.probes",),
        checked_by=("nfr",),
        signals=("k8s-probes", "health-endpoints"),
        missing="low",
        stack_steps=(
            (
                "spring",
                (
                    "Add spring-boot-starter-actuator and set "
                    "management.endpoint.health.probes.enabled=true.",
                ),
            ),
            (
                "kubernetes",
                ("Point the readiness probe at the readiness endpoint, not at a static page.",),
            ),
        ),
    ),
    Checkpoint(
        "reliability.rollouts",
        "reliability",
        "Releases and maintenance do not stop the service",
        "A rollout that stops every instance, or a node drain without a disruption budget, "
        "causes planned downtime.",
        (
            "Use rolling updates instead of Recreate.",
            "Add a PodDisruptionBudget so maintenance keeps instances running.",
            "Shut down gracefully so requests in progress finish.",
        ),
        families=("availability.downtime-deploy",),
        checked_by=("nfr",),
        signals=("disruption-budget", "graceful-shutdown"),
        requires="kubernetes",
    ),
    Checkpoint(
        "reliability.fault-tolerance",
        "reliability",
        "Remote calls are protected (timeouts, retries, circuit breakers)",
        "If this system calls other services, one slow or failing service can take it down.",
        (
            "Set connect and read timeouts on every outgoing call.",
            "Retry only safe calls, with backoff; add circuit breakers around remote services.",
            "Use queues for work that does not need an immediate answer.",
        ),
        families=("reliability.no-timeout",),
        checked_by=("opengrep",),
        signals=("circuit-breakers", "retries", "timeouts", "messaging"),
        missing="low",
        stack_steps=(
            (
                "spring",
                ("Use Resilience4j (circuit breaker, retry, time limiter) with Spring Boot.",),
            ),
            ("node", ("Pass a timeout to fetch or axios; use opossum or cockatiel for breakers.",)),
        ),
    ),
    Checkpoint(
        "reliability.data",
        "reliability",
        "Database changes are versioned and recoverable",
        "Automatic schema changes cannot be reviewed or rolled back, and create or create-drop "
        "delete the data.",
        (
            "Manage the schema with versioned migrations (Flyway or Liquibase).",
            "Set spring.jpa.hibernate.ddl-auto to validate or none outside development.",
            "Keep backups with a retention that matches your recovery needs.",
        ),
        families=("data.schema-auto-ddl",),
        checked_by=("nfr",),
        signals=("db-migrations", "backups"),
    ),
    Checkpoint(
        "reliability.platform",
        "reliability",
        "Platform versions and jobs are supported",
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
        checked_by=("frameworks", "pmd-apex", "opengrep"),
        requires="platform",
    ),
    Checkpoint(
        "reliability.errors",
        "reliability",
        "Errors are handled and behaviour is predictable",
        "Swallowed errors and wrong comparisons hide failures and give wrong results.",
        (
            "Handle or rethrow errors; never leave a catch block empty.",
            "Fix the most severe defects first and add a test for each.",
        ),
        categories=("reliability", "correctness"),
        checked_by=_CODE,
    ),
    Checkpoint(
        "reliability.tests",
        "reliability",
        "Automated tests protect behaviour",
        "Without tests, every change can break behaviour unnoticed, and fixes cannot be verified.",
        (
            "Start with tests for the code you change most and for every fixed defect.",
            "Run the tests in a pipeline on every change.",
        ),
        signals=("automated-tests",),
        missing="medium",
        stack_steps=(
            ("spring", ("Add spring-boot-starter-test and test the services and controllers.",)),
            ("node", ("Add a test runner such as Vitest or Jest.",)),
        ),
    ),
    Checkpoint(
        "performance.database",
        "performance",
        "Database access stays efficient as data grows",
        "One query per item and queries without limits multiply load and latency with data "
        "volume, and hit platform limits.",
        (
            "Load what a loop needs in one query before it, and save in one batch after it.",
            "Add paging or an explicit limit to every list query.",
            "Size the connection pool and cache data that is read often.",
        ),
        families=("performance.persistence-in-loop", "performance.unbounded-query"),
        checked_by=_CODE,
        signals=("connection-pool", "caching"),
    ),
    Checkpoint(
        "performance.code",
        "performance",
        "No costly operations in hot code",
        "Expensive operations repeated in loops waste CPU and memory.",
        ("Move costly work out of loops and reuse results.",),
        categories=("performance",),
        checked_by=_CODE,
    ),
    Checkpoint(
        "performance.autoscaling",
        "performance",
        "Capacity follows demand (autoscaling)",
        "Without autoscaling, peaks overload a fixed number of instances and quiet periods waste "
        "capacity.",
        (
            "Add a HorizontalPodAutoscaler (autoscaling/v2) with a minimum of two replicas.",
            "Scale on CPU from the containers' requests, or on queue length for workers.",
        ),
        signals=("autoscaling",),
        missing="low",
        requires="kubernetes",
    ),
    Checkpoint(
        "operations.monitoring",
        "operations",
        "Metrics and alerts are in place",
        "Without metrics and alerts, problems are found by users first.",
        (
            "Expose metrics and keep alert rules and dashboards with the code.",
            "If monitoring is set up outside this code, mark it as handled elsewhere.",
        ),
        signals=("metrics", "alerting"),
        missing="medium",
        stack_steps=(
            (
                "spring",
                (
                    "Add spring-boot-starter-actuator with micrometer-registry-prometheus and "
                    "expose the prometheus endpoint.",
                ),
            ),
            ("node", ("Add prom-client and expose a /metrics endpoint.",)),
        ),
    ),
    Checkpoint(
        "operations.diagnostics",
        "operations",
        "Incidents can be diagnosed (logs, tracing, error tracking)",
        "Without structured logs, traces and error tracking, finding the cause of an incident "
        "takes much longer.",
        (
            "Log in a structured format with a request or correlation id.",
            "Add tracing (OpenTelemetry) and an error-tracking service.",
        ),
        signals=("structured-logging", "tracing", "error-tracking"),
        missing="low",
        stack_steps=(
            ("spring", ("Use logstash-logback-encoder for JSON logs and Micrometer Tracing.",)),
            ("node", ("Use pino for JSON logs and @opentelemetry/sdk-node for traces.",)),
        ),
    ),
    Checkpoint(
        "operations.delivery",
        "operations",
        "Changes ship through an automated pipeline",
        "A pipeline makes builds, tests and redeploys fast and repeatable, which shortens "
        "recovery.",
        (
            "Build, test and deploy through a pipeline kept with the code.",
            "Keep a runbook for deploying and rolling back.",
        ),
        signals=("ci-pipeline", "runbooks"),
        missing="low",
    ),
    Checkpoint(
        "architecture.cycles",
        "architecture",
        "Parts do not depend on each other in cycles",
        "Parts in a cycle can only change, be tested and be released together.",
        (
            "Start with the cheapest cut named in the issues.",
            "Move shared code into a part both may use, or invert one dependency with an "
            "interface owned by the part that should stay independent.",
        ),
        families=("maintainability.dependency-cycle", "reliability.dependency-cycle"),
        checked_by=("smells", "frameworks"),
    ),
    Checkpoint(
        "architecture.hubs",
        "architecture",
        "No part is a hub that everything depends on",
        "Changes reach a hub from all sides and spread from it to all sides.",
        (
            "Split the hub by responsibility; begin with its busiest files.",
            "Keep new code out of the hub; give it a clear, small interface.",
        ),
        families=("maintainability.hub",),
        checked_by=("smells",),
    ),
    Checkpoint(
        "architecture.unstable",
        "architecture",
        "Stable parts do not depend on parts that change often",
        "Changes in the less stable parts ripple into everything that relies on the stable one.",
        ("Depend in the direction of stability: put the contract in the stable part.",),
        families=("maintainability.unstable-dependency",),
        checked_by=("smells",),
    ),
    Checkpoint(
        "architecture.layers",
        "architecture",
        "The code follows your architecture rules",
        "Uses that skip or reverse your layers tie parts together that should stay independent.",
        (
            "Move the dependency downwards or behind an interface owned by the lower layer.",
            "If the use is intended for now, add an exception with a reason and an expiry date.",
        ),
        engines=("architecture",),
        checked_by=("architecture",),
        requires="rules",
    ),
    Checkpoint(
        "architecture.code",
        "architecture",
        "Code is easy to read and change",
        "Unused code, deprecated APIs and inconsistent style slow every change.",
        ("Clean up as you touch the code; fix the issues in files you change often first.",),
        categories=("maintainability", "coding_standards"),
        checked_by=("pmd", "eslint", "pmd-apex"),
    ),
    Checkpoint(
        "experience.accessibility",
        "experience",
        "Accessibility is checked in the build",
        "Without automated checks, screens that keyboard and screen-reader users cannot use reach "
        "production unnoticed.",
        (
            "Add an accessibility lint plugin (for example eslint-plugin-jsx-a11y) and axe checks "
            "in the UI tests.",
            "Fix the reported problems and keep the checks in the pipeline.",
        ),
        signals=("accessibility-checks",),
        missing="low",
        requires="web-ui",
    ),
    Checkpoint(
        "experience.api",
        "experience",
        "APIs are described for their consumers",
        "An API description (OpenAPI) lets other teams integrate without reading the code and "
        "makes breaking changes visible.",
        (
            "Publish an OpenAPI description, generated from the code or kept with it.",
            "Version the API and check changes against the description in the pipeline.",
        ),
        signals=("api-specs", "api-docs-library"),
        missing="low",
        requires="http-api",
        stack_steps=(("spring", ("Add springdoc-openapi to generate the description.",)),),
    ),
)
BY_ID = {c.id: c for c in CHECKPOINTS}
MISSING_CAPABLE = frozenset(c.id for c in CHECKPOINTS if c.missing)
_MATCH_ORDER = sorted(CHECKPOINTS, key=lambda c: bool(c.categories))  # stable: catch-alls last


def checkpoint_for(issue: TrackedIssue) -> Checkpoint | None:
    """The checkpoint an open issue counts against."""
    return next((c for c in _MATCH_ORDER if c.matches(issue)), None)


# -- what the review shows about the project ------------------------------------------------------

_WEB_UI = frozenset({"react", "react-dom", "vue", "@angular/core", "svelte", "preact", "next"})
_HTTP_NPM = frozenset({"express", "fastify", "koa", "@nestjs/core", "@hapi/hapi"})
_HTTP_MAVEN = (
    "org.springframework.boot:spring-boot-starter-web",
    "org.springframework.boot:spring-boot-starter-webflux",
    "io.quarkus:",
    "io.micronaut:",
    "jakarta.ws.rs:",
    "javax.ws.rs:",
)


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """What the newest reviewed upload shows about the project (from its engine runs)."""

    reviewed: bool
    engines: Mapping[str, str] = field(default_factory=dict)  # engine -> run state
    kubernetes: bool | None = None  # None: the configuration checks did not run
    configuration: bool | None = None
    platform: bool = False
    rules: bool = False
    web_ui: bool = False
    http_api: bool = False
    stacks: frozenset[str] = frozenset()


def review_context(
    runs: Mapping[str, tuple[str, Mapping[str, object] | None]],
    libraries: Iterable[LibraryUse],
    paths: Iterable[str],
) -> ReviewContext:
    """``runs``: engine -> (state, diagnostics) of the reviewed upload's scan."""
    engines = {name: state for name, (state, _) in runs.items()}

    def number(engine: str, key: str) -> int | None:
        state, diagnostics = runs.get(engine, ("", None))
        if state not in COMPLETED:
            return None
        value = (diagnostics or {}).get(key)
        return value if isinstance(value, int) else 0

    workloads = number("nfr", "workloads")
    spring_files = number("nfr", "spring_files")
    config_files = number("trivy", "config_files")
    known = [v for v in (workloads, spring_files, config_files) if v is not None]
    uses = list(libraries)
    names = {(u.ecosystem, u.name) for u in uses}
    spring = any(e == "maven" and n.startswith("org.springframework.boot:") for e, n in names)
    node = any(e == "npm" for e, _ in names) or any(
        PurePosixPath(p).name == "package.json" for p in paths
    )
    kubernetes = None if workloads is None else workloads > 0
    stacks = {s for s, on in (("spring", spring), ("node", node), ("kubernetes", kubernetes)) if on}
    return ReviewContext(
        reviewed=True,
        engines=engines,
        kubernetes=kubernetes,
        configuration=any(v > 0 for v in known) if known else None,
        platform=any(
            engines.get(e) in {"SUCCEEDED", "PARTIAL"} for e in ("frameworks", "pmd-apex")
        ),
        rules=engines.get("architecture") in {"SUCCEEDED", "PARTIAL"},
        web_ui=any(e == "npm" and n in _WEB_UI for e, n in names),
        http_api=any(e == "npm" and n in _HTTP_NPM for e, n in names)
        or any(e == "maven" and n.startswith(_HTTP_MAVEN) for e, n in names),
        stacks=frozenset(stacks),
    )


# -- checkpoint results ---------------------------------------------------------------------------

_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_PRIORITY = {"high": 0, "medium": 1, "low": 2}
_NOT_APPLICABLE = {
    "kubernetes": "No Kubernetes workloads in the upload.",
    "configuration": "No container, deployment or application configuration in the upload.",
    "platform": "No Salesforce or SAP Commerce code in the upload.",
    "rules": "No architecture rules are set (Insights, Architecture).",
    "web-ui": "No web user interface in the upload.",
    "http-api": "No HTTP API framework in the upload.",
}


@dataclass(slots=True)
class CheckpointResult:
    checkpoint: Checkpoint
    status: str
    priority: str | None  # high | medium | low, for failing checkpoints
    summary: str
    steps: tuple[str, ...]
    issues: list[TrackedIssue] = field(default_factory=list)  # every open issue it covers
    evidence: list[Evidence] = field(default_factory=list)
    handled_reason: str | None = None


@dataclass(slots=True)
class AreaHealth:
    area: Area
    state: str  # attention | improve | no_problems | unknown
    counts: dict[str, int]  # checkpoint status -> count


@dataclass(slots=True)
class Report:
    checkpoints: list[CheckpointResult]  # failing first (by priority), then the rest
    areas: list[AreaHealth]
    reviewed: bool

    def failing(self) -> list[CheckpointResult]:
        return [c for c in self.checkpoints if c.status in FAILING]


def _priority(issues: list[TrackedIssue]) -> str:
    worst = min((_SEVERITY.get(i.severity, 3) for i in issues), default=3)
    return "high" if worst <= 1 else "medium" if worst == 2 else "low"


def _issue_summary(issues: list[TrackedIssue]) -> str:
    files = len({i.path for i in issues})
    severities = Counter(i.severity for i in issues)
    mix = ", ".join(
        f"{severities[s]} {s}" for s in ("critical", "high", "medium", "low") if severities[s]
    )
    count = len(issues)
    return (
        f"{count} open issue{'s' if count != 1 else ''} in {files} "
        f"file{'s' if files != 1 else ''}" + (f" ({mix})." if mix else ".")
    )


def _applies(requires: str | None, context: ReviewContext) -> bool | None:
    if requires is None:
        return True
    return {
        "kubernetes": context.kubernetes,
        "configuration": context.configuration,
        "platform": context.platform,
        "rules": context.rules,
        "web-ui": context.web_ui,
        "http-api": context.http_api,
    }[requires]


def _steps(checkpoint: Checkpoint, context: ReviewContext) -> tuple[str, ...]:
    extra = tuple(
        s for stack, steps in checkpoint.stack_steps if stack in context.stacks for s in steps
    )
    return extra + checkpoint.steps


def _evaluate(
    checkpoint: Checkpoint,
    issues: list[TrackedIssue],
    evidence: list[Evidence],
    context: ReviewContext,
    handled: Mapping[str, str],
) -> CheckpointResult:
    steps = _steps(checkpoint, context)

    def result(status: str, summary: str, priority: str | None = None) -> CheckpointResult:
        return CheckpointResult(checkpoint, status, priority, summary, steps, issues, evidence)

    if not context.reviewed:
        return result("not_checked", "Review an upload to check it.")
    if issues:
        return result("attention", _issue_summary(issues), _priority(issues))
    applies = _applies(checkpoint.requires, context)
    if not evidence and applies is False:
        return result("not_applicable", _NOT_APPLICABLE[checkpoint.requires or ""])
    if not evidence and applies is None:
        return result(
            "not_checked", "Review the upload again to check its deployment configuration."
        )
    if evidence:
        return result("in_place", "Found in the upload.")
    if checkpoint.missing:
        reason = handled.get(checkpoint.id)
        if reason:
            done = result("handled", "Handled outside this code, as your team recorded.")
            done.handled_reason = reason
            return done
        return result(
            "missing", "Nothing in the uploaded code or configuration shows it.", checkpoint.missing
        )
    states = [context.engines.get(e) for e in checkpoint.checked_by]
    if any(s in COMPLETED for s in states):
        partly = any(s == "PARTIAL" for s in states)
        return result(
            "no_issues",
            "No open issues from the checks"
            + ("; some files could not be checked." if partly else "."),
        )
    return result("not_checked", "The checks that look for it did not run in the latest review.")


def build(
    issues: Iterable[TrackedIssue],
    evidence: Iterable[Evidence],
    context: ReviewContext,
    handled: Mapping[str, str] | None = None,
) -> Report:
    """Every checkpoint with its status, from open issues, evidence and the team's decisions."""
    grouped: dict[str, list[TrackedIssue]] = {c.id: [] for c in CHECKPOINTS}
    for issue in issues:
        if issue.status not in OPEN:
            continue
        checkpoint = checkpoint_for(issue)
        if checkpoint is not None:
            grouped[checkpoint.id].append(issue)
    found = {e.signal: e for e in evidence if e.kind == "supports"}
    results = []
    for checkpoint in CHECKPOINTS:
        matched = sorted(
            grouped[checkpoint.id], key=lambda i: (_SEVERITY.get(i.severity, 9), i.path, i.title)
        )
        shown = [found[s] for s in checkpoint.signals if s in found]
        results.append(_evaluate(checkpoint, matched, shown, context, handled or {}))
    area_order = [a.id for a in AREAS]
    catalog_order = {c.id: i for i, c in enumerate(CHECKPOINTS)}
    results.sort(
        key=lambda r: (
            r.status not in FAILING,
            _PRIORITY.get(r.priority or "", 3),
            area_order.index(r.checkpoint.area),
            catalog_order[r.checkpoint.id],
        )
    )
    areas = []
    for area in AREAS:
        mine = [r for r in results if r.checkpoint.area == area.id]
        counts = Counter(r.status for r in mine)
        if any(r.status in FAILING and r.priority == "high" for r in mine):
            state = "attention"
        elif any(r.status in FAILING for r in mine):
            state = "improve"
        elif any(r.status in PASSED for r in mine):
            state = "no_problems"
        else:
            state = "unknown"
        areas.append(AreaHealth(area, state, {s: counts.get(s, 0) for s in STATUSES}))
    return Report(results, areas, context.reviewed)


# -- the advisor's fact sheet ---------------------------------------------------------------------

_STATUS_TEXT = {"attention": "needs attention", "missing": "not found in the upload"}


def facts(report: Report) -> list[dict[str, object]]:
    """Numbered facts the advisor agent may cite: checkpoints that need work, their most severe
    issues, mechanisms found and the team's decisions. Everything is copied from the tools."""
    sheet: list[dict[str, object]] = []

    def add(text: str, **refs: object) -> None:
        sheet.append({"id": f"F{len(sheet) + 1}", "text": text[:600], **refs})

    for result in report.failing():
        add(
            f"Checkpoint {result.checkpoint.id} ({result.checkpoint.area}, "
            f"{_STATUS_TEXT[result.status]}, priority {result.priority}): "
            f"{result.checkpoint.title}. {result.summary}",
            insight=result.checkpoint.id,
        )
        for issue in result.issues[:TOP_ISSUES]:
            add(
                f"Issue in {result.checkpoint.id}: {issue.title} ({issue.severity}) "
                f"at {issue.path}",
                insight=result.checkpoint.id,
                issue=issue.id,
                path=issue.path,
            )
    for result in report.checkpoints:
        if result.status == "in_place":
            for item in result.evidence:
                where = ", ".join(f"{p}{f':{n}' if n else ''}" for p, n, _ in item.locations[:2])
                add(f"Found in the upload: {item.label} ({where})", signal=item.signal)
        elif result.status == "handled":
            add(
                f"Handled outside the code (team statement): {result.checkpoint.title}: "
                f"{result.handled_reason}",
                insight=result.checkpoint.id,
            )
    return sheet
