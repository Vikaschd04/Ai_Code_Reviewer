"""Boot the real hosted entrypoint (``crp-dev hosted``) and drive it over HTTP: health, Host
allowlist, ticket upload, a scan with every real engine, persistence and clean shutdown."""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from crp_core.local_secrets import generate_token
from crp_devtools.infra import PostgresCluster, free_port
from crp_devtools.stack import pnpm
from crp_devtools.testing.fixture_projects import prepare_fixture

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[3]
TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED"}


@dataclass
class Hosted:
    """One ``crp-dev hosted`` process tree that can be (re)started on the same database."""

    env: dict[str, str]
    token: str
    data: Path
    log_path: Path
    client: httpx.Client
    process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        with self.log_path.open("ab") as log:
            self.process = subprocess.Popen(  # noqa: S603 - fixed console script
                [str(Path(sys.executable).parent / "crp-dev"), "hosted"],
                cwd=REPO,
                env=self.env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + 120
        while True:
            if self.process.poll() is not None:
                pytest.fail(self.log_path.read_text()[-3000:])
            try:
                if self.client.get("/v1/health/live").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail("hosted API did not start")
            time.sleep(0.5)

    def stop(self, sig: signal.Signals = signal.SIGTERM) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        os.killpg(self.process.pid, sig)
        try:
            self.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=30)


def _prepare(pg_cluster: PostgresCluster, tmp_path: Path, profile: str) -> Hosted:
    trivy_cache = REPO / ".local" / "engines" / "trivy-cache"
    if not (trivy_cache / "db" / "trivy.db").is_file():
        pytest.fail("Trivy DB is not installed; run `make engines`")
    web = REPO / "apps" / "web" / "dist"
    if not (web / "index.html").is_file():
        subprocess.run(  # noqa: S603 - resolved pnpm, fixed arguments
            [pnpm(), "--filter", "@crp/web", "build"], cwd=REPO, check=True, capture_output=True
        )
    data = tmp_path / "data"
    data.mkdir()
    database = f"hosted_{uuid.uuid4().hex[:12]}"
    pg_cluster.create_database(database)
    token, port = generate_token(), free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("CRP_")}
    env.update(
        {
            "CRP_ACCESS_TOKEN": token,
            "CRP_PUBLIC_HOST": "127.0.0.1",
            "CRP_ALLOWED_WEB_ORIGINS": "https://app.example.test",
            "DATABASE_URL": pg_cluster.url(database).replace(
                "postgresql+psycopg://", "postgresql://"
            ),
            "CRP_DATA_DIR": str(data),
            "PORT": str(port),
            "CRP_TEMPORAL_PORT": str(free_port()),
            "CRP_TRIVY_DB_AUTO_REFRESH": "0",
            "CRP_WEB_STATIC_DIR": str(web),
            "CRP_PROFILE": profile,
            "CRP_DEMO_ENABLED": "true",
        }
    )
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    return Hosted(env, token, data, tmp_path / "hosted.log", client)


@pytest.fixture(params=["standard", "lite"])
def hosted_process(
    request: pytest.FixtureRequest, pg_cluster: PostgresCluster, tmp_path: Path
) -> Iterator[tuple[httpx.Client, str, Path]]:
    hosted = _prepare(pg_cluster, tmp_path, request.param)
    try:
        hosted.start()
        yield hosted.client, hosted.token, hosted.data
    finally:
        hosted.client.close()
        hosted.stop()


def _wait(client: httpx.Client, path: str, done: set[str], limit: float = 240) -> dict[str, Any]:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(path).json()
        if body["state"] in done:
            return body
        time.sleep(0.5)
    raise AssertionError(f"timed out waiting for {path}")


