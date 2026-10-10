"""Insight engine (tools): every catalog rule lands in one recommendation, priorities follow the
evidence, missing mechanisms are reported only when nothing shows them, and the advisor's fact
sheet copies the tools' output."""

from __future__ import annotations

from crp_analysis.catalog import all_rules
from crp_analysis.insights.engine import AREAS, ISSUE_GUIDELINES, area_of, build, facts
from crp_analysis.nfr.assessment import TrackedIssue, assess
from crp_analysis.nfr.profile import Profile, from_document
from crp_analysis.nfr.questionnaire import load
from crp_analysis.nfr.signals import LibraryUse, detect


def _issue(
    engine: str,
    rule: str,
    category: str,
    severity: str,
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


def _report(
    issues: list[TrackedIssue],
    paths: list[str],
    libraries: list[LibraryUse],
    profile: Profile,
    reviewed: bool = True,
):  # type: ignore[no-untyped-def]
    evidence = detect(paths, libraries)
    assessment = assess(load(), evidence, issues, profile, reviewed=reviewed)
    return build(assessment, issues, evidence), evidence


def test_every_catalog_rule_lands_in_one_recommendation() -> None:
    for info in all_rules().values():
        issue = _issue(info.engine, info.rule_id, info.category.value, "medium", info.family)
        assert any(g.matches(issue) for g in ISSUE_GUIDELINES), f"{info.engine}:{info.rule_id}"
    first = {
        ("trivy", "secret:aws-access-key-id", "security", None): "security.secrets",
        ("pmd", "HardCodedCryptoKey", "security", "crypto.hardcoded-key"): "security.secrets",
        ("pmd-apex", "ApexCRUDViolation", "security", None): "security.access",
        ("trivy", "CVE-2024-1", "dependencies", None): "security.dependencies",
        (
            "frameworks",
            "crp.sap.extension.dependency-cycle",
            "reliability",
            "reliability.dependency-cycle",
        ): "architecture.cycles",
        (
            "frameworks",
            "crp.sf.metadata.retired-api-version",
            "reliability",
            "compatibility.api-version",
        ): "reliability.platform",
        ("architecture", "arch.layers", "maintainability", None): "architecture.layers",
        (
            "smells",
            "crp.arch.unstable-dependency",
            "maintainability",
            "maintainability.unstable-dependency",
        ): "architecture.unstable",
        ("eslint", "no-empty", "reliability", None): "reliability.defects",
    }
    for (engine, rule, category, family), key in first.items():
        issue = _issue(engine, rule, category, "high", family)
        assert next(g.key for g in ISSUE_GUIDELINES if g.matches(issue)) == key
    assert {area_of(q.id) for q in load().questions} == {a.id for a in AREAS}


def test_recommendations_follow_the_evidence() -> None:
    issues = [
        _issue(
            "opengrep",
            "crp.java.sql-injection.string-concat",
            "security",
            "critical",
            "injection.sql",
        ),
        _issue("eslint", "no-eval", "security", "high", "code-injection.eval", path="web/a.js"),
        _issue("trivy", "CVE-2024-1", "dependencies", "high", path="pom.xml"),
        _issue(
            "pmd-apex",
            "OperationWithLimitsInLoop",
            "performance",
            "medium",
            "performance.persistence-in-loop",
        ),
        _issue(
            "smells",
            "crp.arch.cycle",
            "maintainability",
            "medium",
            "maintainability.dependency-cycle",
        ),
        _issue("eslint", "no-empty", "reliability", "medium", path="web/a.js"),
        _issue(
            "eslint", "no-unused-vars", "maintainability", "low", "maintainability.unused-variable"
        ),
        _issue("pmd", "Fixed", "correctness", "high", status="RESOLVED"),  # not open: ignored
    ]
    report, _ = _report(
        issues,
        ["src/test/java/ATest.java"],
        [LibraryUse("maven", "io.micrometer:micrometer-core", "pom.xml", 12)],
        from_document({"targets": {"availability_percent": 99.9}}),
    )
    by_id = {i.id: i for i in report.insights}
    injection = by_id["security.injection"]
    assert injection.priority == "high" and len(injection.issues) == 2
    assert injection.summary == "2 open issues in 2 files (1 critical, 1 high)."
    assert injection.issues[0].severity == "critical"  # most severe first
    assert by_id["security.dependencies"].priority == "high"
    assert by_id["performance.loops"].priority == "medium"
    assert by_id["architecture.cycles"].area == "architecture"
    assert by_id["reliability.defects"].summary == "1 open issue in 1 file (1 medium)."
    assert by_id["architecture.code"].priority == "low"
    # Monitoring is declared (Micrometer) and tests exist: no "missing" recommendation for them.
    assert "operations.monitoring" not in by_id and "reliability.tests" not in by_id
    assert by_id["operations.diagnostics"].kind == "missing"  # no logging/tracing/error tracking
    assert by_id["reliability.fault-tolerance"].priority == "low"
    # Targets only the team can give, per area; availability is already set.
    reliability_targets = by_id["targets.reliability"]
    assert "availability" not in reliability_targets.summary
    assert "recovery time (RTO)" in reliability_targets.summary
    assert by_id["targets.security"].summary == "Needed to judge this area: regulations."
    # High first, then by area order.
    assert [i.priority for i in report.insights] == sorted(
        (i.priority for i in report.insights), key=["high", "medium", "low"].index
    )
    state = {a.area.id: a.state for a in report.areas}
    assert state["security"] == "attention" and state["performance"] == "improve"
    assert state["experience"] == "unknown"  # no evidence either way: not enough to judge


def test_missing_mechanisms_need_a_review_and_respect_team_answers() -> None:
    profile = from_document(
        {"answers": {"operations.monitoring": {"text": "Datadog agent on every host."}}}
    )
    report, _ = _report([], ["src/App.java"], [], profile)
    ids = {i.id for i in report.insights}
    assert "operations.monitoring" not in ids  # the team explained how it is done
    assert {"reliability.tests", "operations.diagnostics", "reliability.recovery"} <= ids
    unreviewed, _ = _report([], [], [], Profile(), reviewed=False)
    assert not [i for i in unreviewed.insights if i.kind == "missing"]
    assert {a.state for a in unreviewed.areas} <= {"improve", "unknown"}


def test_fact_sheet_copies_the_tools_output() -> None:
    issues = [
        _issue("eslint", "no-eval", "security", "high", "code-injection.eval", path="web/a.js")
    ]
    report, evidence = _report(
        issues,
        ["Dockerfile"],
        [
            LibraryUse(
                "maven", "org.springframework.boot:spring-boot-starter-actuator", "pom.xml", 9
            )
        ],
        Profile(),
    )
    sheet = facts(report, evidence)
    assert [f["id"] for f in sheet] == [f"F{n}" for n in range(1, len(sheet) + 1)]
    texts = [str(f["text"]) for f in sheet]
    assert any(
        t.startswith("Recommendation security.injection (security, priority high)") for t in texts
    )
    issue_fact = next(f for f in sheet if f.get("issue"))
    assert issue_fact["path"] == "web/a.js" and issue_fact["insight"] == "security.injection"
    assert any(
        "Health and readiness endpoints (Spring Boot Actuator) (pom.xml:9)" in t for t in texts
    )
