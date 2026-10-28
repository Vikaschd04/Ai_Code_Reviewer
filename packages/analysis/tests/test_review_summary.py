"""A reviewer's summary of a fix workspace (ADD-A5): impact from the dependency map, what needs a
person, and the Markdown for a pull request."""

from __future__ import annotations

from crp_analysis.architecture.metrics import CodeFile, Dependency
from crp_analysis.architecture.model import ArchitectureModel
from crp_analysis.fixes.review_summary import (
    CheckState,
    FileChange,
    Impact,
    impact,
    markdown,
    needs_human,
)

SERVICE = "src/main/java/shop/service/PriceService.java"


def _model() -> ArchitectureModel:
    files = [
        CodeFile(SERVICE, 40, "shop.service"),
        CodeFile("src/main/java/shop/web/CartController.java", 30, "shop.web"),
        CodeFile("src/main/java/shop/web/Routes.java", 20, "shop.web"),
        CodeFile("src/main/java/shop/batch/Nightly.java", 20, "shop.batch"),
        CodeFile("src/test/java/shop/service/PriceServiceTest.java", 50, "shop.service", test=True),
        CodeFile("gensrc/shop/Model.java", 900, "shop.gen", generated=True),
        CodeFile("src/main/java/shop/Unrelated.java", 10, "shop"),
    ]
    deps = [
        Dependency("src/main/java/shop/web/CartController.java", SERVICE),
        Dependency(
            "src/main/java/shop/web/Routes.java", "src/main/java/shop/web/CartController.java"
        ),
        Dependency("src/main/java/shop/batch/Nightly.java", target_package="shop.service"),
        Dependency("src/test/java/shop/service/PriceServiceTest.java", SERVICE),
        Dependency("gensrc/shop/Model.java", SERVICE),
        Dependency(SERVICE, "src/main/java/shop/Unrelated.java"),  # what it uses is not impact
    ]
    return ArchitectureModel(files=files, dependencies=deps)


def test_impact_follows_users_two_steps_and_finds_the_tests() -> None:
    found = impact(_model(), [SERVICE])
    assert found.available
    assert found.direct == [
        "src/main/java/shop/batch/Nightly.java",  # on-demand import of the package
        "src/main/java/shop/web/CartController.java",
    ]
    assert found.indirect == ["src/main/java/shop/web/Routes.java"]
    assert found.tests == ["src/test/java/shop/service/PriceServiceTest.java"]
    assert found.parts == ["shop.batch", "shop.web"]  # generated code is not the team's design
    assert impact(None, [SERVICE]) == Impact(available=False)
    assert impact(_model(), ["src/main/java/shop/Unrelated.java"]).direct == [SERVICE]


def test_what_needs_a_person_comes_first_and_the_source_only_limit_is_always_said() -> None:
    upgrade = FileChange(
        "pom.xml", "modify", ("recipe",), (), ("Upgrade log4j-core 2.14.1 to 2.25.4",)
    )
    ai = FileChange(
        SERVICE, "modify", ("ai", "manual"), ("suppression_added",), ("Use BigDecimal",)
    )
    stale = CheckState(True, False, "SUCCEEDED", {"fixed": 3, "suppressed": 1, "new": 2})
    todo = needs_human([upgrade, ai], stale, impact(_model(), [SERVICE]))
    assert todo[0] == "Run the check again: the workspace changed after the last check."
    assert "2 new problem(s)" in todo[1] and "hidden, not fixed" in todo[2]
    assert any("adds a marker that silences a check" in t for t in todo)
    assert any(t.startswith("Read the AI-suggested changes") and SERVICE in t for t in todo)
    assert any("a library was upgraded" in t for t in todo)
    assert any("PriceServiceTest.java" in t for t in todo)
    assert todo[-1].startswith("refactorX checked the source only")
    unchecked = needs_human([], CheckState(False, False, None, {}), Impact(available=False))
    assert unchecked[0].startswith("Run the check:") and len(unchecked) == 2


def test_markdown_for_a_pull_request() -> None:
    changes = [FileChange(SERVICE, "modify", ("recipe",), (), ("Compare strings with equals",))]
    check = CheckState(True, True, "SUCCEEDED", {"fixed": 1, "still_present": 0, "new": 0})
    found = impact(_model(), [SERVICE])
    text = markdown("Fixes", "shop.zip", changes, check, found, needs_human(changes, check, found))
    assert "## What changed and why" in text and f"`{SERVICE}`: Compare strings with equals" in text
    assert "1 fixed, 0 still present, 0 hidden (not fixed), 0 new." in text
    assert "2 file(s) use the changed files directly, 1 more through them" in text
    assert "reflection and dependency injection are not seen" in text
    assert "## Needs a person" in text
