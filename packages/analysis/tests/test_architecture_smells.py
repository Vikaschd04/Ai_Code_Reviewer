"""Architecture smells (P10 slice 3; ADR 0020): cycles, unstable dependencies and hub-like parts
on hand-computed models, with negative cases, the engine adapter and comparison anchoring."""

from __future__ import annotations

from pathlib import Path

from crp_analysis.architecture.evaluation import evaluate
from crp_analysis.architecture.metrics import CodeFile, Dependency
from crp_analysis.architecture.model import ArchitectureModel, is_generated
from crp_analysis.architecture.smells import CYCLE, HUB, UNSTABLE, detect
from crp_analysis.engines.base import CancelToken
from crp_analysis.engines.smells import ArchitectureSmellsAdapter, engine_version
from crp_analysis.graph.extract import extract_facts
from crp_analysis.graph.resolve import build_graph
from crp_analysis.sources.changes import FindingRef, diff_findings


def _model(edges: list[tuple[str, str, int]], extra: tuple[str, ...] = ()) -> ArchitectureModel:
    paths = sorted({p for e in edges for p in e[:2]} | set(extra))
    files = [CodeFile(p, 10, test="/test/" in p, generated=is_generated(p)) for p in paths]
    return ArchitectureModel(
        files=files, dependencies=[Dependency(s, t, line=line) for s, t, line in edges]
    )


def _found(model: ArchitectureModel, rule: str) -> dict[str, int | None]:
    report = detect(model.files, model.types, model.dependencies)
    return {s.path: s.line for s in report.smells if s.rule_id == rule}


CYCLIC = [
    ("src/a/a1.ts", "src/b/b1.ts", 1),
    ("src/a/a2.ts", "src/b/b1.ts", 2),
    ("src/a/a2.ts", "src/c/c1.ts", 3),
    ("src/b/b1.ts", "src/c/c1.ts", 1),
    ("src/c/c1.ts", "src/a/a1.ts", 4),
    ("src/d/d1.ts", "src/a/a1.ts", 1),  # uses the cycle, is not in it
    ("src/e/e1.ts", "src/gen/gensrc/g1.ts", 1),  # generated code is left out
    ("src/gen/gensrc/g1.ts", "src/e/e1.ts", 1),
    ("src/test/t1.ts", "src/a/a1.ts", 1),  # test code is left out
    ("src/a/a1.ts", "src/test/t1.ts", 2),
]


def test_cycles_are_reported_once_per_part_at_its_first_file() -> None:
    model = _model(CYCLIC)
    report = detect(model.files, model.types, model.dependencies)
    cycles = [s for s in report.smells if s.rule_id == CYCLE]
    # One finding per part: a (files a1 and a2, anchored at a1's import), b and c.
    assert {s.path: s.line for s in cycles} == {
        "src/a/a1.ts": 1,
        "src/b/b1.ts": 1,
        "src/c/c1.ts": 4,
    }
    assert report.counts[CYCLE] == 3
    by_part = {s.component: s for s in cycles}
    # c -> a (one import) is the cheapest cut: it breaks a->b->c->a and a->c->a.
    cut = by_part["src/c"]
    assert cut.details["cut_from_here"] == {"src/a": 1}
    assert "cheapest way to break the cycle includes removing src/c's use of src/a" in cut.message
    assert cut.title == "Dependency cycle among 3 parts, including src/c"
    a = by_part["src/a"]
    assert a.details["uses_in_cycle"] == ["src/b", "src/c"] and a.details["cut_from_here"] == {}
    assert a.details["files"] == ["src/a/a1.ts", "src/a/a2.ts"]
    assert "Files of src/a that take part: src/a/a1.ts, src/a/a2.ts." in a.message
    assert all(s.anchor_key == s.identity == f"cycle|{s.component}" for s in cycles)
    assert report.metrics.generated_files == 1 and report.metrics.test_files == 1


def test_two_part_cycle_title_and_acyclic_negative() -> None:
    pair = _model([("x/X.ts", "y/Y.ts", 3), ("y/Y.ts", "x/X.ts", 5)])
    report = detect(pair.files, pair.types, pair.dependencies)
    assert [s.title for s in report.smells if s.rule_id == CYCLE] == [
        "Dependency cycle: x ↔ y",
        "Dependency cycle: y ↔ x",
    ]
    assert [s.path for s in report.smells] == ["x/X.ts", "y/Y.ts"]
    layered = _model([("a/A.ts", "b/B.ts", 1), ("b/B.ts", "c/C.ts", 1), ("a/A.ts", "c/C.ts", 2)])
    assert _found(layered, CYCLE) == {}


