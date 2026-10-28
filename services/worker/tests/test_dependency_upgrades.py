"""Dependency upgrades as fixes (ADD-A2, ADR 0027) on the real stack: the security fixture declares
log4j-core 2.14.1 in pom.xml and lodash 4.17.15 in an npm lockfile. "Fix automatically" on the
vulnerable-libraries checkpoint upgrades log4j-core once to the version that fixes all of its
reported vulnerabilities; Trivy's re-check on the workspace copy confirms them fixed. The npm
lockfile is left for a hand fix (its integrity hashes come from the registry)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration
LOG4J = "org.apache.logging.log4j:log4j-core"


async def _wait_check(stack: Any, check_id: str) -> dict[str, Any]:
    for _ in range(900):
        check = await stack.ok("GET", f"/v1/change-set-checks/{check_id}")
        if check["state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}:
            return dict(check)
        await asyncio.sleep(0.3)
    raise AssertionError("workspace check did not finish")


async def test_vulnerable_libraries_are_upgraded_and_confirmed_fixed(
    settings: Settings, tmp_path: Path
) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("Dependency upgrades")
        source = prepare_fixture("security-mixed", tmp_path / "security-mixed")
        intake = await stack.zip_intake(project, zip_directory(source))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        findings = await stack.findings(scan["id"])
        log4j = [f for f in findings if (f.get("details") or {}).get("package") == LOG4J]
        lodash = [f for f in findings if (f.get("details") or {}).get("package") == "lodash"]
        assert log4j and lodash, {f["rule_id"] for f in findings if f["engine"] == "trivy"}
        assert {f["path"] for f in log4j} == {"pom.xml"}

        data = await stack.ok("GET", f"/v1/projects/{project}/insights")
        libraries = next(c for c in data["checkpoints"] if c["id"] == "security.dependencies")
        fixable = set(libraries["fixable_issue_ids"])
        upgradable = {
            f["issue"]["id"] for f in log4j if (f.get("details") or {}).get("fixed_version")
        }
        assert fixable == upgradable, libraries  # lodash (npm lockfile) is fixed by hand
        assert len(fixable) < libraries["issue_count"]

        ws = await stack.ok("POST", f"/v1/projects/{project}/change-sets", json={})
        result = await stack.ok(
            "POST",
            f"/v1/change-sets/{ws['id']}/fixes",
            json={"version": ws["version"], "issue_ids": sorted(fixable), "check": True},
        )
        assert result["skipped"] == [], result["skipped"]
        changed = [a for a in result["applied"] if not a["title"].startswith("Fixed by the same")]
        assert len(changed) == 1 and changed[0]["recipe_id"] == "deps:maven-upgrade"
        assert changed[0]["title"].startswith("Upgrade log4j-core 2.14.1 to 2.")
        assert len(result["applied"]) == len(fixable)  # one upgrade fixes them all

        pom = await stack.ok("GET", f"/v1/change-sets/{ws['id']}/file", params={"path": "pom.xml"})
        target = changed[0]["title"].split(" to ")[1].split(" ")[0]
        assert f"<version>{target}</version>" in pom["content"]
        assert "2.14.1" not in pom["content"]
        assert pom["flags"] == []  # a version-only change is not a build-configuration change

        check = await _wait_check(stack, result["check"]["id"])
        assert check["state"] in {"SUCCEEDED", "PARTIAL"}, check
        outcomes = check["result"]["outcomes"]
        upgraded = [f for f in log4j if f["issue"]["id"] in fixable]
        assert {outcomes[f["id"]] for f in upgraded} == {"fixed"}, outcomes
        assert {outcomes[f["id"]] for f in lodash} == {"still_present"}
        new = [i for i in check["result"]["new_items"] if i["engine"] == "trivy"]
        assert all(LOG4J not in str(i) for i in new), new

        again = await stack.ok(
            "POST",
            f"/v1/change-sets/{ws['id']}/fixes",
            json={"version": result["change_set"]["version"], "issue_ids": sorted(fixable)},
        )
        assert again["applied"] == []  # idempotent: every issue is already changed
        assert {s["reason"][:15] for s in again["skipped"]} == {"Already changed"}
