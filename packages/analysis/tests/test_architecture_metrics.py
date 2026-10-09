"""Architecture metrics (P10 slice 1) against values computed by hand.

Layered fixture (files stand in for classes):

    app:  A1 -> svc.S1          A2 -> svc.S1, dom.D1
    svc:  S1 -> dom.D1, dom.D2  S2 (abstract class, no dependencies)
    dom:  D1                    D2 (interface)

    component  Ca  Ce  I          A    D
    app        0   2   2/2 = 1    0    |0 + 1 - 1| = 0
    svc        2   1   1/3        1/2  |1/2 + 1/3 - 1| = 1/6
    dom        2   0   0          1/2  |1/2 + 0 - 1| = 1/2

Edge weights (distinct file-level dependencies): app->svc 2, app->dom 1, svc->dom 2.
"""

from __future__ import annotations

import pytest

from crp_analysis.architecture.metrics import (
    CodeFile,
    CodeType,
    ComponentEdge,
    Dependency,
    compute,
)


def _file(path: str, package: str | None = None, lines: int = 10, test: bool = False) -> CodeFile:
    return CodeFile(path, lines, package, test)


LAYERED_FILES = [
    _file("src/app/A1.java", "app"),
    _file("src/app/A2.java", "app"),
    _file("src/svc/S1.java", "svc"),
    _file("src/svc/S2.java", "svc"),
    _file("src/dom/D1.java", "dom"),
    _file("src/dom/D2.java", "dom"),
]
LAYERED_TYPES = [
    CodeType("src/app/A1.java", False),
    CodeType("src/app/A2.java", False),
    CodeType("src/svc/S1.java", False),
    CodeType("src/svc/S2.java", True),
    CodeType("src/dom/D1.java", False),
    CodeType("src/dom/D2.java", True),
]
LAYERED_DEPS = [
    Dependency("src/app/A1.java", "src/svc/S1.java"),
    Dependency("src/app/A2.java", "src/svc/S1.java"),
    Dependency("src/app/A2.java", "src/dom/D1.java"),
    Dependency("src/svc/S1.java", "src/dom/D1.java"),
    Dependency("src/svc/S1.java", "src/dom/D2.java"),
    Dependency("src/svc/S1.java", "src/dom/D2.java"),  # duplicate: counted once
    Dependency("src/svc/S2.java", "src/svc/S1.java"),  # same component: ignored
    Dependency("src/dom/D1.java", None),  # unresolved: ignored
]


def test_martin_metrics_match_hand_computed_values() -> None:
    result = compute(LAYERED_FILES, LAYERED_TYPES, LAYERED_DEPS)
    by_key = {c.key: c for c in result.components}
    assert sorted(by_key) == ["app", "dom", "svc"]
    rows = {
        k: (c.afferent, c.efferent, c.instability, c.abstractness, c.distance)
        for k, c in by_key.items()
    }
    assert rows == {
        "app": (0, 2, 1.0, 0.0, 0.0),
        "svc": (2, 1, 0.333, 0.5, 0.167),
        "dom": (2, 0, 0.0, 0.5, 0.5),
    }
    assert (by_key["app"].fan_in, by_key["app"].fan_out) == (0, 2)
    assert (by_key["dom"].fan_in, by_key["dom"].fan_out) == (2, 0)
    assert by_key["svc"].types == 2 and by_key["svc"].abstract_types == 1
    assert by_key["app"].files == 2 and by_key["app"].lines == 20
    assert all(c.zone is None and not c.in_cycle for c in result.components)
    assert result.edges == [
        ComponentEdge("app", "svc", 2),
        ComponentEdge("svc", "dom", 2),
        ComponentEdge("app", "dom", 1),
    ]
    assert result.cycles == [] and result.dependencies_counted == 5


def test_zones_and_undefined_values() -> None:
    files = [
        _file("core/Util.java", "core"),
        _file("api/Port.java", "api"),
        _file("api/Spi.java", "api"),
        _file("web/app.ts"),
        _file("web/lib/a.ts"),
        _file("README.md"),
        _file("leaf/Leaf.java", "leaf"),
    ]
    types = [
        CodeType("core/Util.java", False),
        CodeType("api/Port.java", True),
        CodeType("api/Spi.java", True),
        CodeType("leaf/Leaf.java", False),
    ]
    deps = [
        Dependency("web/app.ts", "core/Util.java"),
        Dependency("web/lib/a.ts", "core/Util.java"),
        Dependency("api/Port.java", "core/Util.java"),
        Dependency("web/app.ts", "leaf/Leaf.java"),
    ]
    by_key = {c.key: c for c in compute(files, types, deps).components}
    # core: concrete (A = 0) and stable (I = 0): D = 1, zone of pain.
    core = by_key["core"]
    assert (core.instability, core.abstractness, core.distance, core.zone) == (
        0.0,
        0.0,
        1.0,
        "pain",
    )
    # leaf: also A = 0 and I = 0 (D = 1), but only one file uses it: not reported as a zone.
    leaf = by_key["leaf"]
    assert (leaf.distance, leaf.zone) == (1.0, None)
    # api: abstract (A = 1) and unstable (I = 1): D = 1, zone of uselessness.
    assert by_key["api"].zone == "uselessness" and by_key["api"].kind == "package"
    # Folders without types: abstractness and distance stay undefined, never zero.
    web = by_key["web"]
    assert web.kind == "folder" and web.abstractness is None and web.distance is None
    # A file with neither types nor dependencies: every ratio is undefined.
    root = by_key["."]
    assert (root.instability, root.abstractness, root.distance) == (None, None, None)


