"""NFR checkpoints (ADR 0024): every catalog rule lands in one checkpoint, statuses follow the
evidence and never claim more than the upload shows, steps fit the project's stack, and the
advisor's fact sheet copies the tools' output. Team decisions are validated."""

from __future__ import annotations

import pytest

from crp_analysis.catalog import all_rules
from crp_analysis.insights import decisions
from crp_analysis.insights.engine import (
    AREAS,
    CHECKPOINTS,
    ReviewContext,
    TrackedIssue,
    build,
    checkpoint_for,
    facts,
    review_context,
)
from crp_analysis.nfr.signals import LibraryUse, detect

ALL_RAN = {e: "SUCCEEDED" for e in ("pmd", "eslint", "opengrep", "trivy", "smells", "nfr")}


def _issue(
    engine: str,
    rule: str,
    category: str,
    severity: str = "medium",
    family: str | None = None,
    path: str = "src/A.java",
    status: str = "OPEN",
) -> TrackedIssue:
    return TrackedIssue(
        f"i-{engine}-{rule}-{path}",
        engine,
        rule,
        category,
        family,
        severity,
        f"T {rule}",
        path,
        status,
    )


def _context(**kw: object) -> ReviewContext:
    values: dict[str, object] = {"reviewed": True, "engines": ALL_RAN, "kubernetes": False}
    values.update(kw)
    return ReviewContext(**values)  # type: ignore[arg-type]


def _by_id(report):  # type: ignore[no-untyped-def]
    return {r.checkpoint.id: r for r in report.checkpoints}


def _owner(issue: TrackedIssue) -> str:
    checkpoint = checkpoint_for(issue)
    assert checkpoint is not None
    return checkpoint.id


def test_every_catalog_rule_lands_in_one_checkpoint() -> None:
    for key, info in all_rules().items():
        issue = _issue(info.engine, info.rule_id, info.category.value, family=info.family)
        assert checkpoint_for(issue) is not None, key
    # First match wins: specific checkpoints before the catch-all categories.
    assert _owner(_issue("trivy", "secret:github-pat", "security")) == "security.secrets"
    assert _owner(_issue("trivy", "misconfig:KSV-0011", "security")) == "performance.capacity"
    assert _owner(_issue("trivy", "misconfig:KSV-0017", "security")) == "security.configuration"
    assert _owner(_issue("trivy", "CVE-2021-44228", "dependencies")) == "security.dependencies"
    rules = all_rules()
    for rule, expected in (
        ("nfr:crp.nfr.k8s.single-replica", "reliability.instances"),
        ("nfr:crp.nfr.k8s.no-readiness-probe", "reliability.health"),
        ("nfr:crp.nfr.k8s.recreate-strategy", "reliability.rollouts"),
        ("nfr:crp.nfr.spring.schema-auto-update", "reliability.data"),
        ("nfr:crp.nfr.spring.actuator-exposed", "security.configuration"),
        ("frameworks:crp.sf.metadata.retired-api-version", "reliability.platform"),
        ("frameworks:crp.sap.extension.dependency-cycle", "architecture.cycles"),
        ("smells:crp.arch.hub", "architecture.hubs"),
    ):
        info = rules[rule]
        issue = _issue(info.engine, info.rule_id, info.category.value, family=info.family)
        assert _owner(issue) == expected, rule
    assert {c.area for c in CHECKPOINTS} == {a.id for a in AREAS}


def test_statuses_follow_the_evidence() -> None:
    issues = [
        _issue("opengrep", "crp.java.sqli", "security", "critical", "injection.sql", "src/R.java"),
        _issue("eslint", "no-eval", "security", "high", "code-injection.eval", "web/a.js"),
        _issue("pmd", "EmptyCatchBlock", "reliability", "low", status="RESOLVED"),
    ]
    paths = ["Dockerfile", ".github/workflows/ci.yml", "src/test/java/ATest.java"]
    libraries = [LibraryUse("maven", "io.micrometer:micrometer-core", "pom.xml", 12)]
    report = build(issues, detect(paths, libraries), _context())
    found = _by_id(report)
    injection = found["security.injection"]
    assert (injection.status, injection.priority) == ("attention", "high")
    assert injection.summary == "2 open issues in 2 files (1 critical, 1 high)."
    assert found["reliability.errors"].status == "no_issues"  # the resolved issue is not a gap
    monitoring = found["operations.monitoring"]
    assert monitoring.status == "in_place"
    assert monitoring.evidence[0].locations == [("pom.xml", 12, "io.micrometer:micrometer-core")]
    assert found["operations.diagnostics"].status == "missing"
    assert found["operations.diagnostics"].priority == "low"
    assert found["reliability.tests"].status == "in_place"
    assert found["operations.delivery"].status == "in_place"
    assert found["reliability.instances"].status == "not_applicable"  # no Kubernetes
    assert found["architecture.layers"].status == "not_applicable"  # no rules set
    assert found["experience.api"].status == "not_applicable"  # no HTTP framework
    # Failing first, by priority.
    assert [r.checkpoint.id for r in report.checkpoints[:2]] == [
        "security.injection",
        "reliability.health",
    ]
    assert all(r.status in {"attention", "missing"} for r in report.failing())
    areas = {a.area.id: a for a in report.areas}
    assert areas["security"].state == "attention"
    assert areas["operations"].state == "improve"
    assert areas["architecture"].state == "no_problems"


