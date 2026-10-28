"""A reviewer's summary of a fix workspace (ADD-A5) on the real stack: on the graph fixture, a hand
edit of the core ``Entity`` class shows who depends on it (from the upload's dependency map),
asks for a check until one covers the current content, and reads as Markdown for a pull request."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration
ENTITY = "core/src/main/java/com/acme/core/Entity.java"


async def _wait_check(stack: Any, check_id: str) -> dict[str, Any]:
    for _ in range(900):
        check = await stack.ok("GET", f"/v1/change-set-checks/{check_id}")
        if check["state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}:
            return dict(check)
        await asyncio.sleep(0.3)
    raise AssertionError("workspace check did not finish")


async def test_the_summary_shows_impact_and_what_needs_a_person(
    settings: Settings, tmp_path: Path
) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("Review summary")
        source = prepare_fixture("graph-mixed", tmp_path / "graph")
        intake = await stack.zip_intake(project, zip_directory(source))
        await stack.scan_and_wait(project, intake["snapshot_id"])
        ws = await stack.ok("POST", f"/v1/projects/{project}/change-sets", json={})
        original = (source / ENTITY).read_text()
        edited = original.replace(
            "public abstract class Entity",
            "/** Base of all entities. */\npublic abstract class Entity",
        )
        await stack.ok(
            "PUT",
            f"/v1/change-sets/{ws['id']}/file",
            json={"version": ws["version"], "path": ENTITY, "content": edited},
        )
        url = f"/v1/change-sets/{ws['id']}/review-summary"
        summary = await stack.ok("GET", url)
        assert [c["path"] for c in summary["changes"]] == [ENTITY]
        assert summary["changes"][0]["reasons"] == ["Edited by hand"]
        assert summary["check"]["checked"] is False
        assert summary["needs_human"][0].startswith("Run the check:")
        assert summary["needs_human"][-1].startswith("refactorX checked the source only")
        impact = summary["impact"]
        assert impact["available"] and impact["direct_count"] >= 1
        assert "app/src/main/java/com/acme/app/Customer.java" in impact["direct"]
        assert "com.acme.app" in impact["parts"]
        assert "## Impact" in summary["markdown"] and f"`{ENTITY}`" in summary["markdown"]

        started = await stack.ok("POST", f"/v1/change-sets/{ws['id']}/checks")
        await _wait_check(stack, started["id"])
        after = await stack.ok("GET", url)
        assert after["check"]["checked"] and after["check"]["current"]
        assert not any(item.startswith("Run the check") for item in after["needs_human"])
        other = await stack.client.get(
            "/v1/change-sets/00000000-0000-0000-0000-000000000000/review-summary"
        )
        assert other.status_code == 404
