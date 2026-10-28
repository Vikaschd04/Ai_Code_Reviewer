"""A code tour for newcomers (ADD-A7): built only from what the review knows, each item naming the
files it comes from."""

from __future__ import annotations

from crp_analysis.architecture.metrics import CodeFile, Dependency
from crp_analysis.architecture.model import ArchitectureModel
from crp_analysis.architecture.tour import build_tour

ENTITY = "core/Entity.java"
REPO = "core/Repository.java"
CUSTOMER = "app/Customer.java"
MAIN = "app/Main.java"
INVENTORY = {
    "languages": [{"language": "java", "files": 5, "lines": 120}],
    "indicators": [
        {"name": "Spring Boot", "version": "3.3.4", "kind": "framework"},
        {"name": "Maven", "version": None, "kind": "build"},
        {"name": "Spring Boot", "version": "3.3.4", "kind": "framework"},
    ],
}


def _model() -> ArchitectureModel:
    files = [
        CodeFile(ENTITY, 20, "acme.core"),
        CodeFile(REPO, 15, "acme.core"),
        CodeFile(CUSTOMER, 30, "acme.app"),
        CodeFile(MAIN, 25, "acme.app"),
        CodeFile("app/CustomerTest.java", 40, "acme.app", test=True),
    ]
    deps = [
        Dependency(CUSTOMER, ENTITY),
        Dependency(REPO, ENTITY),
        Dependency(MAIN, CUSTOMER),
        Dependency(MAIN, REPO),
        Dependency("app/CustomerTest.java", CUSTOMER),  # tests are not where newcomers start
    ]
    return ArchitectureModel(files=files, dependencies=deps)


def test_the_tour_names_parts_starting_points_entries_and_hotspots() -> None:
    tour = build_tour(
        _model(),
        INVENTORY,
        [(MAIN, "method", "main"), (CUSTOMER, "method", "name"), ("tool.js", "function", "main")],
        [(CUSTOMER, "high"), (CUSTOMER, "low"), (REPO, "medium"), (MAIN, "info")],
        [ENTITY, REPO, CUSTOMER, MAIN, "README.md", "docs/adr/0001.md", "app/CustomerTest.java"],
    )
    assert tour.map_available
    assert tour.languages == [("java", 5, 120)]
    assert tour.technologies == ["Spring Boot 3.3.4", "Maven"]
    assert [(p.name, p.files, p.lines) for p in tour.parts] == [
        ("acme.app", 2, 55),
        ("acme.core", 2, 35),
    ]
    app = tour.parts[0]
    assert app.uses == ["acme.core"] and app.used_by == 0 and tour.parts[1].used_by == 1
    assert [(f.path, f.reason) for f in tour.start_here[:2]] == [
        (ENTITY, "used by 2 other files"),
        (CUSTOMER, "used by 1 other file"),
    ]
    assert [f.path for f in tour.entry_points] == [MAIN, "tool.js"]
    assert [f.path for f in tour.hotspots] == [CUSTOMER, REPO]  # info weighs nothing
    assert tour.hotspots[0].reason.startswith("open issues weigh 5")
    assert tour.docs == ["README.md", "docs/adr/0001.md"]


def test_without_a_dependency_map_the_tour_says_less_not_more() -> None:
    tour = build_tour(None, {}, [], [], ["src/a.py"])
    assert not tour.map_available
    assert tour.parts == [] and tour.start_here == [] and tour.languages == []
