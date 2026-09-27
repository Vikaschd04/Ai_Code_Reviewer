"""Phase 1 pipeline on real infrastructure: live HTTP API (uvicorn), Temporal dev server, worker,
PostgreSQL 18, filesystem artifact store and the real PMD/ESLint engines. Fake engine launchers
are used only where a crash or hang must be simulated, and are labelled as such."""

from __future__ import annotations

import asyncio
import contextlib
import io
import socket
import time
import uuid
import zipfile
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from crp_analysis.engines.eslint import EslintAdapter
from crp_analysis.engines.pmd import PmdAdapter, ruleset_sha256
from crp_core.config import Settings
from crp_core.db.models import Intake, Project, Source, Workspace
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_devtools.testing.fixture_projects import prepare_fixture, zip_directory
from crp_runner.api import PlatformClient
from crp_runner.cli import capture_and_upload

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
TERMINAL_SCAN = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "BLOCKED", "BUDGET_EXHAUSTED"}

StackFactory = Callable[..., contextlib.AbstractAsyncContextManager[Any]]


def _fake_pmd(tmp_path: Path, script: str) -> PmdAdapter:
    home = tmp_path / "fake-pmd"
    (home / "bin").mkdir(parents=True)
    (home / "lib").mkdir()
    (home / "lib" / "pmd-core-0.0.0.jar").write_bytes(b"")
    (home / "bin" / "pmd").write_text("#!/bin/sh\n" + script)
    (home / "bin" / "pmd").chmod(0o755)
    return PmdAdapter(home, java_heap="256m", timeout_seconds=120, max_output_bytes=1_000_000)


def _real_eslint() -> EslintAdapter:
    return EslintAdapter(
        REPO / "engines" / "eslint-runner",
        node_executable=None,
        timeout_seconds=120,
        max_output_bytes=10_000_000,
    )


