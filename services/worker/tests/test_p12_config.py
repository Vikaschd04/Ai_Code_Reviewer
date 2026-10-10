"""Configuration and infrastructure evidence (P12 slice 2, ADR 0023) on the real stack: one review
of the configuration fixture runs the ``nfr`` engine and Trivy's misconfiguration checks; gaps
become tracked issues that the NFR checkpoints pick up, and what could not be checked is reported
as such."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration


async def test_configuration_evidence_from_a_real_review(
    settings: Settings, tmp_path: Path
) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P12 configuration")
        source = prepare_fixture("nfr-config", tmp_path / "nfr-config")
        intake = await stack.zip_intake(project, zip_directory(source))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        engines = {e["engine"]: e for e in scan["engines"]}
        nfr = engines["nfr"]
        # The Helm template is not attempted and the broken manifest failed: never clean.
        assert nfr["state"] == "PARTIAL", nfr
        assert nfr["files_failed"] == 1
        assert nfr["diagnostics"]["templates"] == 1
        assert engines["trivy"]["state"] == "SUCCEEDED"
        assert engines["trivy"]["diagnostics"]["misconfig_unrendered"] == ["chart"]

        findings = await stack.findings(scan["id"])
        config = {(f["rule_id"], f["path"]) for f in findings if f["engine"] == "nfr"}
        assert ("crp.nfr.k8s.single-replica", "deploy/base/worker.yaml") in config
        assert (
            "crp.nfr.spring.schema-auto-update",
            "src/main/resources/application-prod.properties",
        ) in config
        assert len(config) == 9
        prod = next(f for f in findings if f["path"].endswith("application-prod.properties"))
        assert prod["severity"] == "high" and prod["title"] == (
            "Database schema changed automatically at startup"
        )
        misconfig = {
            (f["rule_id"], f["path"]) for f in findings if f["rule_id"].startswith("misconfig:")
        }
        assert ("misconfig:DS-0002", "Dockerfile") in misconfig
        assert ("misconfig:KSV-0017", "deploy/base/shop.yaml") in misconfig

        page = await stack.ok("GET", f"/v1/projects/{project}/issues?engine=nfr&limit=50")
        assert page["total"] == 9 and {i["status"] for i in page["items"]} == {"OPEN"}

        data = await stack.ok("GET", f"/v1/projects/{project}/insights")
        checks = {c["id"]: c for c in data["checkpoints"]}
        instances = checks["reliability.instances"]  # shop, cache (autoscaler min 1), worker
        assert instances["status"] == "attention" and instances["issue_count"] == 3
        assert {e["signal"] for e in instances["evidence"]} == {"multiple-instances", "multi-zone"}
        assert checks["reliability.health"]["issue_count"] == 1  # shop has no readiness probe
        rollouts = checks["reliability.rollouts"]
        assert rollouts["issue_count"] == 1  # Recreate
        assert {e["signal"] for e in rollouts["evidence"]} == {
            "disruption-budget",
            "graceful-shutdown",
        }
        schema = checks["reliability.data"]
        assert schema["issue_count"] == 2 and schema["priority"] == "high"  # create drops data
        assert checks["performance.capacity"]["status"] == "attention"
        assert checks["performance.autoscaling"]["status"] == "in_place"
        security = checks["security.configuration"]  # Trivy's checks and the Actuator settings
        assert security["priority"] == "high" and security["issue_count"] >= 10
        assert security["issues"][0]["severity"] == "high"  # the five most severe are shown
        assert [e["signal"] for e in data["not_checked"]] == ["helm-charts", "terraform"]

        # Resolving: the recipes fix every configuration issue in a workspace, and the
        # workspace check confirms each one is gone (the upload itself is untouched).
        targets = [f for f in findings if f["engine"] == "nfr"]
        ws = await stack.ok("POST", f"/v1/projects/{project}/change-sets", json={})
        fixed = await stack.ok(
            "POST",
            f"/v1/change-sets/{ws['id']}/fixes",
            json={"version": ws["version"], "finding_ids": [f["id"] for f in targets]},
        )
        assert {a["finding_id"] for a in fixed["applied"]} == {f["id"] for f in targets}, fixed
        started = await stack.ok("POST", f"/v1/change-sets/{ws['id']}/checks")
        check = await _wait_check(stack, started["id"])
        assert check["state"] in {"SUCCEEDED", "PARTIAL"}, check
        outcomes = check["result"]["outcomes"]
        assert {outcomes[f["id"]] for f in targets} == {"fixed"}, outcomes
        new_config = [i for i in check["result"]["new_items"] if i["engine"] == "nfr"]
        assert new_config == []


async def _wait_check(stack: Any, check_id: str) -> dict[str, Any]:
    for _ in range(600):
        check = await stack.ok("GET", f"/v1/change-set-checks/{check_id}")
        if check["state"] in {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}:
            return dict(check)
        await asyncio.sleep(0.3)
    raise AssertionError("workspace check did not finish")
