"""NFR assessment (P12 slice 1): questionnaire integrity, issue-to-question mapping over the whole
catalog, evidence signals, profile validation, honest status precedence and exports."""

from __future__ import annotations

import csv
import io

import pytest

from crp_analysis.catalog import all_rules
from crp_analysis.nfr.assessment import STATUSES, TrackedIssue, assess, to_csv, to_markdown
from crp_analysis.nfr.profile import TARGET_NAMES, Profile, ProfileError, from_document
from crp_analysis.nfr.questionnaire import load, questions_for_rule
from crp_analysis.nfr.signals import LibraryUse, detect


def test_questionnaire_is_the_owner_list_mapped_to_iso_25010() -> None:
    q = load()
    assert [a.id for a in q.aspects] == [
        "performance",
        "security",
        "recoverability",
        "operations",
        "reliability",
        "availability",
        "scalability",
        "usability",
        "portability",
    ]
    per_aspect = {a.id: sum(1 for x in q.questions if x.aspect == a.id) for a in q.aspects}
    assert per_aspect == {
        "performance": 4,
        "security": 3,
        "recoverability": 4,
        "operations": 2,
        "reliability": 2,
        "availability": 2,
        "scalability": 2,
        "usability": 2,
        "portability": 2,
    }
    assert len(q.questions) == 23 and len(q.ids) == 23
    assert q.question("recoverability.cost") is not None
    assert q.question("performance.access-pattern").text == (  # type: ignore[union-attr]
        "What is the access pattern — number of users, concurrent access?"
    )
    for question in q.questions:
        assert set(question.profile_fields) <= TARGET_NAMES | {"regulations", "platforms"}
    assert all(a.iso for a in q.aspects)


def test_every_catalog_rule_is_a_gap_for_at_least_one_question() -> None:
    known = load().ids
    for info in all_rules().values():
        family = info.family
        mapped = questions_for_rule(info.engine, info.rule_id, info.category.value, family)
        assert mapped, f"{info.engine}:{info.rule_id} maps to no question"
        assert set(mapped) <= known
    # Rules outside the catalog: dependency vulnerabilities and secrets from Trivy.
    assert questions_for_rule("trivy", "CVE-2024-1234", "dependencies", None) == (
        "security.attacks",
    )
    assert questions_for_rule("trivy", "secret:aws-access-key-id", "security", None) == (
        "security.access",
    )
    assert questions_for_rule(
        "smells", "crp.arch.hub", "maintainability", "maintainability.hub"
    ) == (
        "operations.remediation",
        "availability.fault-tolerance",
    )
    assert "scalability.demand" in questions_for_rule(
        "pmd-apex", "OperationWithLimitsInLoop", "performance", "performance.persistence-in-loop"
    )


def test_signals_cite_manifest_lines_and_files() -> None:
    paths = [
        "Dockerfile",
        ".github/workflows/ci.yml",
        "api/openapi.yaml",
        "docs/runbooks/restart.md",
        "deploy/k8s/deployment.yaml",
        "infra/main.tf",
        "src/main/resources/application.yml",
        "src/test/java/com/acme/OrderTest.java",
        "web/src/cart.test.ts",
        "README.md",
    ]
    libraries = [
        LibraryUse("maven", "org.springframework.boot:spring-boot-starter-actuator", "pom.xml", 21),
        LibraryUse("maven", "io.github.resilience4j:resilience4j-spring-boot3", "pom.xml", 30),
        LibraryUse("maven", "org.flywaydb:flyway-core", "pom.xml", 34),
        LibraryUse("npm", "@opentelemetry/sdk-node", "web/package.json", 12),
        LibraryUse("npm", "redis-mock", "web/package.json", 13),  # not a cache in production
        LibraryUse("maven", "io.micrometerx:other", "pom.xml", 40),  # look-alike group
    ]
    found = {e.signal: e for e in detect(paths, libraries)}
    assert set(found) == {
        "health-endpoints",
        "tracing",
        "circuit-breakers",
        "db-migrations",
        "ci-pipeline",
        "container",
        "api-specs",
        "runbooks",
        "deployment-manifests",
        "terraform",
        "application-config",
        "automated-tests",
    }
    assert found["health-endpoints"].locations == [
        ("pom.xml", 21, "org.springframework.boot:spring-boot-starter-actuator")
    ]
    assert found["automated-tests"].count == 2
    assert found["deployment-manifests"].kind == "context"
    assert found["container"].kind == "supports"


