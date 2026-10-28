"""A code tour for newcomers (ADD-A7) on the real stack: on the graph fixture, the tour names the
two parts and how they depend on each other, and starts with the class others extend."""

from __future__ import annotations

from pathlib import Path

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration
ENTITY = "core/src/main/java/com/acme/core/Entity.java"


async def test_the_tour_comes_from_the_review(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("Code tour")
        url = f"/v1/projects/{project}/tour"
        empty = await stack.ok("GET", url)
        assert empty["snapshot_id"] is None and empty["parts"] == []  # before a review
        source = prepare_fixture("graph-mixed", tmp_path / "graph")
        intake = await stack.zip_intake(project, zip_directory(source))
        await stack.scan_and_wait(project, intake["snapshot_id"])
        tour = await stack.ok("GET", url)
        assert tour["snapshot_id"] == intake["snapshot_id"] and tour["map_available"]
        assert "java" in {lang["language"] for lang in tour["languages"]}
        parts = {p["name"]: p for p in tour["parts"]}
        assert {"com.acme.app", "com.acme.core"} <= set(parts)
        assert "com.acme.core" in parts["com.acme.app"]["uses"]
        assert parts["com.acme.core"]["used_by"] >= 1
        start = [f["path"] for f in tour["start_here"]]
        assert ENTITY in start
        assert next(f for f in tour["start_here"] if f["path"] == ENTITY)["reason"].startswith(
            "used by "
        )
        assert tour["entry_points"] == []  # Main extends Thread; there is no main method
