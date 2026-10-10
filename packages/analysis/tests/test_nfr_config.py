"""Configuration and infrastructure checks (P12 slice 2, ADR 0023): the ``nfr`` engine's rules on
positive and negative examples, coverage honesty, parsing bounds, and how the evidence reaches the
NFR questionnaire and the insights."""

from __future__ import annotations

from pathlib import Path

from crp_analysis.catalog import all_rules
from crp_analysis.engines.base import CancelToken
from crp_analysis.engines.nfr import ConfigChecksAdapter
from crp_analysis.insights.engine import ISSUE_GUIDELINES
from crp_analysis.nfr import config
from crp_analysis.nfr.assessment import TrackedIssue
from crp_analysis.nfr.questionnaire import questions_for_rule
from crp_analysis.nfr.signals import detect
from crp_analysis.normalize import normalize
from crp_core.domain.states import CoverageOutcome, EngineState
from crp_devtools.testing.fixture_projects import prepare_fixture

RESOURCES = "src/main/resources"


def _noop(_: str) -> None:
    return None


def _run(tmp_path: Path):  # type: ignore[no-untyped-def]
    root = prepare_fixture("nfr-config", tmp_path / "src")
    adapter = ConfigChecksAdapter()
    files = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and adapter.is_eligible(p.relative_to(root).as_posix(), None)
    )
    return root, files, adapter.run(root, files, cancel=CancelToken(), heartbeat=_noop)


def test_rules_fire_on_positive_examples_only(tmp_path: Path) -> None:
    root, files, outcome = _run(tmp_path)
    found = {(f.rule_id, f.path, f.start_line) for f in outcome.findings}
    assert found == {
        (config.SINGLE_REPLICA, "deploy/base/shop.yaml", 6),
        (config.RECREATE, "deploy/base/shop.yaml", 8),
        (config.NO_READINESS, "deploy/base/shop.yaml", 18),
        (config.SINGLE_REPLICA, "deploy/base/autoscaling.yaml", 32),  # cache: autoscaler min 1
        (config.SINGLE_REPLICA, "deploy/base/worker.yaml", 2),  # replicas not set
        (config.ACTUATOR_EXPOSED, f"{RESOURCES}/application.yml", 7),
        (config.HEALTH_DETAILS, f"{RESOURCES}/application.yml", 10),
        (config.SCHEMA_AUTO, f"{RESOURCES}/application.yml", 16),
        (config.SCHEMA_AUTO, f"{RESOURCES}/application-prod.properties", 2),
    }
    severities = {(f.path, f.rule_id): f.severity for f in outcome.findings}
    assert severities[(f"{RESOURCES}/application-prod.properties", config.SCHEMA_AUTO)] == "high"
    assert severities[(f"{RESOURCES}/application.yml", config.SCHEMA_AUTO)] is None  # catalog
    cache = next(f for f in outcome.findings if "cache" in f.message)
    assert "HorizontalPodAutoscaler cache minimum: 1" in cache.message
    exposed = next(f for f in outcome.findings if f.rule_id == config.ACTUATOR_EXPOSED)
    assert "heapdump, env, configprops, threaddump" in exposed.message
    assert "shutdown" not in exposed.message  # exposure does not enable it

    # Negative examples: overlay and autoscaler minimums, probed or portless containers,
    # development profiles and test resources.
    paths = {f.path for f in outcome.findings}
    assert "deploy/base/api.yaml" not in paths and "deploy/base/orders.yaml" not in paths
    assert f"{RESOURCES}/application-local.properties" not in paths
    assert "src/test/resources/application.yml" not in files  # not eligible
    assert not any("metrics" in f.message for f in outcome.findings)

    # Normalized like any engine: catalog titles and categories, stable identities.
    normalized = normalize("nfr", root, outcome.findings)
    assert {n.category for n in normalized} == {"reliability", "security"}
    assert all(n.in_catalog for n in normalized)