def _upload_fixture(client: httpx.Client, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a project and freeze a snapshot of the security fixture through a ticket upload."""
    workspace = client.get("/v1/auth/me").json()["workspaces"][0]["workspace_id"]
    project = client.post("/v1/projects", json={"workspace_id": workspace, "name": "Smoke"}).json()
    intake = client.post(
        f"/v1/projects/{project['id']}/intakes",
        json={"mode": "zip_upload", "display_name": "security.zip"},
    ).json()
    ticket = client.post(f"/v1/intakes/{intake['id']}/upload-ticket").json()
    assert ticket["upload_url"].startswith("https://127.0.0.1/v1/intakes/")
    folder = prepare_fixture("security-mixed", tmp_path / "security")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder).as_posix())
    target = ticket["upload_url"].removeprefix("https://127.0.0.1")
    uploaded = httpx.put(
        f"{client.base_url}{target}",
        content=buffer.getvalue(),
        headers={"Content-Type": "application/zip", "Origin": "https://app.example.test"},
        timeout=60,
    )  # no bearer token: the ticket alone authorizes the upload
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.headers["access-control-allow-origin"] == "https://app.example.test"
    client.post(f"/v1/intakes/{intake['id']}/finalize")
    snapshot = _wait(client, f"/v1/intakes/{intake['id']}", {"READY", "REJECTED", "FAILED"})
    assert snapshot["state"] == "READY", snapshot
    return project, snapshot


def test_hosted_container_entrypoint_end_to_end(
    hosted_process: tuple[httpx.Client, str, Path], tmp_path: Path
) -> None:
    client, _, data = hosted_process
    ready = client.get("/v1/health/ready")
    assert ready.status_code == 200, ready.text
    evil = client.get("/v1/auth/me", headers={"Host": "evil.example.com"})
    assert evil.status_code == 403
    project, snapshot = _upload_fixture(client, tmp_path)
    scan = client.post(
        f"/v1/projects/{project['id']}/scans", json={"snapshot_id": snapshot["snapshot_id"]}
    ).json()
    done = _wait(client, f"/v1/scans/{scan['id']}", TERMINAL)
    engines = {e["engine"]: e["state"] for e in done["engines"]}
    assert done["state"] == "SUCCEEDED", engines
    assert all(state == "SUCCEEDED" for state in engines.values()), engines
    assert done["summary"]["findings"] > 0
    active_db = (data / "trivy-cache").resolve()  # the copy baked into the image, used directly
    assert active_db == (REPO / ".local" / "engines" / "trivy-cache").resolve()
    if (data / "temporal" / "temporal.db").is_file():  # standard profile
        assert any((data / "artifacts").rglob("*"))  # artifacts on the data disk
    else:  # lite profile: no Temporal server, artifacts stored in PostgreSQL
        assert not any((data / "artifacts").rglob("*"))


def test_ci_smoke_script_passes_against_the_hosted_entrypoint(
    hosted_process: tuple[httpx.Client, str, Path],
) -> None:
    client, token, _ = hosted_process
    result = subprocess.run(  # noqa: S603 - repository script with fixed arguments
        [
            sys.executable,
            str(REPO / "deploy" / "smoke_test.py"),
            str(client.base_url),
            token,
            "https://app.example.test",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        env={**os.environ, "SMOKE_REQUIRE_TRIVY": "1"},
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert '"demo_sample_review": {"sample": "SUCCEEDED"' in result.stdout, result.stdout
    assert "smoke test passed" in result.stdout


def test_lite_profile_resumes_a_scan_after_the_process_is_killed(
    pg_cluster: PostgresCluster, tmp_path: Path
) -> None:
    """Free hosts restart or put the instance to sleep at any time; unfinished scans resume."""
    hosted = _prepare(pg_cluster, tmp_path, "lite")
    try:
        hosted.start()
        client = hosted.client
        project, snapshot = _upload_fixture(client, tmp_path)
        scan = client.post(
            f"/v1/projects/{project['id']}/scans", json={"snapshot_id": snapshot["snapshot_id"]}
        ).json()
        external = {"pmd", "eslint", "opengrep", "trivy"}
        deadline = time.monotonic() + 120
        while True:
            current = client.get(f"/v1/scans/{scan['id']}").json()
            assert current["state"] not in TERMINAL, "scan finished before it was interrupted"
            if any(e["engine"] in external and e["state"] == "RUNNING" for e in current["engines"]):
                break
            assert time.monotonic() < deadline, current
            time.sleep(0.1)
        hosted.stop(signal.SIGKILL)  # no shutdown handling, like a crash or an instance reset
        hosted.start()
        done = _wait(client, f"/v1/scans/{scan['id']}", TERMINAL)
        engines = {e["engine"]: e["state"] for e in done["engines"]}
        assert done["state"] == "SUCCEEDED", engines
        assert all(state == "SUCCEEDED" for state in engines.values()), engines
        assert done["summary"]["findings"] > 0
        assert "resumed unfinished work" in hosted.log_path.read_text()
    finally:
        hosted.client.close()
        hosted.stop()
