"""Configuration and infrastructure checks (P12 slice 2, ADR 0023): the ``nfr`` engine's rules on
positive and negative examples, coverage honesty, parsing bounds, how the evidence reaches the NFR
checkpoints, and the fix recipes that resolve the gaps (ADR 0024)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crp_analysis.catalog import all_rules
from crp_analysis.engines.base import CancelToken
from crp_analysis.engines.nfr import ConfigChecksAdapter
from crp_analysis.fixes import recipes
from crp_analysis.fixes.patching import apply_edits
from crp_analysis.insights.engine import TrackedIssue, checkpoint_for
from crp_analysis.nfr import config
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
    stored = [
        {"signal": "autoscaling", "count": 7, "locations": [["deploy/k8s/hpa.yaml", 2, "HPA a"]]},
        {"signal": "health-endpoints", "count": 1, "locations": [["application.yml", 9, "k"]]},
        {"signal": "unknown-signal", "count": 1, "locations": [["x", 1, None]]},
    ]
    checked = {e.signal: e for e in detect(paths, [], stored)}
    assert checked["helm-charts"].kind == "context"
    assert (
        checked["terraform"].label == "Terraform infrastructure (security settings are not checked)"
    )
    assert checked["autoscaling"].count == 7
    assert checked["autoscaling"].locations == [("deploy/k8s/hpa.yaml", 2, "HPA a")]
    assert checked["health-endpoints"].kind == "supports"
    assert "unknown-signal" not in checked
    assert {e.signal for e in detect(paths, [])} == {"helm-charts", "terraform"}


def _issue(engine: str, rule: str, category: str, family: str | None) -> TrackedIssue:
    return TrackedIssue("i", engine, rule, category, family, "medium", "t", "p", "OPEN")


def test_configuration_issues_land_in_their_checkpoints() -> None:
    catalog = all_rules()
    expected = {
        config.SINGLE_REPLICA: "reliability.instances",
        config.NO_READINESS: "reliability.health",
        config.RECREATE: "reliability.rollouts",
        config.ACTUATOR_EXPOSED: "security.configuration",
        config.HEALTH_DETAILS: "security.configuration",
        config.SCHEMA_AUTO: "reliability.data",
    }
    for rule in config.RULES:
        info = catalog[f"nfr:{rule}"]
        found = checkpoint_for(_issue("nfr", rule, info.category.value, info.family))
        assert found is not None and found.id == expected[rule], rule


def _fix(path: str, text: str, rule: str, line: int | None) -> tuple[str, recipes.RecipeProposal]:
    info = recipes.FindingInfo("nfr", rule, path, line, line, None)
    [recipe] = recipes.options(info)
    proposal = recipes.propose(recipe, info, text, lambda _: None)
    return apply_edits(text, list(proposal.edits)), proposal


def test_recipes_resolve_every_configuration_gap_in_the_fixture(tmp_path: Path) -> None:
    root = prepare_fixture("nfr-config", tmp_path / "src")
    texts = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in root.rglob("*")
        if p.is_file()
        and config.is_candidate(p.relative_to(root).as_posix())
        and "src/test/" not in p.relative_to(root).as_posix()
    }
    gaps = config.analyse(texts).gaps
    assert {g.rule_id for g in gaps} == set(config.RULES)
    for gap in gaps:
        fixed, proposal = _fix(gap.path, texts[gap.path], gap.rule_id, gap.line)
        assert proposal.behaviour_note  # every recipe says what behaviour changes
        after = config.analyse({**texts, gap.path: fixed})
        assert gap.path not in after.failed, (gap.rule_id, after.failed)
        remaining = [
            g
            for g in after.gaps
            if (g.rule_id, g.path, g.identity) == (gap.rule_id, gap.path, gap.identity)
        ]
        assert remaining == [], (gap.rule_id, gap.path)


def test_recipes_change_only_the_narrow_shapes_they_understand() -> None:
    fixed, proposal = _fix(
        "application.properties",
        "management.endpoints.web.exposure.include=health,info,prometheus,heapdump\n",
        config.ACTUATOR_EXPOSED,
        1,
    )
    assert fixed == "management.endpoints.web.exposure.include=health,info,prometheus\n"
    assert proposal.title == "Expose only health,info,prometheus over HTTP"
    starred, _ = _fix(
        "application.yml", 'management:\n  include: "*"  # all\n', config.ACTUATOR_EXPOSED, 2
    )
    assert starred == 'management:\n  include: "health,info"  # all\n'
    workload = (
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: w\nspec:\n"
        "  selector: {}\n  template:\n    spec:\n      containers:\n        - name: w\n"
        "          ports:\n            - name: http\n"
    )
    added, _ = _fix("w.yaml", workload, config.SINGLE_REPLICA, 2)
    assert "spec:\n  replicas: 2\n  selector: {}" in added
    probed, proposal = _fix("w.yaml", workload, config.NO_READINESS, 10)
    assert "        - name: w\n          readinessProbe:\n            tcpSocket:\n" in probed
    assert "              port: http\n" in probed and proposal.title.endswith("port http")
    with pytest.raises(recipes.NoFix, match="single value"):
        _fix("a.yml", "include:\n  - health\n", config.ACTUATOR_EXPOSED, 1)
    with pytest.raises(recipes.NoFix, match="one line"):
        _fix(
            "w.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: w}\nspec:\n  template:\n"
            "    spec:\n      containers:\n        - {name: w, ports: [{containerPort: 80}]}\n",
            config.NO_READINESS,
            8,
        )