def test_profile_validation_says_where_and_why() -> None:
    profile = from_document(
        {
            "targets": {"availability_percent": 99.9, "rto_minutes": 60, "growth": " 2x a year "},
            "regulations": ["GDPR", "GDPR", "PCI DSS"],
            "answers": {
                "recoverability.cost": {"text": "About 20k EUR per hour of downtime."},
                "usability.simplicity": {"not_applicable": True, "reason": "Batch system, no UI"},
            },
        }
    )
    assert profile.targets == {
        "availability_percent": 99.9,
        "rto_minutes": 60,
        "growth": "2x a year",
    }
    assert profile.regulations == ("GDPR", "PCI DSS")
    assert from_document(profile.to_document()) == profile
    assert from_document(profile.to_document()).sha256() == profile.sha256()
    with pytest.raises(ProfileError) as error:
        from_document(
            {
                "targets": {
                    "availability_percent": 120,
                    "rto_minutes": 1.5,
                    "speed": 3,
                    "latency_p95_ms": "fast",
                },
                "answers": {
                    "nope": {"text": "x"},
                    "usability.simplicity": {"not_applicable": True},
                },
                "regulations": ["x"] * 21,
                "colour": "blue",
            }
        )
    problems = " | ".join(error.value.problems)
    for expected in (
        "targets.availability_percent: between 1 and 100 %",
        "targets.rto_minutes: must be a whole number",
        "targets.speed: unknown target",
        "targets.latency_p95_ms: must be a number",
        "answers.nope: unknown question",
        "answers.usability.simplicity.reason: say why the question does not apply",
        "regulations: a list of at most 20 entries",
        "profile: unknown field colour",
    ):
        assert expected in problems


def _issue(
    rule: str,
    category: str,
    severity: str = "high",
    status: str = "OPEN",
    engine: str = "pmd",
    family: str | None = None,
) -> TrackedIssue:
    return TrackedIssue(
        f"id-{rule}",
        engine,
        rule,
        category,
        family,
        severity,
        f"Title {rule}",
        "src/A.java",
        status,
    )


def test_status_precedence_is_honest() -> None:
    evidence = detect(
        ["Dockerfile"],
        [
            LibraryUse(
                "maven", "org.springframework.boot:spring-boot-starter-actuator", "pom.xml", 3
            )
        ],
    )
    issues = [
        _issue("EmptyCatchBlock", "reliability"),
        _issue("UnusedPrivateField", "maintainability", status="ACCEPTED_RISK"),
        _issue("SqlI", "security", severity="critical", status="RESOLVED"),  # fixed: not a gap
    ]
    profile = from_document(
        {
            "targets": {"rto_minutes": 30},
            "answers": {"usability.simplicity": {"not_applicable": True, "reason": "No UI"}},
        }
    )
    result = assess(load(), evidence, issues, profile, reviewed=True)
    status = {r.question.id: r.status for a in result.aspects for r in a.questions}
    assert status["reliability.consistency"] == "needs_work"  # an open defect
    assert status["recoverability.recovery-time"] == "evidence"  # Actuator + the team's RTO
    assert status["availability.continuous"] == "needs_input"  # evidence, but no target set
    assert status["portability.platforms"] == "evidence"  # Dockerfile
    assert status["usability.simplicity"] == "not_applicable"
    assert status["recoverability.cost"] == "needs_input"  # only the team can answer
    assert status["operations.remediation"] == "not_checked"  # accepted risk only, no evidence
    assert status["security.attacks"] == "not_checked"  # the issue was resolved
    remediation = next(
        r for a in result.aspects for r in a.questions if r.question.id == "operations.remediation"
    )
    assert (remediation.gaps.open, remediation.gaps.accepted) == (0, 1)
    assert sum(result.counts.values()) == 23 and set(result.counts) == set(STATUSES)


def test_exports_list_every_question_and_never_claim_compliance() -> None:
    profile = from_document({"targets": {"availability_percent": 99.5}, "platforms": ["Linux"]})
    result = assess(load(), [], [_issue("EmptyCatchBlock", "reliability")], profile, reviewed=True)
    rows = list(csv.reader(io.StringIO(to_csv(result))))
    assert rows[0][:4] == ["Aspect", "ISO/IEC 25010:2023", "Question", "Status"]
    assert len(rows) == 24  # header + 23 questions
    availability = next(r for r in rows if r[2].startswith("How can we ensure the high"))
    assert (
        availability[3] == "Answered by your team"
        and "Availability target: 99.5 %" in availability[9]
    )
    markdown = to_markdown(result, project="Shop | Core", source=load().source, basis="Upload X")
    assert markdown.startswith("# NFR readiness: Shop \\| Core")
    assert "refactorX does not certify compliance." in markdown
    assert "Status: **Needs work**." in markdown and markdown.count("### ") == 23
    empty = assess(load(), [], [], Profile(), reviewed=False)
    assert empty.counts["not_checked"] + empty.counts["needs_input"] == 23
