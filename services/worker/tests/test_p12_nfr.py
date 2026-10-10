"""NFR readiness (P12 slice 1) on the real stack: a reviewed upload gives cited evidence (declared
libraries with their manifest lines, files) and gaps (tracked issues); the team's profile adds
targets and attested answers; nothing found stays "not checked yet"."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pytest

from crp_core.config import Settings
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory

from .conftest import lite_stack

pytestmark = pytest.mark.integration


def _zip(tmp_path: Path) -> bytes:
    return zip_directory(prepare_fixture("nfr-mixed", tmp_path / "nfr-mixed"))


def _questions(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {q["id"]: q for aspect in data["aspects"] for q in aspect["questions"]}


async def test_nfr_readiness_from_a_real_review(settings: Settings, tmp_path: Path) -> None:
    async with lite_stack(settings) as stack:
        project = await stack.project("P12 NFR")
        url = f"/v1/projects/{project}/nfr"
        before = await stack.ok("GET", url)
        assert before["basis"] is None and before["profile_version"] == 0
        assert before["counts"]["not_checked"] + before["counts"]["needs_input"] == 23
        assert len(before["targets"]) == 10 and before["can_edit"] is True

        intake = await stack.zip_intake(project, _zip(tmp_path))
        scan = await stack.scan_and_wait(project, intake["snapshot_id"])
        assert scan["state"] in {"SUCCEEDED", "PARTIAL"}
        data = await stack.ok("GET", url)
        assert data["basis"]["snapshot_id"] == intake["snapshot_id"]
        questions = _questions(data)

        recovery = questions["recoverability.recovery-time"]
        signals = {e["signal"]: e for e in recovery["evidence"]}
        assert set(signals) == {"health-endpoints", "ci-pipeline"}
        assert signals["health-endpoints"]["locations"] == [
            {
                "path": "pom.xml",
                "line": 9,
                "detail": "org.springframework.boot:spring-boot-starter-actuator",
            }
        ]
        # Evidence alone does not answer it: the recovery time objective is the team's.
        assert recovery["status"] == "needs_input" and recovery["team_required"] is True

        consistency = questions["reliability.consistency"]
        assert consistency["status"] == "needs_work"  # the empty catch is an open issue
        assert consistency["gaps"]["open"] >= 1
        assert {e["signal"] for e in consistency["evidence"]} >= {
            "circuit-breakers",
            "automated-tests",
            "ci-pipeline",
        }
        assert questions["security.attacks"]["status"] == "needs_work"  # eval()
        assert questions["portability.platforms"]["status"] == "evidence"  # Dockerfile
        assert questions["portability.data-exchange"]["status"] == "evidence"  # openapi.yaml
        assert questions["recoverability.data"]["evidence"][0]["signal"] == "db-migrations"
        availability = questions["availability.continuous"]
        assert availability["status"] == "needs_input"
        assert [e["signal"] for e in availability["context"]] == ["deployment-manifests"]
        assert questions["usability.simplicity"]["status"] == "not_checked"

        # The team adds targets and answers; a new profile version.
        document = {
            "targets": {"rto_minutes": 30, "availability_percent": 99.9},
            "answers": {
                "recoverability.cost": {"text": "About 20k EUR per hour of downtime."},
                "usability.simplicity": {"not_applicable": True, "reason": "Back-office API"},
            },
        }
        saved = await stack.ok(
            "PUT",
            f"{url}/profile",
            json={"document": document, "note": "First targets", "base_version": 0},
        )
        assert saved["profile_version"] == 1 and saved["profile_note"] == "First targets"
        questions = _questions(saved)
        assert questions["recoverability.recovery-time"]["status"] == "evidence"
        assert questions["recoverability.recovery-time"]["values"] == {"rto_minutes": 30}
        assert questions["availability.continuous"]["status"] == "evidence"
        assert questions["recoverability.cost"]["status"] == "answered"
        assert questions["usability.simplicity"]["status"] == "not_applicable"
        assert saved["history"][0]["created_by"]

        stale = await stack.client.put(
            f"{url}/profile", json={"document": document, "base_version": 0}
        )
        assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
        invalid = await stack.client.put(
            f"{url}/profile",
            json={
                "document": {"answers": {"usability.simplicity": {"not_applicable": True}}},
                "base_version": 1,
            },
        )
        assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_profile"
        assert any("say why" in p for p in invalid.json()["details"]["problems"])
        unknown = await stack.client.put(
            f"{url}/profile",
            json={"document": {"answers": {"made.up": {"text": "x"}}}, "base_version": 1},
        )
        assert unknown.status_code == 422

        exported = await stack.client.get(f"{url}/export?format=csv")
        assert exported.status_code == 200 and exported.headers["content-type"].startswith(
            "text/csv"
        )
        rows = list(csv.reader(io.StringIO(exported.text)))
        assert len(rows) == 24
        cost = next(
            r for r in rows if r[2].startswith("What would be the impact in terms of costs")
        )
        assert cost[3] == "Answered by your team" and cost[8].startswith("About 20k EUR")
        report = await stack.client.get(f"{url}/export?format=md")
        assert report.text.startswith("# NFR readiness: P12 NFR")
        assert "NFR profile version 1." in report.text
        assert "refactorX does not certify compliance." in report.text
