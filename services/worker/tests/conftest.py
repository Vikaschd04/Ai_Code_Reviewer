"""Shared live-stack fixtures for worker integration tests: real uvicorn API, Temporal dev
server, worker, PostgreSQL and artifact store (no mocks)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

from crp_analysis.engines.base import EngineAdapter
from crp_api.app import create_app
from crp_core.artifacts import FilesystemArtifactStore
from crp_core.config import Settings
from crp_core.db.identity import ensure_local_identity
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.local_secrets import read_secret_file
from crp_core.workflows.temporal import connect_temporal
from crp_devtools.engines import pmd_home
from crp_devtools.infra import TemporalDevServer, free_port
from crp_worker.runtime import build_worker

REPO = Path(__file__).resolve().parents[3]
TERMINAL_SCAN = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "BLOCKED", "BUDGET_EXHAUSTED"}


class LiveServer:
    """Run the real ASGI app with uvicorn on a free loopback port in a background thread."""

    def __init__(self, settings: Settings) -> None:
        self.port = free_port()
        config = uvicorn.Config(
            create_app(settings),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("API did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@dataclass
class Stack:
    settings: Settings
    server: LiveServer
    client: httpx.AsyncClient
    token: str
    workspace_id: str

    async def ok(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self.client.request(method, path, **kwargs)
        assert response.is_success, (response.status_code, response.text)
        return response.json() if response.content else None

    async def project(self, name: str = "Pipeline") -> str:
        body = await self.ok(
            "POST", "/v1/projects", json={"workspace_id": self.workspace_id, "name": name}
        )
        return str(body["id"])

    async def zip_intake(self, project_id: str, data: bytes) -> dict[str, Any]:
        intake = await self.ok(
            "POST",
            f"/v1/projects/{project_id}/intakes",
            json={"mode": "zip_upload", "display_name": "upload.zip"},
        )
        await self.ok(
            "PUT",
            f"/v1/intakes/{intake['id']}/content",
            content=data,
            headers={"Content-Type": "application/zip"},
        )
        first = await self.ok("POST", f"/v1/intakes/{intake['id']}/finalize")
        second = await self.ok("POST", f"/v1/intakes/{intake['id']}/finalize")
        assert first["id"] == second["id"]
        return await self.wait_intake(intake["id"])

    async def wait_intake(self, intake_id: str) -> dict[str, Any]:
        for _ in range(300):
            intake = await self.ok("GET", f"/v1/intakes/{intake_id}")
            if intake["state"] in {"READY", "REJECTED", "FAILED", "CANCELED"}:
                return dict(intake)
            await asyncio.sleep(0.2)
        raise AssertionError("intake did not finish")

    async def scan(
        self, project_id: str, snapshot_id: str, key: str | None = None, cache_mode: str = "use"
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": key} if key else {}
        return dict(
            await self.ok(
                "POST",
                f"/v1/projects/{project_id}/scans",
                json={"snapshot_id": snapshot_id, "cache_mode": cache_mode},
                headers=headers,
            )
        )

    async def scan_and_wait(
        self, project_id: str, snapshot_id: str, cache_mode: str = "use"
    ) -> dict[str, Any]:
        started = await self.scan(project_id, snapshot_id, cache_mode=cache_mode)
        return await self.wait_scan(started["id"])

    async def wait_scan(self, scan_id: str, limit_seconds: float = 180) -> dict[str, Any]:
        deadline = time.monotonic() + limit_seconds
        while time.monotonic() < deadline:
            scan = await self.ok("GET", f"/v1/scans/{scan_id}")
            if scan["state"] in TERMINAL_SCAN:
                return dict(scan)
            await asyncio.sleep(0.3)
        raise AssertionError("scan did not finish")

    async def findings(self, scan_id: str) -> list[dict[str, Any]]:
        page = await self.ok("GET", f"/v1/scans/{scan_id}/findings?limit=200")
        assert page["next_cursor"] is None
        return list(page["items"])


@pytest.fixture
def pmd_dir() -> Path:
    home = pmd_home(REPO / ".local" / "engines")
    if not (home / "bin" / "pmd").is_file():
        pytest.fail("PMD is not installed; run `make engines`")
    return home


@pytest.fixture
def settings(
    make_settings: Callable[..., Settings],
    database_url: str,
    temporal_server: TemporalDevServer,
    pmd_dir: Path,
    tmp_path: Path,
) -> Settings:
    return make_settings(
        database_url=database_url,
        temporal_address=temporal_server.address,
        pmd_home=pmd_dir,
        eslint_runner_dir=REPO / "engines" / "eslint-runner",
        opengrep_home=REPO / ".local" / "engines" / "opengrep-1.30.0",
        trivy_home=REPO / ".local" / "engines" / "trivy-0.69.3",
        trivy_cache_dir=REPO / ".local" / "engines" / "trivy-cache",
        work_root=tmp_path / "work",
        engine_timeout_seconds=120,
        pmd_java_heap="512m",
    )


@contextlib.asynccontextmanager
async def running_stack(
    settings: Settings, adapters: dict[str, EngineAdapter] | None = None
) -> AsyncIterator[Stack]:
    engine = create_engine_from_settings(settings)
    async with transaction(create_session_factory(engine)) as session:
        identity = await ensure_local_identity(session)
    client = await connect_temporal(settings)
    store = FilesystemArtifactStore(
        settings.artifact_root, max_object_bytes=settings.artifact_max_object_bytes
    )
    worker = build_worker(
        client,
        settings,
        store=store,
        engine=engine,
        identity="crp-worker@pytest",
        adapters=adapters,
    )
    server = LiveServer(settings)
    await asyncio.to_thread(server.start)
    assert settings.local_token_file is not None
    token = read_secret_file(settings.local_token_file)
    try:
        async with (
            worker,
            httpx.AsyncClient(
                base_url=server.url, headers={"Authorization": f"Bearer {token}"}, timeout=30
            ) as http,
        ):
            yield Stack(settings, server, http, token, str(identity.workspace_id))
    finally:
        await asyncio.to_thread(server.stop)
        await engine.dispose()


@pytest.fixture
def stack_factory() -> Callable[..., contextlib.AbstractAsyncContextManager[Stack]]:
    return running_stack
