"""Health over time (ADD-A6) on the real stack: two reviews of the configuration fixture, the second
without its Kubernetes manifests. The history shows both, oldest first, each computed from what
that review found: fewer open issues and fewer checkpoints needing work the second time."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration


async def test_health_history_follows_the_reviews(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("Health over time")
        source = prepare_fixture("nfr-config", tmp_path / "first")
        first = await stack.zip_intake(project, zip_directory(source))
        await stack.scan_and_wait(project, first["snapshot_id"])
        url = f"/v1/projects/{project}/health-history"
        one = await stack.ok("GET", url)
        assert len(one["points"]) == 1

        shutil.rmtree(source / "deploy")
        second = await stack.zip_intake(project, zip_directory(source))
        await stack.scan_and_wait(project, second["snapshot_id"])
        history = await stack.ok("GET", url)
        before, after = history["points"]
        assert before["snapshot_id"] == first["snapshot_id"]
        assert after["snapshot_id"] == second["snapshot_id"]
        assert before["reviewed_at"] < after["reviewed_at"]
        assert sum(after["issues"].values()) < sum(before["issues"].values())
        assert sum(after["failing"].values()) < sum(before["failing"].values())
        assert set(before["areas"]) == {
            "security",
            "reliability",
            "performance",
            "operations",
            "architecture",
            "experience",
        }
        assert before["upload"] and before["upload"].endswith(".zip")
        # History counts what each review found. Insights keeps issues open until a check shows
        # them gone (the removed manifests' issues are not verified fixed), so it never shows less.
        insights = await stack.ok("GET", f"/v1/projects/{project}/insights")
        failing = [c for c in insights["checkpoints"] if c["status"] in {"attention", "missing"}]
        assert sum(after["failing"].values()) <= len(failing)
        assert (await stack.client.get(f"{url}?limit=1")).status_code == 400
