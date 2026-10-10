"""NFR checkpoints (P12, ADR 0024) on the real stack: a reviewed upload gives each checkpoint a
status with cited evidence (declared libraries with their manifest lines, configuration read by
the ``nfr`` engine, files) or the open issues behind it; a mechanism the upload does not show can
be marked as handled elsewhere by the team, and the mark is shown as their statement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration


def _zip(tmp_path: Path) -> bytes:
    return zip_directory(prepare_fixture("nfr-mixed", tmp_path / "nfr-mixed"))


def _checks(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in data["checkpoints"]}


async def test_nfr_checkpoints_from_a_real_review(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P12 NFR")
        url = f"/v1/projects/{project}/insights"
        intake = await stack.zip_intake(project, _zip(tmp_path))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        assert scan["state"] in {"SUCCEEDED", "PARTIAL"}
        engines = {e["engine"]: e for e in scan["engines"]}
        assert engines["nfr"]["state"] == "SUCCEEDED"
        data = await stack.ok("GET", url)
        assert data["basis"]["snapshot_id"] == intake["snapshot_id"]
        checks = _checks(data)

        health = checks["reliability.health"]
        assert health["status"] == "in_place"
        evidence = {e["signal"]: e for e in health["evidence"]}
        assert evidence["health-endpoints"]["locations"] == [
            {
                "path": "pom.xml",
                "line": 9,
                "detail": "org.springframework.boot:spring-boot-starter-actuator",
            },
            {
                "path": "src/main/resources/application.yml",
                "line": 7,
                "detail": "management.endpoint.health.probes.enabled",
            },
        ]
        assert evidence["k8s-probes"]["locations"] == [
            {"path": "deploy/k8s/deployment.yaml", "line": 20, "detail": "shop: readinessProbe"}
        ]
        instances = checks["reliability.instances"]
        assert instances["status"] == "in_place"
        assert instances["evidence"][0]["locations"] == [
            {
                "path": "deploy/k8s/deployment.yaml",
                "line": 6,
                "detail": "Deployment shop: replicas: 2",
            }
        ]
        assert checks["reliability.rollouts"]["status"] == "in_place"  # graceful shutdown
        assert checks["reliability.data"]["evidence"][0]["signal"] == "db-migrations"
        assert checks["reliability.errors"]["status"] == "attention"  # the empty catch
        assert checks["security.injection"]["status"] == "attention"  # eval()
        assert checks["performance.autoscaling"]["status"] == "missing"  # no autoscaler
        api = checks["experience.api"]  # api/openapi.yaml describes it
        assert api["status"] == "in_place" and api["evidence"][0]["signal"] == "api-specs"
        assert checks["experience.accessibility"]["status"] == "not_applicable"  # no web UI
        assert data["not_checked"] == []  # no Helm charts, Terraform or load tests here

        # The team marks a missing mechanism as handled elsewhere: their statement, versioned.
        monitoring = checks["operations.monitoring"]
        assert monitoring["status"] == "missing" and monitoring["priority"] == "medium"
        marked = await stack.ok(
            "PUT",
            f"{url}/checkpoints/operations.monitoring/handled",
            json={"reason": "Prometheus scrapes the platform's sidecar"},
        )
        monitoring = _checks(marked)["operations.monitoring"]
        assert monitoring["status"] == "handled"
        assert monitoring["handled_reason"] == "Prometheus scrapes the platform's sidecar"
        assert marked["decisions_version"] == 1
        areas = {a["id"]: a for a in marked["areas"]}
        assert areas["operations"]["counts"]["handled"] == 1
        cleared = await stack.ok("DELETE", f"{url}/checkpoints/operations.monitoring/handled")
        assert _checks(cleared)["operations.monitoring"]["status"] == "missing"