def test_coverage_is_honest_about_templates_and_broken_files(tmp_path: Path) -> None:
    _, _, outcome = _run(tmp_path)
    problems = {p.path: (p.outcome, p.reason) for p in outcome.problems}
    assert problems["deploy/broken.yaml"][0] == CoverageOutcome.FAILED.value
    assert problems["deploy/broken.yaml"][1].startswith("not valid YAML (line 5")
    assert problems["chart/templates/deployment.yaml"] == (
        CoverageOutcome.NOT_ATTEMPTED.value,
        "template; its values are filled in at deploy time",
    )
    assert "chart/templates/deployment.yaml" not in outcome.attempted
    assert "deploy/broken.yaml" in outcome.attempted
    assert outcome.state is EngineState.PARTIAL
    assert outcome.diagnostics["workloads"] == 5
    assert outcome.diagnostics["templates"] == 1


def test_supporting_signals_have_file_and_line(tmp_path: Path) -> None:
    _, _, outcome = _run(tmp_path)
    signals = {s["signal"]: s for s in outcome.diagnostics["signals"]}  # type: ignore[union-attr]
    assert set(signals) == {
        "autoscaling",
        "backups",
        "circuit-breakers",
        "connection-pool",
        "disruption-budget",
        "graceful-shutdown",
        "health-endpoints",
        "k8s-probes",
        "multi-zone",
        "multiple-instances",
        "timeouts",
    }
    assert signals["multiple-instances"]["locations"] == [
        [
            "deploy/base/autoscaling.yaml",
            10,
            "Deployment orders: HorizontalPodAutoscaler orders minimum: 3",
        ],
        ["deploy/overlays/prod/kustomization.yaml", 7, "Deployment api: kustomization replicas: 3"],
    ]
    assert signals["backups"]["locations"] == [
        ["infra/database.tf", 4, "backup_retention_period = 7"]
    ]
    assert signals["k8s-probes"]["count"] == 4


def test_properties_documents_continuations_and_separators() -> None:
    documents = config.parse_properties(
        "# comment\n"
        "a.b = 1\n"
        "c.d: two\n"
        "e.f three\n"
        "long.value=first,\\\n"
        "    second\n"
        "#---\n"
        "spring.config.activate.on-profile=test\n"
        "x=y\n"
    )
    assert [[(p.raw, p.value, p.line) for p in d] for d in documents] == [
        [
            ("a.b", "1", 2),
            ("c.d", "two", 3),
            ("e.f", "three", 4),
            ("long.value", "first,second", 5),
        ],
        [("spring.config.activate.on-profile", "test", 8), ("x", "y", 9)],
    ]
    assert config.relaxed("Management.Endpoint.Health.Show_Details") == config.relaxed(
        "management.endpoint.health.show-details"
    )


def test_relaxed_keys_lists_and_profiles() -> None:
    text = (
        "management:\n  endpoints:\n    web:\n      exposure:\n"
        "        include: [health, heapdump]\n        exclude: env\n"
        "  endpoint:\n    health:\n      showDetails: ALWAYS\n"
    )
    found = config.analyse({"application.yml": text})
    assert {g.rule_id for g in found.gaps} == {config.ACTUATOR_EXPOSED, config.HEALTH_DETAILS}
    exposed = next(g for g in found.gaps if g.rule_id == config.ACTUATOR_EXPOSED)
    assert "heapdump" in exposed.message and "all endpoints" not in exposed.message
    # Everything sensitive excluded: fine. Development profile file: ignored.
    safe = "management.endpoints.web.exposure.include=*\n" + (
        "management.endpoints.web.exposure.exclude=heapdump,env,configprops,threaddump\n"
    )
    assert config.analyse({"application.properties": safe}).gaps == []
    assert config.analyse({"application-dev.properties": safe.splitlines()[0]}).gaps == []


def test_placeholders_and_templated_values_are_not_judged() -> None:
    deployment = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: a\nspec:\n"
        "  replicas: ${REPLICAS}\n"
    )
    found = config.analyse(
        {
            "k8s/a.yaml": deployment,
            "application.properties": "spring.jpa.hibernate.ddl-auto=${DDL:update}\n",
        }
    )
    assert found.gaps == []