# s is used by p, q and r (stable, I = 1/4) and uses u (I = 1/2): a less stable part.
UNSTABLE_EDGES = [
    ("p/p1.ts", "s/s1.ts", 1),
    ("q/q1.ts", "s/s1.ts", 1),
    ("r/r1.ts", "s/s1.ts", 1),
    ("s/s1.ts", "u/u1.ts", 7),
    ("u/u1.ts", "v/v1.ts", 1),
]


def test_unstable_dependency_matches_hand_computed_instability() -> None:
    model = _model(UNSTABLE_EDGES)
    report = detect(model.files, model.types, model.dependencies)
    (smell,) = [s for s in report.smells if s.rule_id == UNSTABLE]
    assert (smell.path, smell.line, smell.component) == ("s/s1.ts", 7, "s")
    assert smell.title == "Unstable dependency: s → u"
    assert smell.message.startswith(
        "s (instability 0.25) uses u (0.50), which change more easily than s: 1 of the 1 parts"
    )
    assert smell.details["less_stable"] == {"u": 0.5} and smell.anchor_key == "unstable|s"
    # u (0.5) uses v (0.0): toward stability, fine.
    assert report.counts[UNSTABLE] == 1


def test_unstable_dependency_needs_share_and_delta() -> None:
    # s (I = 1/4) uses u (I = 1/2) and stable parts k0..kn (I = 0) from the same file.
    def edges(stable_parts: int) -> list[tuple[str, str, int]]:
        return [*UNSTABLE_EDGES, *[("s/s1.ts", f"k{i}/k.ts", 8 + i) for i in range(stable_parts)]]

    assert _found(_model(edges(3)), UNSTABLE) == {}  # 1 of 4 parts: below 30%
    assert _found(_model(edges(2)), UNSTABLE) == {"s/s1.ts": 7}  # 1 of 3: flagged at the import
    # s (I = 1/2) uses m (I = 5/9 = 0.556): less stable, but within the 0.1 delta.
    close = [
        ("a/a.ts", "s/s.ts", 1),
        ("s/s.ts", "m/m0.ts", 2),
        *[(f"z{i}/z.ts", "m/m0.ts", 1) for i in range(3)],
        *[(f"m/m{i}.ts", "n/n.ts", 1) for i in range(5)],
    ]
    report = detect(_model(close).files, [], _model(close).dependencies)
    instability = {c.key: c.instability for c in report.metrics.components}
    assert instability["s"] == 0.5 and instability["m"] == 0.556
    assert not [s for s in report.smells if s.rule_id == UNSTABLE]


def _hub_edges(users: int = 4, used: int = 4) -> list[tuple[str, str, int]]:
    return [
        *[(f"src/a{i}/A.ts", "src/h/h1.ts", 1) for i in range(users)],
        *[("src/h/h2.ts", f"src/b{i}/B.ts", i + 1) for i in range(used)],
        ("src/h/h2.ts", "src/h/h1.ts", 9),
    ]


def test_hub_like_part_is_reported_once_at_its_first_file() -> None:
    model = _model(_hub_edges())
    report = detect(model.files, model.types, model.dependencies)
    (hub,) = [s for s in report.smells if s.rule_id == HUB]
    assert (hub.path, hub.line, hub.component) == ("src/h/h1.ts", None, "src/h")
    assert hub.anchor_key == "hub|src/h" and hub.identity == "hub|src/h"
    assert (hub.details["fan_in"], hub.details["fan_out"]) == (4, 4)
    assert hub.details["fan_in_upper_quartile"] == 1.0
    assert hub.details["busiest_files"] == ["src/h/h1.ts", "src/h/h2.ts"]
    assert hub.title == "Hub-like part: src/h"
    assert "used by 4 parts and uses 4 parts" in hub.message


def test_hub_needs_evidence() -> None:
    assert _found(_model(_hub_edges(users=4, used=2)), HUB) == {}  # uses too few parts
    assert _found(_model(_hub_edges(users=3, used=3)[:-1][:6]), HUB) == {}  # fewer than 8 parts


