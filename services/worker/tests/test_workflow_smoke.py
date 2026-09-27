"""Durable-execution smoke test: real Temporal dev server, real worker, real PostgreSQL, real
filesystem artifact store, exercised through the real API endpoints. No mocks."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest

from crp_api.app import create_app
from crp_core.artifacts import FilesystemArtifactStore
from crp_core.config import Settings
from crp_core.db.identity import ensure_local_identity
from crp_core.db.migrate import head_revision
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.local_secrets import read_secret_file
from crp_core.workflows.temporal import connect_temporal
from crp_devtools.infra import TemporalDevServer
from crp_worker.runtime import build_worker

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(
    make_settings: Callable[..., Settings], database_url: str, temporal_server: TemporalDevServer
) -> Settings:
    return make_settings(database_url=database_url, temporal_address=temporal_server.address)


@pytest.fixture
async def api(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    engine = create_engine_from_settings(settings)
    async with transaction(create_session_factory(engine)) as session:
        await ensure_local_identity(session)
    await engine.dispose()
    app = create_app(settings)
    assert settings.local_token_file is not None
    token = read_secret_file(settings.local_token_file)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8710",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        yield client


@contextlib.asynccontextmanager
async def running_worker(settings: Settings) -> AsyncIterator[str]:
    client = await connect_temporal(settings)
    engine = create_engine_from_settings(settings)
    store = FilesystemArtifactStore(settings.artifact_root, max_object_bytes=1024 * 1024)
    identity = "crp-worker@pytest"
    worker = build_worker(client, settings, store=store, engine=engine, identity=identity)
    try:
        async with worker:
            yield identity
    finally:
        await engine.dispose()


async def _readiness(api: httpx.AsyncClient) -> dict[str, dict[str, object]]:
    body = (await api.get("/v1/health/ready")).json()
    return {check["name"]: check for check in body["checks"]}


async def test_readiness_reports_missing_worker_honestly(api: httpx.AsyncClient) -> None:
    checks = await _readiness(api)
    assert checks["workflow_service"]["status"] == "ok"
    assert checks["workflow_worker"]["status"] == "unavailable"
    assert checks["workflow_worker"]["error_code"] == "no_active_worker"


async def test_diagnostic_workflow_executes_on_real_worker(
    api: httpx.AsyncClient, settings: Settings
) -> None:
    async with running_worker(settings) as identity:
        for _ in range(50):  # pollers register asynchronously after the worker starts
            checks = await _readiness(api)
            if checks["workflow_worker"]["status"] == "ok":
                break
            await asyncio.sleep(0.2)
        assert all(check["status"] == "ok" for check in checks.values()), checks
        assert (await api.get("/v1/health/ready")).status_code == 200

        started = await api.post("/v1/diagnostics/workflow-runs")
        assert started.status_code == 202
        workflow_id = started.json()["workflow_id"]

        for _ in range(100):
            run = (await api.get(f"/v1/diagnostics/workflow-runs/{workflow_id}")).json()
            if run["status"] != "RUNNING":
                break
            await asyncio.sleep(0.1)

    assert run["status"] == "COMPLETED", run
    result = run["result"]
    assert result["worker_identity"] == identity
    assert result["artifact"]["read_back_verified"] is True
    assert result["artifact"]["deleted"] is True
    assert len(result["artifact"]["sha256"]) == 64
    assert result["database"]["schema_current"] is True
    assert result["database"]["schema_revision"] == head_revision()
    probe_dir = Path(settings.artifact_root) / "diagnostics"
    assert not any(p.is_file() for p in probe_dir.rglob("*")), "probe artifact left behind"


async def test_unknown_and_malformed_workflow_ids_are_not_found(api: httpx.AsyncClient) -> None:
    unknown = await api.get(f"/v1/diagnostics/workflow-runs/crp-diagnostic-{'0' * 32}")
    assert unknown.status_code == 404
    arbitrary = await api.get("/v1/diagnostics/workflow-runs/some-other-workflow")
    assert arbitrary.status_code == 404
    assert arbitrary.json()["code"] == "workflow_run_not_found"
