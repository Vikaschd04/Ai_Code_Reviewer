"""Architecture rules (P10 slice 2; ADR 0019): parsing as data, pattern matching, evaluation with
hand-computed breaches, per-rule hashes, and the engine adapter's coverage."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from crp_analysis.architecture.metrics import CodeFile, Dependency
from crp_analysis.architecture.model import ArchitectureModel
from crp_analysis.architecture.rules import (
    FORBID_PREFIX,
    LAYERS_RULE,
    RulesError,
    evaluate,
    from_document,
    matches,
    parse_yaml,
    to_yaml,
)
from crp_analysis.engines.architecture import ArchitectureRulesAdapter, engine_version
from crp_analysis.engines.base import CancelToken
from crp_analysis.lifecycle import Prior, RunView, classify_absence
from crp_core.domain.states import RecheckState

TODAY = date(2026, 10, 9)
J = "src/main/java/com/acme"

RULES_YAML = """\
schema: crp-architecture-rules-v1
layers:
  - name: web
    match: [com.acme.web.**, web/ui]
  - name: service
    match: [com.acme.service.**]
  - name: domain
    match: [com.acme.domain.**]
layering: lower
forbid:
  - key: domain-no-legacy
    from: Domain
    to: com.acme.legacy.**
    reason: Legacy is being removed.
    severity: high
allow:
  - from: domain
    to: service
    reason: Events migration.
    until: 2026-12-31
  - from: service
    to: web
    reason: Old view helper.
    until: 2026-01-01