def test_adapter_reports_smells_with_honest_coverage(tmp_path: Path) -> None:
    model = _model([*CYCLIC, *_hub_edges()])
    adapter = ArchitectureSmellsAdapter().bind(
        model,
        version=engine_version("crp-graph-extract-v2"),
        unreadable={"src/b/b1.ts": "the dependency map could not read this file (FAILED)"},
    )
    files = [f.path for f in model.files if not f.test and not f.generated] + ["src/new.ts"]
    outcome = adapter.run(tmp_path, files, cancel=CancelToken(), heartbeat=lambda _m: None)
    assert outcome.state.value == "PARTIAL"
    assert {p.path for p in outcome.problems} == {"src/b/b1.ts", "src/new.ts"}
    rules = {(f.rule_id, f.path) for f in outcome.findings}
    assert (CYCLE, "src/b/b1.ts") not in rules  # not checked, so not reported either
    assert (CYCLE, "src/c/c1.ts") in rules and (HUB, "src/h/h1.ts") in rules
    hub = next(f for f in outcome.findings if f.rule_id == HUB)
    assert hub.details is not None and hub.details["anchor_key"] == "hub|src/h"
    assert hub.details["evidence"] == "potential" and hub.anchor == "file"
    affected = outcome.diagnostics["parts_affected"]
    assert isinstance(affected, dict) and (affected[CYCLE], affected[HUB]) == (3, 1)
    unbound = ArchitectureSmellsAdapter().run(
        tmp_path, files, cancel=CancelToken(), heartbeat=lambda _m: None
    )
    assert unbound.state.value == "FAILED" and unbound.error_code == "not_bound"


def test_part_level_findings_follow_their_part_in_comparisons() -> None:
    def ref(fid: str, path: str, key: str | None) -> FindingRef:
        return FindingRef(
            fid, f"fp-{path}", "smells", HUB, path, None, "medium", "Hub", None, None, key
        )

    base = [ref("1", "src/h/a.ts", "hub|src/h"), ref("2", "src/g/a.ts", "hub|src/g")]
    head = [ref("3", "src/h/0.ts", "hub|src/h"), ref("4", "src/k/a.ts", "hub|src/k")]
    diff = diff_findings(base, head, {})
    assert [(h.id, b.id) for h, b in diff.unchanged] == [("3", "1")]
    assert [f.id for f in diff.new] == ["4"] and [f.id for f in diff.absent] == ["2"]


def test_dependencies_inside_a_cycle_are_reported_once_as_the_cycle() -> None:
    # store (I = 1/3) uses utils (I = 1/2) inside their cycle: a cycle, not also unstable.
    model = _model(
        [
            ("p/panel.js", "s/store.js", 1),
            ("s/store.js", "u/utils.js", 2),
            ("u/utils.js", "s/store.js", 3),
        ]
    )
    assert _found(model, UNSTABLE) == {}
    assert set(_found(model, CYCLE)) == {"s/store.js", "u/utils.js"}


def test_lwc_c_modules_resolve_to_sibling_bundles() -> None:
    lwc = "force-app/main/default/lwc"
    texts = {
        f"{lwc}/a/a.js": 'import { b } from "c/b";\nimport { x } from "c/missing";\nexport const a = b;\n',
        f"{lwc}/b/b.ts": "export const b = 1;\n",
        "other/lwc/c/c.js": 'import { b } from "c/b";\nexport const c = b;\n',
    }
    facts = {
        p: extract_facts(t.encode(), p, "typescript" if p.endswith(".ts") else "javascript")
        for p, t in texts.items()
    }
    graph = build_graph(
        sources={p: "javascript" for p in texts},
        facts=facts,
        known_files=dict.fromkeys(texts, "ANALYZABLE"),
        modules=[],
        tsconfigs=[],
    )
    edges = {(e.source, e.target_ref): (e.target, e.classification) for e in graph.edges}
    assert edges[(f"file:{lwc}/a/a.js", "c/b")] == (f"file:{lwc}/b/b.ts", "resolved")
    assert edges[(f"file:{lwc}/a/a.js", "c/missing")] == (None, "unresolved")
    assert edges[("file:other/lwc/c/c.js", "c/b")] == (f"file:{lwc}/b/b.ts", "inferred")


def test_labelled_evaluation_set_has_no_mismatches() -> None:
    result = evaluate(Path(__file__).resolve().parents[3] / "fixtures" / "architecture-eval")
    assert result.mismatches == []
    report = result.to_json()
    rules = report["rules"]
    assert isinstance(rules, dict)
    for rule in (CYCLE, UNSTABLE, HUB):
        assert rules[rule]["precision"] == 1.0 and rules[rule]["recall"] == 1.0
        assert rules[rule]["positive_cases"] >= 1 and rules[rule]["negative_cases"] >= 1
    assert result.cases == 10
