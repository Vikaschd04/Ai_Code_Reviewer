"""Configuration and infrastructure evidence (P12 slice 2, ADR 0023) on the real stack: one review
of the configuration fixture runs the ``nfr`` engine and Trivy's misconfiguration checks; gaps
become tracked issues that the NFR questionnaire and the insights pick up, and what could not be
checked is reported as such."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration


def _questions(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {q["id"]: q for aspect in data["aspects"] for q in aspect["questions"]}


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

        questions = _questions(await stack.ok("GET", f"/v1/projects/{project}/nfr"))
        continuous = questions["availability.continuous"]
        assert continuous["status"] == "needs_work" and continuous["gaps"]["open"] >= 4
        assert {e["signal"] for e in continuous["evidence"]} >= {
            "multiple-instances",
            "disruption-budget",
            "graceful-shutdown",
        }
        assert [e["signal"] for e in continuous["context"]] == ["helm-charts"]
        assert questions["recoverability.data"]["gaps"]["open"] == 2  # ddl-auto update, create
        assert questions["scalability.spikes"]["evidence"][0]["signal"] == "autoscaling"

        insights = await stack.ok("GET", f"/v1/projects/{project}/insights")
        recs = {r["id"]: r for r in insights["recommendations"]}
        assert recs["reliability.deployment"]["issue_count"] == 5
        assert recs["reliability.schema"]["issue_count"] == 2
        assert recs["reliability.schema"]["priority"] == "high"  # create drops the data
        assert recs["performance.capacity"]["kind"] == "issues"
        security = recs["security.configuration"]  # Trivy's checks and the Actuator settings
        assert security["priority"] == "high" and security["issue_count"] >= 10
        assert security["issues"][0]["severity"] == "high"  # the five most severe are shown