"""


def _files() -> list[CodeFile]:
    return [
        CodeFile(f"{J}/web/W1.java", 10, "com.acme.web"),
        CodeFile(f"{J}/web/W2.java", 10, "com.acme.web"),
        CodeFile(f"{J}/service/S1.java", 10, "com.acme.service"),
        CodeFile(f"{J}/domain/D1.java", 10, "com.acme.domain"),
        CodeFile(f"{J}/domain/events/E1.java", 10, "com.acme.domain.events"),
        CodeFile(f"{J}/legacy/L1.java", 10, "com.acme.legacy"),
        CodeFile("web/ui/U1.ts", 5),
        CodeFile("web/api/Api.ts", 5),
        CodeFile("src/test/java/com/acme/web/W1Test.java", 5, "com.acme.web", test=True),
    ]


def _dependencies() -> list[Dependency]:
    return [
        Dependency(f"{J}/web/W1.java", f"{J}/service/S1.java", line=3),  # down: fine
        Dependency(f"{J}/web/W1.java", f"{J}/service/S1.java", line=9),  # same pair (extends)
        Dependency(f"{J}/web/W1.java", f"{J}/domain/D1.java", line=4),  # skips a layer
        Dependency(f"{J}/service/S1.java", f"{J}/domain/D1.java", line=3),
        Dependency(f"{J}/domain/D1.java", f"{J}/service/S1.java", line=5),  # up, allowed
        Dependency(f"{J}/service/S1.java", f"{J}/web/W2.java", line=6),  # up, exception expired
        Dependency(f"{J}/domain/D1.java", f"{J}/legacy/L1.java", line=7),  # forbidden
        Dependency(f"{J}/domain/events/E1.java", target_package="com.acme.legacy", line=2),
        Dependency(f"{J}/domain/events/E1.java", f"{J}/domain/D1.java", line=3),  # same layer
        Dependency(f"{J}/web/W1.java", f"{J}/web/W2.java", line=5),  # same part
        Dependency("web/ui/U1.ts", "web/api/Api.ts", line=1),  # target in no layer
        Dependency(
            "src/test/java/com/acme/web/W1Test.java", f"{J}/legacy/L1.java", line=1
        ),  # test code is left out
    ]


@pytest.mark.parametrize(
    ("pattern", "key", "expected"),
    [
        ("com.acme.web.**", "com.acme.web", True),
        ("com.acme.web.**", "com.acme.web.view.admin", True),
        ("com.acme.web.**", "com.acme.webx", False),
        ("com.*.web", "com.acme.web", True),
        ("com.*.web", "com.a.b.web", False),
        ("**.web", "com.acme.web", True),
        ("com.acme.**.impl", "com.acme.impl", True),
        ("com.acme.**.impl", "com.acme.x.y.impl", True),
        ("com.acme.**.impl", "com.acme.x.y", False),
        ("src/web/**", "src/web/a/b", True),
        ("src/*", "src", False),
        ("src/*", "src/web", True),
        ("svc-*", "svc-orders", True),
        ("**", ".", True),
        (".", ".", True),
        ("src", ".", False),
    ],
)
def test_patterns(pattern: str, key: str, expected: bool) -> None:
    assert matches(pattern, key) is expected


def test_breaches_match_hand_computed_values() -> None:
    rules = parse_yaml(RULES_YAML)
    result = evaluate(rules, _files(), _dependencies(), today=TODAY)
    found = {(v.rule_id, v.source_path, v.target, v.line, v.severity) for v in result.violations}
    assert found == {
        (
            f"{FORBID_PREFIX}domain-no-legacy",
            f"{J}/domain/D1.java",
            f"{J}/legacy/L1.java",
            7,
            "high",
        ),
        (
            f"{FORBID_PREFIX}domain-no-legacy",
            f"{J}/domain/events/E1.java",
            "com.acme.legacy",
            2,
            "high",
        ),
        (LAYERS_RULE, f"{J}/service/S1.java", f"{J}/web/W2.java", 6, "medium"),
    }
    # The forbid rule names the layer case-insensitively; ** covers sub-packages.
    assert rules.forbid[0].source == "domain"
    assert result.layer_of["com.acme.domain.events"] == "domain"
    assert result.layer_of["web/ui"] == "web" and result.layer_of["web/api"] is None
    assert result.unassigned == ["com.acme.legacy", "web/api"]
    assert result.allowed == 1  # domain -> service, until 2026-12-31
    assert [(a.source, a.target) for a in result.expired] == [("service", "web")]
    # W1 -> S1 appears twice but counts once (anchored at the import, line 3); the test file
    # and the same-part dependency are not checked.
    assert result.dependencies_checked == 9
    layered = next(v for v in result.violations if v.rule_id == LAYERS_RULE)
    assert layered.message.endswith("service may only use the layers below it (domain).")
    assert layered.source_layer == "service" and layered.target_layer == "web"


def test_next_layer_only_and_bottom_layer_wording() -> None:
    rules = from_document(
        {
            "layers": [
                {"name": "web", "match": ["com.acme.web.**"]},
                {"name": "service", "match": ["com.acme.service.**"]},
                {"name": "domain", "match": ["com.acme.domain.**"]},
            ],
            "layering": "next",
        }
    )
    result = evaluate(rules, _files(), _dependencies(), today=TODAY)
    messages = {(v.source_path, v.target): v.message for v in result.violations}
    skip = messages[(f"{J}/web/W1.java", f"{J}/domain/D1.java")]
    assert skip.endswith("web may only use the layer directly below it (service).")
    up = messages[(f"{J}/domain/D1.java", f"{J}/service/S1.java")]
    assert up.endswith("domain is the bottom layer and may not use other layers.")
    assert len(result.violations) == 3  # W1 -> D1, D1 -> S1, S1 -> W2


def test_yaml_round_trip_and_canonical_hash() -> None:
    rules = parse_yaml(RULES_YAML)
    text = to_yaml(rules)
    assert text.startswith("# refactorX architecture rules")
    assert "until: 2026-12-31" in text  # a YAML date, not quoted text
    again = parse_yaml(text)
    assert again == rules and again.sha256() == rules.sha256()
    assert from_document(rules.to_document()) == rules
    assert rules.rule_ids() == [LAYERS_RULE, f"{FORBID_PREFIX}domain-no-legacy"]


def test_rule_hashes_follow_what_each_rule_detects() -> None:
    base = from_document(
        {
            "layers": [
                {"name": "web", "match": ["web.**"]},
                {"name": "core", "match": ["core.**"]},
            ],
            "forbid": [
                {"key": "no-legacy", "from": "core", "to": "legacy.**"},
                {"key": "no-tools", "from": "app.**", "to": "tools.**"},
            ],
        }
    )
    reworded = from_document(
        {
            **base.to_document(),
            "forbid": [
                {"key": "no-legacy", "from": "core", "to": "legacy.**", "reason": "x"},
                {"key": "no-tools", "from": "app.**", "to": "tools.**", "severity": "high"},
            ],
        }
    )
    assert reworded.sha256() != base.sha256()
    assert reworded.rule_hashes() == base.rule_hashes()  # reasons and severity detect nothing
    moved = from_document(
        {
            **base.to_document(),
            "layers": [
                {"name": "web", "match": ["web.**", "ui.**"]},
                {"name": "core", "match": ["core.**"]},
            ],
        }
    )
    old, new = base.rule_hashes(), moved.rule_hashes()
    assert old[LAYERS_RULE] != new[LAYERS_RULE]
    assert old[f"{FORBID_PREFIX}no-legacy"] != new[f"{FORBID_PREFIX}no-legacy"]  # uses a layer
    assert old[f"{FORBID_PREFIX}no-tools"] == new[f"{FORBID_PREFIX}no-tools"]  # patterns only
    excepted = from_document(
        {**base.to_document(), "allow": [{"from": "core", "to": "web", "reason": "x"}]}
    )
    assert excepted.rule_hashes()[LAYERS_RULE] != old[LAYERS_RULE]


def test_recheck_compares_the_rules_own_hash() -> None:
    hashes = {LAYERS_RULE: "a" * 64, f"{FORBID_PREFIX}x": "b" * 64}
    run = RunView("architecture", "SUCCEEDED", "1", "z" * 64, frozenset(hashes), hashes)

    def recheck(rule_id: str, observed: str) -> RecheckState:
        prior = Prior("architecture", rule_id, "A.java", "1", observed)
        return classify_absence(prior, run, file_present=True, file_outcome="ANALYZED").state

    # Another rule changed (the run's whole-set hash differs): this rule's absence is verified.
    assert recheck(LAYERS_RULE, "a" * 64) is RecheckState.VERIFIED_ABSENT
    # This rule changed: not a fix.
    assert recheck(LAYERS_RULE, "c" * 64) is RecheckState.UNKNOWN
    # This rule was removed.
    assert recheck(f"{FORBID_PREFIX}gone", "b" * 64) is RecheckState.RULE_OBSOLETE


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("layers: &a []\nforbid: *a\n", "anchors and aliases"),
        ("a: 1\n---\nb: 2\n", "one YAML document"),
        ("!!python/object/apply:os.system ['true']\n", "constructor"),
        ("layers: [\n", "line"),
        ("x" * 70_000, "larger than 64 KB"),
        ("layers:\n  - name: web\n    match: ['src//web']\n", "empty name part"),
        ("layers:\n  - name: web\n  - name: Web\n    match: [a]\n", "used twice"),
        ("layers:\n  - name: web\n    mach: [a]\n", "unknown field mach"),
        ("forbid:\n  - from: a.**\n    to: a.**\n", "from and to are the same"),
        ("allow:\n  - from: a\n    to: b\n", "allow[1].reason: required"),
        ("allow:\n  - from: a\n    to: b\n    reason: r\n    until: soon\n", "a date like"),
        ("forbid:\n  - from: a\n    to: b\n    severity: urgent\n", "one of critical"),
        ("layering: strict\n", "layering: one of lower, next, none"),
        ("schema: other\n", "schema: must be crp-architecture-rules-v1"),
        ("forbid:\n  - from: a\n    to: b\n  - from: a\n    to: b\n", "is used twice"),
        ("forbid:\n  - from: 'a b'\n    to: c\n", "not a layer name or a valid pattern"),
        ("- just a list\n", "must be a mapping"),
    ],
)
def test_invalid_rules_say_where_and_why(text: str, problem: str) -> None:
    with pytest.raises(RulesError) as error:
        parse_yaml(text)
    assert any(problem in item for item in error.value.problems), error.value.problems


def test_overlaps_unmatched_references_and_empty_rules() -> None:
    rules = from_document(
        {
            "layers": [
                {"name": "web", "match": ["com.acme.**"]},
                {"name": "domain", "match": ["com.acme.domain.**"]},
            ],
            "forbid": [{"key": "typo", "from": "web", "to": "com.acme.legasy.**"}],
        }
    )
    result = evaluate(rules, _files(), _dependencies(), today=TODAY)
    assert result.layer_of["com.acme.domain"] == "web"  # the first layer that matches
    assert result.overlaps["com.acme.domain"] == ["web", "domain"]
    assert result.unmatched == ["forbid \"typo\": to 'com.acme.legasy.**' matches no part"]
    assert from_document(None).empty and from_document({"layers": []}).empty
    one_layer = from_document({"layers": [{"name": "all", "match": ["**"]}]})
    assert one_layer.empty and one_layer.rule_ids() == []


def test_adapter_reports_breaches_and_honest_coverage(tmp_path: Path) -> None:
    rules = parse_yaml(RULES_YAML)
    model = ArchitectureModel(files=_files(), dependencies=_dependencies())
    adapter = ArchitectureRulesAdapter().bind(
        rules,
        model,
        today=TODAY,
        version=engine_version("crp-graph-extract-v2"),
        rules_version=3,
        unreadable={f"{J}/web/W2.java": "the dependency map could not read this file (FAILED)"},
    )
    files = [f.path for f in _files() if not f.test] + [f"{J}/New.java"]
    outcome = adapter.run(tmp_path, files, cancel=CancelToken(), heartbeat=lambda _m: None)
    assert outcome.state.value == "PARTIAL"  # two files could not be checked
    assert {p.path: p.reason for p in outcome.problems} == {
        f"{J}/web/W2.java": "the dependency map could not read this file (FAILED)",
        f"{J}/New.java": "not in the dependency map of this review",
    }
    assert {f.rule_id for f in outcome.findings} == {
        LAYERS_RULE,
        f"{FORBID_PREFIX}domain-no-legacy",
    }
    forbidden = next(f for f in outcome.findings if f.path.endswith("D1.java"))
    assert forbidden.start_line == 7 and forbidden.severity == "high"
    assert forbidden.category == "maintainability" and forbidden.guidance is not None
    assert "Legacy is being removed." in forbidden.guidance.explanation
    assert forbidden.details is not None
    assert forbidden.details["rule_sha256"] == rules.rule_hashes()[forbidden.rule_id]
    assert forbidden.details["rules_version"] == 3
    assert outcome.diagnostics["rule_hashes"] == rules.rule_hashes()
    assert outcome.ruleset_sha256 == rules.sha256()
    assert outcome.engine_version is not None and outcome.engine_version.startswith("1.0.0+graph.")
    unbound = ArchitectureRulesAdapter().run(
        tmp_path, files, cancel=CancelToken(), heartbeat=lambda _m: None
    )
    assert unbound.state.value == "FAILED" and unbound.error_code == "not_bound"
