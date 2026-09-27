"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from crp_api import __version__
from crp_api.auth.local_token import LocalTokenProvider
from crp_api.auth.throttle import FailedAttemptThrottle
from crp_api.container import AppContainer
from crp_api.errors import install_error_handlers
from crp_api.middleware import LoopbackOnlyMiddleware, RequestContextMiddleware
from crp_api.routes import (
    auth,
    diagnostics,
    graph,
    health,
    intakes,
    issues,
    projects,
    reports,
    scans,
    snapshots,
)
from crp_core.artifacts import ArtifactStore, create_artifact_store
from crp_core.config import AuthMode, Settings
from crp_core.db.session import create_engine_from_settings, create_session_factory
from crp_core.local_secrets import read_secret_file
from crp_core.workflows.gateway import WorkflowGateway
from crp_core.workflows.temporal import TemporalWorkflowGateway

API_PREFIX = "/v1"


def create_app(
    settings: Settings,
    *,
    workflow_gateway: WorkflowGateway | None = None,
    artifact_store: ArtifactStore | None = None,
) -> FastAPI:
    """Build the API. Optional collaborators allow tests to simulate dependency failures."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.local_token_file is None:  # guaranteed by Settings validation in local mode
            raise RuntimeError("local token file is not configured")
        engine = create_engine_from_settings(settings)
        workflows = workflow_gateway or TemporalWorkflowGateway(settings)
        app.state.container = AppContainer(
            settings=settings,
            engine=engine,
            session_factory=create_session_factory(engine),
            workflows=workflows,
            artifacts=artifact_store or create_artifact_store(settings),
            identity=LocalTokenProvider(
                read_secret_file(settings.local_token_file), settings.session_ttl_seconds
            ),
            login_throttle=FailedAttemptThrottle(),
        )
        try:
            yield
        finally:
            await workflows.close()
            await engine.dispose()

    app = FastAPI(
        title="Code Review Platform API",
        version=__version__,
        description=(
            "Control-plane API: local authentication, readiness, projects, ZIP/local-runner "
            "intake, frozen snapshots, baseline PMD/ESLint scans with coverage, findings and "
            "exact source evidence. Source-only analysis; no AI provider is used."
        ),
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    install_error_handlers(app)
    for router in (
        health.router,
        auth.router,
        projects.router,
        intakes.router,
        snapshots.router,
        scans.router,
        issues.router,
        reports.router,
        graph.router,
        diagnostics.router,
    ):
        app.include_router(router, prefix=API_PREFIX)
    if settings.auth_mode is AuthMode.LOCAL_TOKEN:
        app.add_middleware(LoopbackOnlyMiddleware)
    app.add_middleware(RequestContextMiddleware)  # outermost: request IDs on every response
    return app