def test_on_demand_package_imports_and_test_code() -> None:
    files = [
        _file("a/A.java", "a"),
        _file("b/B.java", "b"),
        _file("a/ATest.java", "a", test=True),
    ]
    deps = [
        Dependency("a/A.java", target_package="b"),
        Dependency("a/A.java", target_package="not.here"),
        Dependency("a/ATest.java", "b/B.java"),
    ]
    result = compute(files, [], deps)
    assert result.test_files == 1
    assert result.edges == [ComponentEdge("a", "b", 1)]
    by_key = {c.key: c for c in result.components}
    assert by_key["a"].files == 1 and by_key["b"].afferent == 1


CYCLIC_FILES = [_file(f"{c}/{c}{i}.java", c) for c in "xyz" for i in range(1, 4)]
# x->y weight 3, y->x 1, y->z 1, z->x 2: two cycles (x-y-x and x-y-z-x).
CYCLIC_DEPS = [
    Dependency("x/x1.java", "y/y1.java"),
    Dependency("x/x2.java", "y/y1.java"),
    Dependency("x/x3.java", "y/y2.java"),
    Dependency("y/y1.java", "x/x1.java"),
    Dependency("y/y2.java", "z/z1.java"),
    Dependency("z/z1.java", "x/x1.java"),
    Dependency("z/z2.java", "x/x2.java"),
]


@pytest.mark.parametrize("exact_limit", [16, 0])
def test_cycles_and_the_cheapest_cut(exact_limit: int) -> None:
    result = compute(CYCLIC_FILES, [], CYCLIC_DEPS, exact_limit=exact_limit)
    (cycle,) = result.cycles
    assert cycle.components == ["x", "y", "z"]
    assert sorted((e.source, e.target, e.weight) for e in cycle.edges) == [
        ("x", "y", 3),
        ("y", "x", 1),
        ("y", "z", 1),
        ("z", "x", 2),
    ]
    # Cutting x->y costs 3; cutting y->x and y->z costs 2 and breaks both cycles.
    assert [(e.source, e.target) for e in cycle.cut] == [("y", "x"), ("y", "z")]
    assert cycle.exact is (exact_limit > 0)
    assert all(c.in_cycle for c in result.components)


def test_two_separate_cycles_and_a_self_contained_component() -> None:
    files = [_file(f"{c}/F.java", c) for c in "abcde"]
    deps = [
        Dependency("a/F.java", "b/F.java"),
        Dependency("b/F.java", "a/F.java"),
        Dependency("c/F.java", "d/F.java"),
        Dependency("d/F.java", "c/F.java"),
        Dependency("d/F.java", "e/F.java"),
    ]
    result = compute(files, [], deps)
    assert [c.components for c in result.cycles] == [["a", "b"], ["c", "d"]]
    assert all(len(c.cut) == 1 and c.exact for c in result.cycles)
    assert {c.key: c.in_cycle for c in result.components}["e"] is False


def test_unknown_abstractness_stays_undefined() -> None:
    result = compute(LAYERED_FILES, LAYERED_TYPES, LAYERED_DEPS, abstract_known=False)
    assert all(c.abstractness is None and c.distance is None for c in result.components)
    assert {c.key: c.instability for c in result.components}["app"] == 1.0


def test_heuristic_cuts_are_valid_and_never_cheaper_than_exact() -> None:
    import random

    from crp_analysis.architecture.metrics import _acyclic

    rng = random.Random(11)  # noqa: S311 - reproducible test data
    for _ in range(60):
        packages = rng.randint(3, 6)
        files = [_file(f"p{p}/F{i}.java", f"p{p}") for p in range(packages) for i in range(3)]
        deps = [
            Dependency(rng.choice(files).path, rng.choice(files).path)
            for _ in range(rng.randint(6, 18))
        ]
        exact = compute(files, [], deps, exact_limit=40)
        heuristic = compute(files, [], deps, exact_limit=0)
        assert [c.components for c in exact.cycles] == [c.components for c in heuristic.cycles]
        for best, approx in zip(exact.cycles, heuristic.cycles, strict=True):
            for cycle in (best, approx):
                kept = [e for e in cycle.edges if e not in cycle.cut]
                assert _acyclic(cycle.components, kept)  # every cut breaks the cycle
            assert sum(e.weight for e in approx.cut) >= sum(e.weight for e in best.cut)