def test_checks_that_did_not_run_are_not_clean() -> None:
    engines = {**ALL_RAN, "trivy": "UNAVAILABLE", "pmd": "PARTIAL", "eslint": "FAILED"}
    found = _by_id(build([], [], _context(engines=engines, kubernetes=None)))
    assert found["security.dependencies"].status == "not_checked"  # only Trivy looks for it
    assert found["reliability.errors"].summary == (
        "No open issues from the checks; some files could not be checked."
    )
    # The configuration checks did not run: Kubernetes checkpoints are not checked, not clean.
    assert found["reliability.instances"].status == "not_checked"
    assert found["performance.autoscaling"].status == "not_checked"
    nothing = build([], [], ReviewContext(reviewed=False))
    assert {r.status for r in nothing.checkpoints} == {"not_checked"}
    assert {a.state for a in nothing.areas} == {"unknown"}


def test_team_decisions_count_only_while_the_mechanism_is_missing() -> None:
    handled = {"operations.monitoring": "Datadog agent on every node"}
    found = _by_id(build([], [], _context(), handled))
    monitoring = found["operations.monitoring"]
    assert (
        monitoring.status == "handled"
        and monitoring.handled_reason == handled["operations.monitoring"]
    )
    evidence = detect([], [LibraryUse("npm", "prom-client", "package.json", 3)])
    assert _by_id(build([], evidence, _context(), handled))["operations.monitoring"].status == (
        "in_place"
    )


def test_kubernetes_checkpoints_use_configuration_evidence() -> None:
    stored = [
        {"signal": "autoscaling", "count": 1, "locations": [["k8s/hpa.yaml", 2, "HPA api"]]},
        {"signal": "k8s-probes", "count": 2, "locations": [["k8s/api.yaml", 20, "api: r"]]},
    ]
    evidence = detect(["k8s/api.yaml"], [], stored)
    issues = [
        _issue(
            "nfr",
            "crp.nfr.k8s.single-replica",
            "reliability",
            "medium",
            "availability.single-instance",
            "k8s/api.yaml",
        ),
        _issue("trivy", "misconfig:KSV-0016", "security", "low", path="k8s/api.yaml"),
    ]
    found = _by_id(build(issues, evidence, _context(kubernetes=True, configuration=True)))
    assert found["reliability.instances"].status == "attention"
    assert found["performance.autoscaling"].status == "in_place"
    assert found["reliability.health"].status == "in_place"
    assert found["performance.capacity"].status == "attention"
    assert found["reliability.rollouts"].status == "no_issues"


def test_steps_fit_the_stack() -> None:
    spring = _context(stacks=frozenset({"spring"}))
    steps = _by_id(build([], [], spring))["operations.monitoring"].steps
    assert steps[0].startswith(
        "Add spring-boot-starter-actuator with micrometer-registry-prometheus"
    )
    plain = _by_id(build([], [], _context()))["operations.monitoring"].steps
    assert plain[0].startswith("Expose metrics")


def test_review_context_from_engine_runs_and_libraries() -> None:
    runs = {
        "nfr": ("PARTIAL", {"workloads": 2, "spring_files": 1}),
        "trivy": ("SUCCEEDED", {"config_files": 3}),
        "frameworks": ("NOT_APPLICABLE", {}),
        "pmd-apex": ("SUCCEEDED", {}),
        "architecture": ("NOT_APPLICABLE", {}),
    }
    libraries = [
        LibraryUse("maven", "org.springframework.boot:spring-boot-starter-web", "pom.xml", 5),
        LibraryUse("npm", "react", "web/package.json", 4),
    ]
    context = review_context(runs, libraries, ["pom.xml", "web/package.json"])
    assert context.kubernetes is True and context.configuration is True
    assert context.platform is True and context.rules is False
    assert context.web_ui and context.http_api
    assert context.stacks == {"spring", "node", "kubernetes"}
    unknown = review_context({"nfr": ("FAILED", None)}, [], [])
    assert unknown.kubernetes is None and unknown.configuration is None


def test_fact_sheet_copies_the_tools_output() -> None:
    issues = [_issue("opengrep", "crp.java.sqli", "security", "critical", "injection.sql")]
    evidence = detect([], [LibraryUse("maven", "io.micrometer:micrometer-core", "pom.xml", 12)])
    sheet = facts(build(issues, evidence, _context(), {"operations.diagnostics": "ELK stack"}))
    texts = [str(f["text"]) for f in sheet]
    assert texts[0].startswith(
        "Checkpoint security.injection (security, needs attention, priority high): "
        "Untrusted input cannot reach dangerous calls. 1 open issue in 1 file (1 critical)."
    )
    assert sheet[0]["insight"] == "security.injection"
    assert any(
        t == "Issue in security.injection: T crp.java.sqli (critical) at src/A.java" for t in texts
    )
    assert "Found in the upload: Metrics library (pom.xml:12)" in texts
    assert any(t.startswith("Handled outside the code (team statement)") for t in texts)
    assert [f["id"] for f in sheet] == [f"F{i}" for i in range(1, len(sheet) + 1)]


def test_decisions_are_validated_and_old_documents_hold_none() -> None:
    handled = decisions.with_handled({}, "operations.monitoring", "  Platform   monitoring  ")
    assert handled == {"operations.monitoring": "Platform monitoring"}
    document = decisions.document(handled)
    assert decisions.handled_from(document) == handled
    assert len(decisions.sha256(document)) == 64
    assert decisions.with_handled(handled, "operations.monitoring", None) == {}
    with pytest.raises(decisions.DecisionError, match="missing mechanism"):
        decisions.with_handled({}, "security.injection", "we are careful")
    with pytest.raises(decisions.DecisionError, match="3 to 500"):
        decisions.with_handled({}, "operations.monitoring", "no")
    # Documents of the retired questionnaire carry no decisions.
    assert decisions.handled_from({"targets": {"rto_minutes": 30}, "answers": {}}) == {}
