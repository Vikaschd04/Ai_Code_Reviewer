"""API test harness: the real app against a real (cloned, migrated) PostgreSQL database.

The workflow gateway can be swapped for a test double that simulates an unavailable workflow
service; real Temporal execution is covered by services/worker/tests/test_workflow_smoke.py.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from crp_api.app import create_app
from crp_core.config import Settings
from crp_core.db.identity import LocalIdentity, ensure_local_identity
from crp_core.db.session import create_engine_from_settings, create_session_factory, transaction
from crp_core.local_secrets import read_secret_file
from crp_core.workflows.contracts import DiagnosticWorkflowInput
from crp_core.workflows.gateway import (
    DiagnosticRun,
    WorkerPollerStatus,
    WorkflowGateway,
    WorkflowServiceStatus,
    WorkflowUnavailableError,
)

BASE_URL = "http://127.0.0.1:8710"
WEB_ORIGIN = "http://127.0.0.1:5173"


class UnavailableWorkflowGateway:
    """Test double: behaves like an unreachable Temporal service."""

    async def describe_service(self) -> WorkflowServiceStatus:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def describe_workers(self) -> WorkerPollerStatus:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def start_diagnostic(self, payload: DiagnosticWorkflowInput) -> str:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def get_diagnostic(self, workflow_id: str) -> DiagnosticRun:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def start_intake(self, intake_id: UUID) -> str:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def start_scan(self, scan_id: UUID) -> str:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def cancel_scan(self, scan_id: UUID) -> None:
        raise WorkflowUnavailableError("Temporal service at 127.0.0.1:1 is unreachable")

    async def close(self) -> None:
        return None


@dataclass
class ApiHarness:
    app: FastAPI
    client: httpx.AsyncClient
    token: str
    settings: Settings
    identity: LocalIdentity | None

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def provision_identity(settings: Settings) -> LocalIdentity:
    engine = create_engine_from_settings(settings)
    try:
        async with transaction(create_session_factory(engine)) as session:
            return await ensure_local_identity(session)
    finally:
        await engine.dispose()


ApiFactory = Callable[..., Awaitable[ApiHarness]]


@pytest.fixture
async def api_factory(
    make_settings: Callable[..., Settings], database_url: str
) -> AsyncIterator[ApiFactory]:
    stack = contextlib.AsyncExitStack()

    async def factory(
        *,
        gateway: WorkflowGateway | None = None,
        provision: bool = True,
        client_addr: tuple[str, int] = ("127.0.0.1", 50000),
        base_url: str = BASE_URL,
        **overrides: Any,
    ) -> ApiHarness:
        settings = make_settings(**{"database_url": database_url, **overrides})
        identity = await provision_identity(settings) if provision else None
        app = create_app(settings, workflow_gateway=gateway or UnavailableWorkflowGateway())
        await stack.enter_async_context(app.router.lifespan_context(app))
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=client_addr), base_url=base_url
            )
        )
        assert settings.local_token_file is not None
        token = read_secret_file(settings.local_token_file)
        return ApiHarness(app, client, token, settings, identity)

    try:
        yield factory
    finally:
        await stack.aclose()


@pytest.fixture
async def api(api_factory: ApiFactory) -> ApiHarness:
    return await api_factory()