async def test_zip_and_folder_intake_are_equivalent_and_findings_persist(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    folder = prepare_fixture("seeded-mixed", tmp_path / "seeded")
    async with stack_factory(settings) as stack:
        project = await stack.project()

        zipped = await stack.zip_intake(project, zip_directory(folder))
        assert zipped["state"] == "READY", zipped
        snapshot = await stack.ok("GET", f"/v1/snapshots/{zipped['snapshot_id']}")
        indicators = {(i["name"], i["version"]) for i in snapshot["inventory"]["indicators"]}
        assert {("Java", "17"), ("Spring Boot", "3.3.4"), ("React", "^18.3.1")} <= indicators
        assert snapshot["inventory"]["agent_instruction_files"] == ["AGENTS.md"]
        files = {
            f["path"]: f
            for f in (await stack.ok("GET", f"/v1/snapshots/{snapshot['id']}/files?limit=500"))[
                "items"
            ]
        }
        assert (
            files[".env"]["disposition"] == "EXCLUDED"
            and files[".env"]["reason"] == "secret_candidate"
        )
        assert files["node_modules/leftpad/index.js"]["reason"] == "dependency_vendor"
        assert files["assets/logo.png"]["disposition"] == "BINARY"
        assert files["AGENTS.md"]["category"] == "agent_instructions"

        platform = PlatformClient(httpx.Client(timeout=30), stack.server.url, stack.token)
        runner_result = await asyncio.to_thread(
            capture_and_upload,
            folder,
            project,
            platform,
            api_url=stack.server.url,
            dry_run=False,
            assume_yes=True,
            start_scan=False,
            out=lambda _line: None,
        )
        assert runner_result is not None and runner_result["state"] == "READY", runner_result
        runner_snapshot = await stack.ok("GET", f"/v1/snapshots/{runner_result['snapshot_id']}")
        assert runner_snapshot["source_mode"] == "local_runner"
        assert runner_snapshot["manifest_sha256"] == snapshot["manifest_sha256"]

        zip_scan = await stack.scan(project, snapshot["id"], key="zip-scan-0001")
        again = await stack.scan(project, snapshot["id"], key="zip-scan-0001")
        assert again["id"] == zip_scan["id"], "Idempotency-Key must return the same scan"
        runner_scan = await stack.scan(project, runner_snapshot["id"])
        zip_done = await stack.wait_scan(zip_scan["id"])
        runner_done = await stack.wait_scan(runner_scan["id"])
        assert zip_done["state"] == runner_done["state"] == "PARTIAL"
        engines = {e["engine"]: e for e in zip_done["engines"]}
        assert engines["pmd"]["engine_version"] == "7.27.0" and engines["pmd"]["state"] == "PARTIAL"
        assert engines["pmd"]["ruleset_sha256"] == ruleset_sha256(), (
            "AGENTS.md must not change rules"
        )
        assert (
            engines["eslint"]["engine_version"] == "10.11.0"
            and engines["eslint"]["state"] == "PARTIAL"
        )
        assert engines["structure"]["files_failed"] == 0

        zip_findings = await stack.findings(zip_scan["id"])
        runner_findings = await stack.findings(runner_scan["id"])
        assert {f["fingerprint"] for f in zip_findings} == {
            f["fingerprint"] for f in runner_findings
        }
        rules = {(f["path"].rsplit("/", 1)[-1], f["rule_id"]) for f in zip_findings}
        assert {
            ("InvoiceService.java", "UseEqualsToCompareStrings"),
            ("CryptoUtil.java", "HardCodedCryptoKey"),
            ("app.js", "no-eval"),
            ("cart.ts", "use-isnan"),
        } <= rules

        compare = next(f for f in zip_findings if f["rule_id"] == "UseEqualsToCompareStrings")
        detail = await stack.ok("GET", f"/v1/findings/{compare['id']}")
        source = detail["source"]
        flagged = source["lines"][compare["start_line"] - source["start_line"]]
        assert 'status == "PAID"' in flagged
        assert (
            detail["rule"]["recommendation"]
            and detail["manifest_sha256"] == snapshot["manifest_sha256"]
        )
        key = next(f for f in zip_findings if f["rule_id"] == "HardCodedCryptoKey")
        key_detail = await stack.ok("GET", f"/v1/findings/{key['id']}")
        assert key_detail["source"]["redactions"] >= 1
        assert "0123456789abcdef" not in "\n".join(key_detail["source"]["lines"])

        coverage = (await stack.ok("GET", f"/v1/scans/{zip_scan['id']}/coverage?outcome=FAILED"))[
            "items"
        ]
        failed = {(c["engine"], c["path"].rsplit("/", 1)[-1]) for c in coverage}
        assert {("pmd", "Broken.java"), ("eslint", "broken.ts")} <= failed

        async with stack.client.stream("GET", f"/v1/scans/{zip_scan['id']}/events") as stream:
            body = "".join([chunk async for chunk in stream.aiter_text()])
        for kind in ("scan_started", "engine_started", "engine_finished", "scan_finished", "end"):
            assert f"event: {kind}" in body
        assert (
            await stack.client.get(
                f"/v1/scans/{zip_scan['id']}/events", headers={"Last-Event-ID": "999999999"}
            )
        ).text.startswith("event: end")
        finding_count = len(zip_findings)

    # "Restart": a brand-new API process view over the same database still serves the results.
    async with stack_factory(settings) as restarted:
        persisted = await restarted.findings(zip_scan["id"])
        assert len(persisted) == finding_count


async def test_clean_and_empty_sources(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    async with stack_factory(settings) as stack:
        project = await stack.project()
        clean = await stack.zip_intake(
            project, zip_directory(prepare_fixture("clean-mixed", tmp_path / "clean"))
        )
        done = await stack.wait_scan((await stack.scan(project, clean["snapshot_id"]))["id"])
        assert done["state"] == "SUCCEEDED"
        assert await stack.findings(done["id"]) == []
        coverage = (await stack.ok("GET", f"/v1/scans/{done['id']}/coverage"))["items"]
        assert coverage and all(c["outcome"] == "ANALYZED" for c in coverage)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("README.md", "# nothing to analyze\n")
        empty = await stack.zip_intake(project, buffer.getvalue())
        empty_done = await stack.wait_scan((await stack.scan(project, empty["snapshot_id"]))["id"])
        assert empty_done["state"] == "SUCCEEDED"
        states = {e["engine"]: e["state"] for e in empty_done["engines"]}
        # Only Trivy's secret scanner applies to a README; every other engine is not applicable.
        assert states.pop("trivy") == "SUCCEEDED"
        assert set(states.values()) == {"NOT_APPLICABLE"}


async def test_malicious_archive_is_rejected_and_not_stored(
    settings: Settings, stack_factory: StackFactory
) -> None:
    async with stack_factory(settings) as stack:
        project = await stack.project()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("src/Main.java", "class Main {}")
            zf.writestr("../../escape.sh", "rm -rf /")
        result = await stack.zip_intake(project, buffer.getvalue())
        assert result["state"] == "REJECTED"
        assert result["error_code"] == "path_traversal"
        assert result["snapshot_id"] is None
        assert "escape" in str(result["error_details"])
        assert not (
            settings.artifact_root / "intakes" / result["id"].replace("-", "") / "archive.zip"
        ).exists()
        assert not (settings.artifact_root / "blobs").exists()


async def test_intake_contract_errors(settings: Settings, stack_factory: StackFactory) -> None:
    small = settings.model_copy(update={"intake_max_upload_bytes": 2048})
    async with stack_factory(small) as stack:
        project = await stack.project()
        intake = await stack.ok(
            "POST",
            f"/v1/projects/{project}/intakes",
            json={"mode": "zip_upload", "display_name": "x"},
        )
        path = f"/v1/intakes/{intake['id']}"
        assert (await stack.client.post(f"{path}/finalize")).json()["code"] == "no_content"
        wrong_type = await stack.client.put(
            f"{path}/content", content=b"x", headers={"Content-Type": "text/plain"}
        )
        assert wrong_type.status_code == 415
        declared = await stack.client.put(
            f"{path}/content", content=b"x" * 4096, headers={"Content-Type": "application/zip"}
        )
        assert declared.status_code == 413 and declared.json()["code"] == "upload_too_large"

        async def unsized() -> AsyncIterator[bytes]:  # no Content-Length: limit enforced on bytes
            for _ in range(4):
                yield b"y" * 1024

        streamed = await stack.client.put(
            f"{path}/content", content=unsized(), headers={"Content-Type": "application/zip"}
        )
        assert streamed.status_code == 413
        assert list((small.work_root / "uploads").glob("*.part")) == [], (
            "temp upload must be removed"
        )
        assert (await stack.ok("GET", path))["state"] == "CREATED"
        manifest = await stack.client.put(f"{path}/client-manifest", content=b"{}")
        assert manifest.status_code in {400, 409}
        for unknown in (
            "/v1/intakes/",
            "/v1/scans/",
            "/v1/findings/",
            "/v1/snapshots/",
            "/v1/files/",
        ):
            assert (
                await stack.client.get(unknown + "00000000-0000-4000-8000-000000000000")
            ).status_code == 404

        # Interrupted upload: the client disconnects mid-body; nothing is stored or left behind.
        def interrupted_upload() -> None:
            with socket.create_connection(("127.0.0.1", stack.server.port), timeout=5) as sock:
                request = (
                    f"PUT {path}/content HTTP/1.1\r\nHost: 127.0.0.1:{stack.server.port}\r\n"
                    f"Authorization: Bearer {stack.token}\r\nContent-Type: application/zip\r\n"
                    "Content-Length: 1500\r\n\r\n"
                )
                sock.sendall(request.encode() + b"z" * 500)

        await asyncio.to_thread(interrupted_upload)
        await asyncio.sleep(1)
        assert (await stack.ok("GET", path))["state"] == "CREATED"
        assert list((small.work_root / "uploads").glob("*.part")) == []
        assert not (small.artifact_root / "intakes" / str(intake["id"]).replace("-", "")).exists()

        # Another workspace's intake is invisible (404, existence not revealed).
        engine = create_engine_from_settings(small)
        async with transaction(create_session_factory(engine)) as session:
            other_ws, other_project, other_source, other_intake = (uuid.uuid4() for _ in range(4))
            session.add(Workspace(id=other_ws, slug=f"other-{other_ws.hex[:8]}", name="Other"))
            await session.flush()
            session.add(
                Project(id=other_project, workspace_id=other_ws, slug="p", name="P", origin="user")
            )
            await session.flush()
            session.add(
                Source(
                    id=other_source,
                    workspace_id=other_ws,
                    project_id=other_project,
                    mode="zip_upload",
                    display_name="x",
                )
            )
            await session.flush()
            session.add(
                Intake(
                    id=other_intake,
                    workspace_id=other_ws,
                    project_id=other_project,
                    source_id=other_source,
                    mode="zip_upload",
                    state="CREATED",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
        await engine.dispose()
        for probe in (f"/v1/intakes/{other_intake}", f"/v1/projects/{other_project}/snapshots"):
            assert (await stack.client.get(probe)).status_code == 404
        assert (await stack.client.post(f"/v1/intakes/{other_intake}/finalize")).status_code == 404

        canceled = await stack.ok("POST", f"{path}/cancel")
        assert canceled["state"] == "CANCELED"
        assert (
            await stack.client.put(
                f"{path}/content", content=b"PK", headers={"Content-Type": "application/zip"}
            )
        ).status_code == 409


async def test_failed_engine_keeps_completed_results(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    crashing = _fake_pmd(
        tmp_path, "echo 'fatal: simulated PMD crash' >&2\nexit 1\n"
    )  # labelled test double
    folder = prepare_fixture("seeded-mixed", tmp_path / "seeded")
    async with stack_factory(
        settings, adapters={"pmd": crashing, "eslint": _real_eslint()}
    ) as stack:
        project = await stack.project()
        intake = await stack.zip_intake(project, zip_directory(folder))
        done = await stack.wait_scan((await stack.scan(project, intake["snapshot_id"]))["id"])
        engines = {e["engine"]: e for e in done["engines"]}
        assert done["state"] == "PARTIAL"
        assert (
            engines["pmd"]["state"] == "FAILED" and engines["pmd"]["error_code"] == "engine_crashed"
        )
        assert engines["eslint"]["findings_count"] > 0
        assert any("pmd" in item for item in done["summary"]["limitations"])
        findings = await stack.findings(done["id"])
        assert findings and all(f["engine"] != "pmd" for f in findings)  # crashed run: none
        assert {"eslint", "opengrep"} <= {f["engine"] for f in findings}
        pmd_coverage = (await stack.ok("GET", f"/v1/scans/{done['id']}/coverage?engine=pmd"))[
            "items"
        ]
        assert pmd_coverage and all(c["outcome"] == "NOT_ATTEMPTED" for c in pmd_coverage)


async def test_scan_cancellation(
    settings: Settings, tmp_path: Path, stack_factory: StackFactory
) -> None:
    hanging = _fake_pmd(tmp_path, "sleep 120\n")  # labelled test double
    async with stack_factory(
        settings, adapters={"pmd": hanging, "eslint": _real_eslint()}
    ) as stack:
        project = await stack.project()
        intake = await stack.zip_intake(
            project, zip_directory(prepare_fixture("seeded-mixed", tmp_path / "s"))
        )
        scan = await stack.scan(project, intake["snapshot_id"])
        for _ in range(100):
            current = await stack.ok("GET", f"/v1/scans/{scan['id']}")
            if any(e["engine"] == "pmd" and e["state"] == "RUNNING" for e in current["engines"]):
                break
            await asyncio.sleep(0.2)
        started = time.monotonic()
        await stack.ok("POST", f"/v1/scans/{scan['id']}/cancel")
        await stack.ok("POST", f"/v1/scans/{scan['id']}/cancel")
        done = await stack.wait_scan(scan["id"], limit_seconds=60)
        assert time.monotonic() - started < 45
        assert done["state"] == "CANCELED"
        engines = {e["engine"]: e for e in done["engines"]}
        assert engines["pmd"]["state"] == "CANCELED"