def test_autoscaler_owns_the_replica_count() -> None:
    deployment = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: a\nspec:\n  replicas: 4\n"
    )
    hpa = (
        "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: a\n"
        "spec:\n  scaleTargetRef: {kind: Deployment, name: a}\n"
    )
    found = config.analyse({"a.yaml": deployment, "hpa.yaml": hpa})
    assert [(g.rule_id, g.path) for g in found.gaps] == [(config.SINGLE_REPLICA, "hpa.yaml")]
    assert "minimum: 1" in found.gaps[0].message  # minReplicas defaults to 1


def test_anchor_expansion_and_recursion_are_bounded() -> None:
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{n}: &{n} [{', '.join([f'*{p}'] * 10)}]\n" for p, n in zip("abcd", "bcde", strict=True)
    )
    found = config.analyse({"application.yml": bomb, "bootstrap.yml": "a: &a\n  b: *a\n"})
    assert found.failed["application.yml"].startswith("more than 20000 settings")
    assert "bootstrap.yml" in found.failed
    assert found.gaps == []


def test_detect_uses_config_evidence_and_says_what_is_not_checked() -> None:
    paths = ["chart/Chart.yaml", "infra/main.tf", "deploy/k8s/app.yaml", "application.yml"]
    legacy = {e.signal for e in detect(paths, [])}
    assert {"deployment-manifests", "terraform", "application-config"} <= legacy

    stored = [
        {"signal": "autoscaling", "count": 7, "locations": [["deploy/k8s/hpa.yaml", 2, "HPA a"]]},
        {"signal": "health-endpoints", "count": 1, "locations": [["application.yml", 9, "k"]]},
        {"signal": "unknown-signal", "count": 1, "locations": [["x", 1, None]]},
    ]
    checked = {e.signal: e for e in detect(paths, [], stored)}
    assert "deployment-manifests" not in checked and "application-config" not in checked
    assert checked["helm-charts"].kind == "context"
    assert (
        checked["terraform"].label == "Terraform infrastructure (security settings are not checked)"
    )
    assert checked["autoscaling"].count == 7
    assert checked["autoscaling"].locations == [("deploy/k8s/hpa.yaml", 2, "HPA a")]
    assert "scalability.spikes" in checked["autoscaling"].questions
    assert checked["health-endpoints"].kind == "supports"
    assert "unknown-signal" not in checked


def _issue(engine: str, rule: str, category: str, family: str | None) -> TrackedIssue:
    return TrackedIssue("i", engine, rule, category, family, "medium", "t", "p", "OPEN")


def test_questions_and_recommendations_for_configuration_issues() -> None:
    catalog = all_rules()
    for rule in config.RULES:
        info = catalog[f"nfr:{rule}"]
        assert questions_for_rule("nfr", rule, info.category.value, info.family)
        issue = _issue("nfr", rule, info.category.value, info.family)
        key = next(g.key for g in ISSUE_GUIDELINES if g.matches(issue))
        assert key in {"reliability.deployment", "reliability.schema", "security.configuration"}
    assert questions_for_rule(
        "nfr", config.SINGLE_REPLICA, "reliability", "availability.single-instance"
    ) == (
        "availability.continuous",
        "availability.fault-tolerance",
    )
    cpu = _issue("trivy", "misconfig:KSV-0011", "security", None)
    root = _issue("trivy", "misconfig:KSV-0012", "security", None)
    assert next(g.key for g in ISSUE_GUIDELINES if g.matches(cpu)) == "performance.capacity"
    assert next(g.key for g in ISSUE_GUIDELINES if g.matches(root)) == "security.configuration"
    assert questions_for_rule("trivy", "misconfig:KSV-0011", "security", None) == (
        "security.attacks",
        "scalability.demand",
    )
    assert questions_for_rule("trivy", "misconfig:DS-0002", "security", None) == (
        "security.attacks",
    )
