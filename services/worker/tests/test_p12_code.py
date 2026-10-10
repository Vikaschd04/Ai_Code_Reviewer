"""Code-pattern evidence (P12 slice 3) on the real stack: calls without timeouts and blocking or
unbounded concurrency become tracked issues that put the matching NFR checkpoints on the list
to resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration


async def test_code_patterns_feed_the_nfr_checkpoints(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P12 code patterns")
        source = prepare_fixture("nfr-code", tmp_path / "nfr-code")
        intake = await stack.zip_intake(project, zip_directory(source))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        assert scan["state"] in {"SUCCEEDED", "PARTIAL"}
        data = await stack.ok("GET", f"/v1/projects/{project}/insights")
        checks = {c["id"]: c for c in data["checkpoints"]}
        calls = checks["reliability.fault-tolerance"]  # 4 Java clients and 2 axios instances
        assert calls["status"] == "attention" and calls["issue_count"] == 6
        assert calls["priority"] == "medium"
        assert {i["path"].rsplit("/", 1)[-1] for i in calls["issues"]} <= {
            "UnsafeClients.java",
            "unsafeApi.ts",
        }
        hot = checks["performance.code"]  # two blocking calls and a cached thread pool
        assert hot["status"] == "attention" and hot["issue_count"] == 3
        findings = await stack.findings(scan["id"])
        safe = [
            (f["engine"], f["rule_id"], f["path"], f["start_line"])
            for f in findings
            if f["engine"] == "opengrep" and f["path"].rsplit("/", 1)[-1].startswith("Safe")
        ]  # the negative examples (other engines may still report style issues there)
        assert not safe, safe
